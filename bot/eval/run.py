"""Eval pipeline runner.

  uv run python -m eval.run check                 # coverage gate + provider diff (no API calls)
  uv run python -m eval.corpus                    # build the audio corpus once (local Piper voices, cached)
  uv run python -m eval.run stt  [--quick]        # STT + turn detection: Whisper + Silero settings
  uv run python -m eval.run conv [--quick]        # LLM settings over simulated customers
  uv run python -m eval.run all  [--quick] [--apply]

Each run writes eval/runs/<time>/: report.md, recommended.json and per-config rows.
--apply saves the recommended settings to bot/config.json (the /voice page), from the next call.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agent import config as call_config

from . import params

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"


def defaults() -> dict:
    return {f["key"]: f["default"] for f in call_config.FIELDS}


def normalise(cfg: dict) -> dict:
    return dict(cfg)  # both local STT engines use the same Silero turn detection


def matches(cfg: dict, when: dict | None) -> bool:
    return all(cfg.get(k) == v for k, v in (when or {}).items())


def score(summary: dict, weights: dict, gates: dict) -> float | None:
    if "skipped" in summary:
        return None
    if summary.get("clips") and summary.get("errors", 0) / summary["clips"] > gates.get("errors_rate", 1):
        return None
    for g, lim in gates.items():
        if g != "errors_rate" and summary.get(g) is not None and summary[g] > lim:
            return None
    return round(sum(w * (summary.get(k) or 0) for k, w in weights.items()), 2)


class Stage:
    def __init__(self, name: str, spec: dict, base: dict, out: Path, quick: bool, concurrency: int):
        self.name, self.spec, self.base, self.out = name, spec, base, out
        self.quick, self.concurrency = quick, concurrency
        self.runs: list[dict] = []
        self._memo: dict[str, dict] = {}
        self._rows: dict[str, list[dict]] = {}
        if spec["bench"] == "stt":
            from . import corpus

            self.clips = corpus.load(limit=24 if quick else None, conditions=spec.get("conditions"))
        else:
            from . import conv_bench

            only = ["happy", "alt_day", "dnc", "price_probe"] if quick else None
            self.personas, self.must_not = conv_bench.load_personas(only)

    async def evaluate(self, label: str, cfg: dict) -> dict:
        cfg = normalise(cfg)
        key = hashlib.sha1(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]
        if key in self._memo:
            r = {**self._memo[key], "label": label, "cached": True}
            self.runs.append(r)
            return r
        t = time.monotonic()
        print(f"  [{self.name}] {label} …", flush=True)
        if self.spec["bench"] == "stt":
            from . import stt_bench

            summary, rows = await stt_bench.run(cfg, self.clips, self.concurrency)
        else:
            from . import conv_bench

            summary, rows = await conv_bench.run(cfg, self.personas, self.must_not,
                                                 repeats=1 if self.quick else self.spec.get("repeats", 2),
                                                 concurrency=self.concurrency)
        s = score(summary, self.spec["score"], self.spec.get("gates", {}))
        r = {"label": label, "key": key, "cfg": cfg, "summary": summary, "score": s,
             "secs": round(time.monotonic() - t, 1)}
        self._memo[key] = r
        self._rows[key] = rows
        (self.out / f"{self.name}.{key}.jsonl").write_text(
            "\n".join(json.dumps({"label": label, **row}, ensure_ascii=False, default=str) for row in rows))
        print(f"      score={s} {_brief(summary)} ({r['secs']}s)", flush=True)
        self.runs.append(r)
        return r

    async def run(self) -> dict:
        min_gain = self.spec.get("min_gain", 0)
        arm_results = {}
        for arm, arm_cfg in self.spec["arms"].items():
            base_cfg = {**self.base, **arm_cfg}
            base = await self.evaluate(f"{arm} (baseline)", base_cfg)
            if base["score"] is None:
                print(f"      arm {arm} skipped: {base['summary'].get('skipped', 'failed a gate')}")
                arm_results[arm] = {"base": base, "winners": {}, "final": base}
                continue
            winners = {}
            for k, sw in self.spec.get("sweep", {}).items():
                vals, when = (sw["values"], sw.get("when")) if isinstance(sw, dict) else (sw, None)
                if not matches(normalise(base_cfg), when):
                    continue
                best = None
                for v in vals:
                    if v == base_cfg.get(k):
                        continue
                    r = await self.evaluate(f"{arm} {k}={v}", {**base_cfg, k: v})
                    if r["score"] is not None and (best is None or r["score"] > best[1]["score"]):
                        best = (v, r)
                if best and best[1]["score"] - base["score"] > min_gain:
                    winners[k] = {"value": best[0], "gain": round(best[1]["score"] - base["score"], 2)}
            final = base
            if winners:
                combo = await self.evaluate(f"{arm} combined", {**base_cfg, **{k: w["value"] for k, w in winners.items()}})
                best_single = max(w["gain"] for w in winners.values()) + base["score"]
                if combo["score"] is not None and combo["score"] >= best_single - min_gain:
                    final = combo
                else:  # interactions: add winners greedily by gain, keep only what helps
                    cur_cfg, cur = dict(base_cfg), base
                    for k, w in sorted(winners.items(), key=lambda kv: -kv[1]["gain"]):
                        r = await self.evaluate(f"{arm} greedy +{k}={w['value']}", {**cur_cfg, k: w["value"]})
                        if r["score"] is not None and r["score"] > cur["score"] + min_gain:
                            cur_cfg, cur = {**cur_cfg, k: w["value"]}, r
                    final = cur
            arm_results[arm] = {"base": base, "winners": winners, "final": final}
        scored = {a: r for a, r in arm_results.items() if r["final"]["score"] is not None}
        best_arm = max(scored, key=lambda a: scored[a]["final"]["score"]) if scored else None
        return {"arms": arm_results, "best_arm": best_arm,
                "best_cfg": arm_results[best_arm]["final"]["cfg"] if best_arm else None,
                "never_fixed": self.never_fixed()}

    def never_fixed(self) -> list[dict]:
        """Cases that failed under every config that ran: no existing knob fixes them."""
        stats: dict[str, dict] = {}
        for key, rows in self._rows.items():
            for row in rows:
                if self.spec["bench"] == "stt":
                    if row["kind"] == "noise":
                        cid, failed, why = row["id"], row["false_start"], f"heard: {row['hyp'][:60]}"
                    else:
                        cid, failed = row["id"], bool(row["ent_misses"]) or row["empty"]
                        why = f"missed {row['ent_misses']} | heard: {row['hyp'][:60]}"
                else:
                    cid, failed, why = row["persona"], not row["pass"], f"{row['failure']}: got {row.get('outcome')}"
                s = stats.setdefault(cid, {"case": cid, "runs": 0, "fails": 0, "example": why})
                s["runs"] += 1
                s["fails"] += failed
                if failed:
                    s["example"] = why
        return sorted((s for s in stats.values() if s["runs"] and s["fails"] == s["runs"]), key=lambda s: s["case"])


def _brief(s: dict) -> str:
    if "skipped" in s:
        return f"SKIPPED: {s['skipped']}"
    keys = ["entity_acc", "cer", "latency_p50", "false_start_rate", "split_rate", "errors",
            "pass_rate", "compliance_fails", "llm_p50_ms", "llm_cost_inr"]
    return " ".join(f"{k}={s[k]}" for k in keys if k in s)


def report(plan: dict, results: dict, cov: dict, prov: dict, out: Path, recommended: dict, barge: str | None) -> str:
    L = [f"# Eval run {out.name}", ""]
    d = defaults()
    L += ["## Recommended settings", "", "| Setting | Default | Recommended |", "|---|---|---|"]
    for k, v in recommended.items():
        L.append(f"| {k} | {d.get(k)} | **{v}** |")
    if barge:
        L += ["", f"Barge-in: {barge}"]
    for name, res in results.items():
        st = plan["stages"][name]
        L += ["", f"## Stage: {name}", "", f"Winning arm: **{res['best_arm']}**", ""]
        for arm, a in res["arms"].items():
            fin = a["final"]
            L.append(f"- **{arm}**: baseline {a['base']['score']} -> final {fin['score']} "
                     f"({fin['label']}); winners: " + (", ".join(f"{k}={w['value']} (+{w['gain']})"
                                                               for k, w in a["winners"].items()) or "none"))
        L += ["", "| Run | Score | Metrics |", "|---|---|---|"]
        for r in sorted(STAGES[name].runs, key=lambda r: -(r["score"] if r["score"] is not None else -1e9)):
            L.append(f"| {r['label']}{' (cached)' if r.get('cached') else ''} | {r['score']} | {_brief(r['summary'])} |")
        by_cond = [(r["label"], r["summary"].get("by_condition")) for r in STAGES[name].runs if r["summary"].get("by_condition")]
        if by_cond and res["best_arm"]:
            bc = res["arms"][res["best_arm"]]["final"]["summary"].get("by_condition", {})
            L += ["", "Winner by condition: " + ", ".join(f"{c}: acc {m['entity_acc']} cer {m['cer']}" for c, m in bc.items())]
        nf = res["never_fixed"]
        L += ["", f"### Failed under every config ({len(nf)}): candidates for a missing parameter or a code/prompt fix", ""]
        L += [f"- `{s['case']}` ({s['runs']} runs): {s['example']}" for s in nf] or ["- none"]
        _ = st
    L += ["", "## Who decides what's missing", "",
          "1. **Coverage gate**: every /voice setting is swept or pinned with a reason.",
          *(f"   - ERROR {e}" for e in cov["errors"]), *(f"   - warning: {w}" for w in cov["warnings"]),
          f"2. **Provider parameters the bot never sets, not yet reviewed** ({len(prov['new'])}). "
          "Decide each in plan.yaml `provider_review`:",
          *(f"   - `{p['param']}`" for p in prov["new"]),
          f"   Hard-coded in agent/bot.py, review whether to expose: "
          + ", ".join(f"`{p['param']}={p['value']}`" for p in prov["hardcoded"]),
          "3. **Failures no setting fixed**: listed per stage above. Each is either a missing parameter "
          "(expose it, add to plan.yaml) or a prompt/flow/code change."]
    text = "\n".join(L) + "\n"
    (out / "report.md").write_text(text)
    return text


STAGES: dict[str, Stage] = {}


async def main_async(a) -> int:
    plan = yaml.safe_load((ROOT / "plan.yaml").read_text())
    cov = params.coverage(plan)
    prov = params.unwired(plan.get("provider_review") or {})
    if a.cmd == "check" or cov["errors"]:
        print(f"coverage: {len(cov['swept'])} swept, {len(cov['pinned'])} pinned")
        for e in cov["errors"]:
            print(f"  ERROR   {e}")
        for w in cov["warnings"]:
            print(f"  warning {w}")
        print(f"hard-coded in agent/bot.py (not tunable): {len(prov['hardcoded'])}")
        for p in prov["hardcoded"]:
            print(f"  FIXED   {p['param']} = {p['value']}")
        print(f"provider params never set by the bot: {len(prov['new'])} unreviewed, {len(prov['reviewed'])} reviewed")
        for p in prov["new"]:
            print(f"  NEW     {p['param']}")
        if cov["errors"]:
            print("fix the errors above before running a sweep")
            return 1
        return 2 if a.strict and prov["new"] else 0

    out = RUNS / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    base = {**defaults(), **(plan.get("baseline") or {})}
    stages = ["stt", "conv"] if a.cmd == "all" else [a.cmd]
    results, recommended = {}, {}
    for name in stages:
        STAGES[name] = Stage(name, plan["stages"][name], base, out, a.quick, a.concurrency)
        res = await STAGES[name].run()
        results[name] = res
        if res["best_cfg"]:
            changed = {k: v for k, v in res["best_cfg"].items() if v != defaults().get(k)}
            recommended.update(changed)
            base = {**base, **res["best_cfg"]}  # carry the winner into the next stage

    barge = None
    if "stt" in results and results["stt"]["best_arm"]:
        fsr = results["stt"]["arms"][results["stt"]["best_arm"]]["final"]["summary"].get("false_start_rate")
        if fsr is not None:
            on = fsr <= 0.1
            if on != defaults()["barge_in"]:
                recommended["barge_in"] = on
            barge = f"{'on' if on else 'off'} (false_start_rate {fsr} vs limit 0.1)"
    (out / "recommended.json").write_text(json.dumps(recommended, indent=2, ensure_ascii=False))
    (out / "results.json").write_text(json.dumps({n: {**r, "runs": STAGES[n].runs} for n, r in results.items()},
                                                 indent=1, ensure_ascii=False, default=str))
    text = report(plan, results, cov, prov, out, recommended, barge)
    print("\n" + text)
    print(f"written to {out}")
    if a.apply and recommended:
        call_config.save(recommended)
        print(f"applied to {call_config.CONFIG_FILE} (from the next call)")
    return 0


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "stt", "conv", "all"])
    ap.add_argument("--quick", action="store_true", help="24 clips, 4 personas x 1: a smoke run")
    ap.add_argument("--apply", action="store_true", help="save the recommendation to bot/config.json")
    ap.add_argument("--strict", action="store_true", help="check: also fail on unreviewed provider params")
    ap.add_argument("--concurrency", type=int, default=4)
    a = ap.parse_args()
    sys.exit(asyncio.run(main_async(a)))


if __name__ == "__main__":
    main()
