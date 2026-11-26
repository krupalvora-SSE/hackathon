#!/usr/bin/env python3
"""Validate call-flow config, run paper-test scenarios, and redraw the flow diagram.

Usage:
    python tools/flowcheck.py                 # check every flows/*/flow.yaml
    python tools/flowcheck.py flows/service-scheduling
    python tools/flowcheck.py --write         # also rewrite the Mermaid diagram in each README.md

Exit code 1 if any error is found. Needs PyYAML (pip install -r tools/requirements.txt).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
STATE_TYPES = {"conversation", "system", "terminal"}
ACTION_STATUSES = {"live", "contract_pending", "stub"}
PREREQ_POLICIES = {"site_on_hold", "reschedule_after_ready_date"}
DIAGRAM_START = "<!-- flow-diagram:start -->"
DIAGRAM_END = "<!-- flow-diagram:end -->"


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps on/off/yes/no as strings (YAML 1.1 turns `on:` into True)."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.load(f, Loader=_Loader)


class Flow:
    def __init__(self, folder: Path):
        self.dir = folder
        self.flow = load_yaml(folder / "flow.yaml")
        self.intents = load_yaml(folder / "intents.yaml")
        self.actions = load_yaml(folder / "actions.yaml")
        self.policies = load_yaml(folder / "policies.yaml")
        self.prompts = {
            lang: load_yaml(folder / "prompts" / f"{lang}.yaml")
            for lang in self.policies.get("languages", [])
            if (folder / "prompts" / f"{lang}.yaml").exists()
        }
        scen = folder / "scenarios.yaml"
        self.scenarios = load_yaml(scen) if scen.exists() else []
        self.states: dict = self.flow["states"]
        self.global_intents: dict = self.intents.get("global", {}) or {}
        self.local_intents: dict = self.intents.get("local", {}) or {}
        self.events = set(self.flow.get("events", []))
        self.flags = set((self.flow.get("flags") or {}).keys())
        self.outcomes = set(self.flow.get("outcomes", []))
        self.errors: list[str] = []
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ checks
    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def check(self):
        f, states = self.flow, self.states
        if f.get("start") not in states:
            self.err(f"start state '{f.get('start')}' is not defined")

        for name, s in states.items():
            t = s.get("type")
            if t not in STATE_TYPES:
                self.err(f"{name}: type '{t}' must be one of {sorted(STATE_TYPES)}")
            for a in s.get("actions", []) or []:
                if a not in self.actions:
                    self.err(f"{name}: action '{a}' is not in actions.yaml")
            for g in s.get("disable_global", []) or []:
                if g not in self.global_intents:
                    self.err(f"{name}: disable_global '{g}' is not a global intent")
            trans = s.get("transitions", []) or []
            if t == "terminal":
                if trans:
                    self.err(f"{name}: terminal states cannot have transitions")
                self._check_terminal_outcome(name, s)
            elif not trans:
                self.err(f"{name}: non-terminal state has no transitions")
            for tr in trans:
                self._check_transition(name, tr)

        for ge in f.get("global_events", []) or []:
            if ge.get("on") not in self.events:
                self.err(f"global_events: '{ge.get('on')}' is not a declared event")
            if ge.get("to") not in states:
                self.err(f"global_events: target '{ge.get('to')}' is not a state")

        for p in (f.get("post_call") or {}).get("actions", []):
            if p not in self.actions:
                self.err(f"post_call: action '{p}' is not in actions.yaml")

        self._check_intents()
        self._check_actions()
        self._check_policies()
        self._check_prompts()
        self._check_reachability()
        self._check_schemas()

    def _check_terminal_outcome(self, name, s):
        if "outcome" in s:
            if s["outcome"] not in self.outcomes:
                self.err(f"{name}: outcome '{s['outcome']}' is not in outcomes")
        elif "outcome_map" in s:
            if "outcome_from" not in s:
                self.err(f"{name}: outcome_map needs outcome_from")
            for v in s["outcome_map"].values():
                if v not in self.outcomes:
                    self.err(f"{name}: outcome_map value '{v}' is not in outcomes")
        else:
            self.err(f"{name}: terminal state needs outcome or outcome_map")

    def _check_transition(self, name, tr):
        on, to = tr.get("on"), tr.get("to")
        if on not in self.events and on not in self.local_intents and on not in self.global_intents:
            self.err(f"{name}: transition on '{on}' is not a declared event or intent")
        if to not in self.states:
            self.err(f"{name}: transition target '{to}' is not a state")
        when = tr.get("when")
        if when is not None and str(when).lstrip("!") not in self.flags:
            self.err(f"{name}: when-flag '{when}' is not declared under flags")
        say = tr.get("say")
        if say and to in self.states:
            for lang, p in self.prompts.items():
                if say not in (p.get(to) or {}):
                    self.err(f"{name} -> {to}: say '{say}' missing in prompts/{lang}.yaml under {to}")

    def _check_intents(self):
        for name, gi in self.global_intents.items():
            goto = gi.get("goto")
            if goto != "$stay" and goto not in self.states:
                self.err(f"intent {name}: goto '{goto}' is not a state")
            if goto == "$stay" and gi.get("say"):
                for lang, p in self.prompts.items():
                    if gi["say"] not in (p.get("_global") or {}):
                        self.err(f"intent {name}: say '{gi['say']}' missing in prompts/{lang}.yaml under _global")
        dup = set(self.global_intents) & set(self.local_intents)
        for d in dup:
            self.err(f"intent '{d}' is both global and local")
        used = {tr.get("on") for s in self.states.values() for tr in s.get("transitions", []) or []}
        for name in self.local_intents:
            if name not in used:
                self.warn(f"local intent '{name}' is not used by any transition")
        for name, i in {**self.global_intents, **self.local_intents}.items():
            ex = (i or {}).get("examples") or {}
            for lang in self.prompts:
                if not ex.get(lang):
                    self.warn(f"intent '{name}' has no {lang} examples")

    def _check_actions(self):
        for name, a in self.actions.items():
            st = a.get("status")
            if st not in ACTION_STATUSES:
                self.err(f"action {name}: status '{st}' must be one of {sorted(ACTION_STATUSES)}")
            if st == "stub" and not a.get("fallback"):
                self.err(f"action {name}: stub actions need a fallback")
            if a.get("output_schema") and not (self.dir / a["output_schema"]).exists():
                self.err(f"action {name}: output_schema '{a['output_schema']}' not found")
        used = set(self.flow.get("post_call", {}).get("actions", []))
        for s in self.states.values():
            used.update(s.get("actions", []) or [])
        for name in self.actions:
            if name not in used:
                self.warn(f"action '{name}' is not used by any state")

    def _check_policies(self):
        p = self.policies
        if p.get("on_missing_prereq") not in PREREQ_POLICIES:
            self.err(f"policies: on_missing_prereq must be one of {sorted(PREREQ_POLICIES)}")
        for lang in p.get("languages", []):
            if lang not in self.prompts:
                self.err(f"policies: language '{lang}' has no prompts/{lang}.yaml")
        if p.get("default_language") not in p.get("languages", []):
            self.err("policies: default_language must be in languages")
        if not p.get("readiness_items"):
            self.err("policies: readiness_items is empty")

    def _check_prompts(self):
        spoken = {
            n for n, s in self.states.items()
            if s.get("type") == "conversation" or (s.get("type") == "terminal" and not s.get("silent"))
        }
        key_sets = {}
        for lang, p in self.prompts.items():
            p = p or {}
            for n in spoken:
                if not (p.get(n) or {}).get("main"):
                    self.err(f"prompts/{lang}.yaml: state {n} has no 'main' line")
            for n in p:
                if n != "_global" and n not in self.states:
                    self.err(f"prompts/{lang}.yaml: '{n}' is not a state")
            key_sets[lang] = {(n, k) for n, v in p.items() for k in (v or {})}
            for n in self.states:
                if n in p and self.states[n].get("type") == "system":
                    self.warn(f"prompts/{lang}.yaml: {n} is a system state; its lines are never spoken")
        langs = list(key_sets)
        for a in langs:
            for b in langs:
                for n, k in sorted(key_sets[a] - key_sets[b]):
                    self.err(f"prompt {n}.{k} exists in {a} but not in {b}")
        # readiness items need an ask_<id> line
        for item in self.policies.get("readiness_items", []):
            for lang, p in self.prompts.items():
                if f"ask_{item['id']}" not in ((p or {}).get("READINESS_CHECK") or {}):
                    self.err(f"prompts/{lang}.yaml: READINESS_CHECK.ask_{item['id']} missing for readiness item")

    def _edges(self):
        for n, s in self.states.items():
            for tr in s.get("transitions", []) or []:
                yield n, tr["to"]
            if s.get("type") == "conversation":
                disabled = set(s.get("disable_global", []) or [])
                for gname, gi in self.global_intents.items():
                    if gname not in disabled and gi.get("goto") not in (None, "$stay"):
                        yield n, gi["goto"]
                for ge in self.flow.get("global_events", []) or []:
                    yield n, ge["to"]

    def _check_reachability(self):
        start = self.flow.get("start")
        if start not in self.states:
            return
        adj: dict[str, set] = {}
        for a, b in self._edges():
            adj.setdefault(a, set()).add(b)
        seen, stack = {start}, [start]
        while stack:
            for nxt in adj.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        for n in self.states:
            if n not in seen:
                self.err(f"{n}: unreachable from {start}")
        reachable_outcomes = set()
        for n in seen:
            s = self.states.get(n, {})
            if "outcome" in s:
                reachable_outcomes.add(s["outcome"])
            reachable_outcomes.update((s.get("outcome_map") or {}).values())
        for o in sorted(self.outcomes - reachable_outcomes):
            self.err(f"outcome '{o}' is declared but no reachable terminal state produces it")

    def _check_schemas(self):
        for path in sorted((self.dir / "schemas").glob("*.json")):
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                self.err(f"{path.name}: invalid JSON ({e})")
                continue
            enum = (((schema.get("properties") or {}).get("outcome")) or {}).get("enum")
            if enum is not None and set(enum) != self.outcomes:
                self.err(f"{path.name}: outcome enum differs from flow outcomes: "
                         f"missing {sorted(self.outcomes - set(enum))}, extra {sorted(set(enum) - self.outcomes)}")

    # -------------------------------------------------------------- simulator
    def simulate(self, steps):
        """Walk steps through the flow. Returns (outcome, path) or raises ValueError."""
        state, ctx, path = self.flow["start"], {}, [self.flow["start"]]
        if state not in self.states:
            raise ValueError(f"start state '{state}' is not defined")
        for i, step in enumerate(steps, 1):
            on, flags = (step, {}) if isinstance(step, str) else (step["on"], step.get("flags", {}))
            s = self.states[state]
            if s.get("type") == "terminal":
                raise ValueError(f"step {i} '{on}': call already ended in {state}")
            nxt = self._next(state, s, on, flags, ctx)
            if nxt is None:
                raise ValueError(f"step {i} '{on}': no transition from {state}")
            if nxt not in self.states:
                raise ValueError(f"step {i} '{on}': {state} goes to undefined state '{nxt}'")
            state = nxt
            path.append(state)
        s = self.states[state]
        if s.get("type") != "terminal":
            raise ValueError(f"steps ended in non-terminal state {state}")
        outcome = s.get("outcome") or s.get("outcome_map", {}).get(ctx.get(s.get("outcome_from")))
        return outcome, path

    def _next(self, name, s, on, flags, ctx):
        if s.get("type") == "conversation":
            for ge in self.flow.get("global_events", []) or []:
                if ge["on"] == on:
                    return ge["to"]
            if on in self.global_intents and on not in (s.get("disable_global") or []):
                gi = self.global_intents[on]
                ctx.update(gi.get("set") or {})
                return name if gi["goto"] == "$stay" else gi["goto"]
        for tr in s.get("transitions", []) or []:
            if tr["on"] != on:
                continue
            when = tr.get("when")
            if when is not None:
                flag = str(when).lstrip("!")
                if flag not in flags:
                    raise ValueError(f"'{on}' in {name} needs flag '{flag}' in the scenario step")
                if bool(flags[flag]) == str(when).startswith("!"):
                    continue
            ctx.update(tr.get("set") or {})
            return tr["to"]
        return None

    def run_scenarios(self):
        results = []
        for sc in self.scenarios or []:
            try:
                outcome, path = self.simulate(sc["steps"])
                ok = outcome == sc["expect"]
                results.append((ok, sc["name"], outcome, path))
                if not ok:
                    self.err(f"scenario '{sc['name']}': expected {sc['expect']}, got {outcome} via {' > '.join(path)}")
            except ValueError as e:
                results.append((False, sc["name"], None, []))
                self.err(f"scenario '{sc['name']}': {e}")
        covered = {o for ok, _, o, _ in results if ok}
        for o in sorted(self.outcomes - covered):
            self.warn(f"no scenario ends in outcome '{o}'")
        return results

    # ---------------------------------------------------------------- diagram
    def mermaid(self) -> str:
        def node(n):
            t = self.states[n].get("type")
            if t == "system":
                return f'{n}[["{n}"]]'
            if t == "terminal":
                s = self.states[n]
                out = s.get("outcome") or "/".join(s.get("outcome_map", {}).values())
                return f'{n}(["{n}<br/><i>{out}</i>"])'
            return f'{n}["{n}"]'

        lines = ["flowchart TD"]
        for n in self.states:
            lines.append(f"  {node(n)}")
        for n, s in self.states.items():
            for tr in s.get("transitions", []) or []:
                label = tr["on"] + (f" [{tr['when']}]" if tr.get("when") else "")
                lines.append(f'  {n} -->|"{label}"| {tr["to"]}')
        lines.append('  ANY{{"any conversation state"}}')
        for gname, gi in self.global_intents.items():
            if gi.get("goto") not in (None, "$stay"):
                lines.append(f'  ANY -.->|"{gname}"| {gi["goto"]}')
        for ge in self.flow.get("global_events", []) or []:
            lines.append(f'  ANY -.->|"{ge["on"]}"| {ge["to"]}')
        post = ", ".join((self.flow.get("post_call") or {}).get("actions", []))
        lines.append(f'  POST_CALL[/"POST_CALL (every call): {post}"/]')
        lines.append("  classDef system fill:#eef,stroke:#88a")
        lines.append("  classDef terminal fill:#efe,stroke:#8a8")
        lines.append("  classDef global fill:#fee,stroke:#a88")
        sys_states = [n for n, s in self.states.items() if s.get("type") == "system"]
        term_states = [n for n, s in self.states.items() if s.get("type") == "terminal"]
        if sys_states:
            lines.append(f"  class {','.join(sys_states)} system")
        if term_states:
            lines.append(f"  class {','.join(term_states)} terminal")
        lines.append("  class ANY,POST_CALL global")
        return "\n".join(lines)

    def write_diagram(self) -> bool:
        readme = self.dir / "README.md"
        block = f"{DIAGRAM_START}\n```mermaid\n{self.mermaid()}\n```\n{DIAGRAM_END}"
        text = readme.read_text(encoding="utf-8") if readme.exists() else ""
        if DIAGRAM_START in text and DIAGRAM_END in text:
            new = re.sub(re.escape(DIAGRAM_START) + r".*?" + re.escape(DIAGRAM_END),
                         lambda _: block, text, flags=re.S)
        else:
            new = text.rstrip() + "\n\n## Flow diagram\n\n" + block + "\n"
        if new != text:
            readme.write_text(new, encoding="utf-8")
            return True
        return False

    def diagram_is_current(self) -> bool:
        readme = self.dir / "README.md"
        if not readme.exists():
            return False
        return f"```mermaid\n{self.mermaid()}\n```" in readme.read_text(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("flows", nargs="*", help="flow folders (default: every flows/*/ with a flow.yaml)")
    ap.add_argument("--write", action="store_true", help="rewrite the Mermaid diagram in README.md")
    args = ap.parse_args()

    folders = [Path(p).resolve() for p in args.flows] or sorted(p.parent for p in (REPO / "flows").glob("*/flow.yaml"))
    if not folders:
        print("no flows found under flows/")
        return 1

    failed = False
    for folder in folders:
        fl = Flow(folder)
        fl.check()
        results = fl.run_scenarios()
        if args.write and not fl.errors:
            if fl.write_diagram():
                print(f"  updated diagram in {folder.name}/README.md")
        elif not fl.diagram_is_current():
            fl.warn("README.md diagram is out of date; run with --write")

        rel = folder.relative_to(REPO) if folder.is_relative_to(REPO) else folder
        version = fl.flow.get("version")
        passed = sum(1 for r in results if r[0])
        print(f"{rel} (v{version}): {len(fl.states)} states, {passed}/{len(results)} scenarios passed")
        for e in fl.errors:
            print(f"  ERROR   {e}")
        for w in fl.warnings:
            print(f"  warning {w}")
        failed |= bool(fl.errors)

    print("FAILED" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
