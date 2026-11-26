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
            cl = s.get("checklist")
            if cl is not None and not isinstance(self.policies.get(cl), list):
                self.err(f"{name}: checklist '{cl}' is not a list in policies.yaml")
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

        self._check_sections()
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

    def _check_sections(self):
        secs = self.flow.get("sections")
        if not secs:
            self.warn("no sections in flow.yaml; README gets no diagrams")
            return
        seen = {}
        for key, sec in secs.items():
            if not sec.get("title"):
                self.err(f"section {key}: needs a title")
            for st in sec.get("states", []):
                if st not in self.states:
                    self.err(f"section {key}: '{st}' is not a state")
                elif st in seen:
                    self.err(f"state {st} is in sections {seen[st]} and {key}")
                seen[st] = key
        for st in self.states:
            if st not in seen:
                self.err(f"state {st} is not in any section")
            if not self.states[st].get("title"):
                self.warn(f"state {st} has no title (diagram shows its id)")
        labels = self.flow.get("labels") or {}
        for ev in self.events:
            if ev not in labels:
                self.warn(f"event '{ev}' has no label (diagram shows its id)")
        for name, i in {**self.global_intents, **self.local_intents}.items():
            if not (i or {}).get("label"):
                self.warn(f"intent '{name}' has no label (diagram shows its id)")

    def _check_intents(self):
        for name, gi in self.global_intents.items():
            goto = gi.get("goto")
            if goto != "$stay" and goto not in self.states:
                self.err(f"intent {name}: goto '{goto}' is not a state")
            if gi.get("escalate_to") and gi["escalate_to"] not in self.states:
                self.err(f"intent {name}: escalate_to '{gi['escalate_to']}' is not a state")
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
        # readiness items need an ask_<id> line (and arrange_<id> when ask_if_can_arrange is on)
        for item in self.policies.get("readiness_items", []):
            keys = [f"ask_{item['id']}"] + ([f"arrange_{item['id']}"] if item.get("ask_if_can_arrange") else [])
            for lang, p in self.prompts.items():
                for k in keys:
                    if k not in ((p or {}).get("READINESS_CHECK") or {}):
                        self.err(f"prompts/{lang}.yaml: READINESS_CHECK.{k} missing for readiness item")
        # prefix_if: its prompt key must exist in every language
        for n, st in self.states.items():
            pre = st.get("prefix_if")
            if pre:
                for lang, p in self.prompts.items():
                    if pre.get("key") not in ((p or {}).get(n) or {}):
                        self.err(f"prompts/{lang}.yaml: {n}.{pre.get('key')} missing (prefix_if)")
        # time_line: the alternative prompt key must exist in every language
        for n, st in self.states.items():
            key = st.get("time_line")
            if not key:
                continue
            for lang, p in self.prompts.items():
                if key not in ((p or {}).get(n) or {}):
                    self.err(f"prompts/{lang}.yaml: {n}.{key} missing (time_line)")
        if not any((i or {}).get("shares_time") for i in {**self.global_intents, **self.local_intents}.values()):
            if any(st.get("time_line") for st in self.states.values()):
                self.warn("time_line is used but no intent has shares_time: true")

    def _edges(self):
        for n, s in self.states.items():
            for tr in s.get("transitions", []) or []:
                yield n, tr["to"]
            if s.get("type") == "conversation":
                disabled = set(s.get("disable_global", []) or [])
                for gname, gi in self.global_intents.items():
                    if gname not in disabled and gi.get("goto") not in (None, "$stay"):
                        yield n, gi["goto"]
                    if gname not in disabled and gi.get("escalate_to"):
                        yield n, gi["escalate_to"]
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
        streaks = ctx.setdefault("_streaks", {})
        for k in [k for k in streaks if k != on]:
            del streaks[k]
        if s.get("type") == "conversation":
            for ge in self.flow.get("global_events", []) or []:
                if ge["on"] == on:
                    return ge["to"]
            if on in self.global_intents and on not in (s.get("disable_global") or []):
                gi = self.global_intents[on]
                streaks[on] = streaks.get(on, 0) + 1
                if gi.get("escalate_to") and streaks[on] > int(gi.get("escalate_after", 2)):
                    return gi["escalate_to"]
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

    # ---------------------------------------------------------------- diagrams
    # README gets: a 6-box overview, then one small diagram per section (flow.yaml
    # `sections`), with plain-English titles (state `title`) and arrow labels
    # (intent `label` in intents.yaml, `labels` in flow.yaml). Arrows that leave a
    # section end in a "go to section N" box instead of crossing the whole chart.

    def _label(self, on, when=None) -> str:
        intent = {**self.global_intents, **self.local_intents}.get(on) or {}
        labels = self.flow.get("labels") or {}
        text = intent.get("label") or (labels.get(on) if isinstance(labels.get(on), str) else None) or on
        if when is not None:
            flag = str(when).lstrip("!")
            fl = labels.get(flag) if isinstance(labels.get(flag), dict) else {}
            neg = str(when).startswith("!")
            text += f" ({fl.get('when_false' if neg else 'when_true') or ('not ' if neg else '') + flag})"
        return text.replace('"', "'")

    def _section_of(self) -> dict:
        return {st: key for key, sec in (self.flow.get("sections") or {}).items() for st in sec.get("states", [])}

    # The usual call = follow yes / ok answers from the start until the call ends.
    PATH_PREFER = ["context_ready", "affirm", "action_ok", "band_available"]

    def _main_path(self) -> list:
        path, cur, seen = [], self.flow.get("start"), set()
        while cur and cur not in seen:
            seen.add(cur)
            path.append(cur)
            trs = self.states[cur].get("transitions", []) or []
            cur = next((t["to"] for p_ in self.PATH_PREFER for t in trs if t["on"] == p_), None)
        return path

    def _talk_path(self) -> list:
        return [n for n in self._main_path() if self.states[n].get("type") == "conversation"]

    def _detours(self) -> list:
        """Sections as side processes: their talking/system steps that are not on the usual call."""
        on_path = set(self._main_path())
        out = []
        for key, sec in (self.flow.get("sections") or {}).items():
            if key == "anytime":
                continue
            steps = [n for n in sec["states"] if n not in on_path and self.states[n].get("type") != "terminal"]
            if steps:
                out.append({"key": key, "title": sec["title"].partition(". ")[2] or sec["title"],
                            "about": sec.get("about", ""), "steps": steps})
        return out

    def _row(self, n, tr) -> dict:
        """One line of a step's decision table: what the customer says -> what happens."""
        to = tr["to"]
        st = self.states[to]
        title = st.get("title", to)
        talk = self._talk_path()
        where = self._section_of()
        detour_of = {s: d for d in self._detours() for s in d["steps"]}
        if st.get("type") == "terminal":
            kind, text = "end", f"Call ends: {title}"
        elif to == n:
            kind, text = "stay", "Bot answers and asks again"
        elif to in talk:
            kind, text = "path", f"Step {talk.index(to) + 1}: {title}"
        elif to in self._main_path():
            kind, text = "path", f"System: {title}"
        elif to in detour_of and detour_of.get(n, {}).get("key") == detour_of[to]["key"]:
            kind, text = "step", title
        elif to in detour_of:
            kind, text = "detour", f"{detour_of[to]['title']}: {title}"
        else:
            kind, text = "step", title
        return {"label": self._label(tr["on"], tr.get("when")), "to": to, "kind": kind, "text": text,
                "detour": (detour_of.get(to) or {}).get("key"), "section": where.get(to)}

    def _sample_line(self, n, lang="en", key="main") -> str:
        vals = (self.flow.get("sample_values") or {}).get(lang) or {}
        text = (((self.prompts.get(lang) or {}).get(n) or {}).get(key)) or ""
        return re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), text)

    def _anytime_rows(self) -> list:
        rows = []
        for g, gi in self.global_intents.items():
            goto = gi.get("goto")
            if goto == "$stay":
                text = "Bot answers briefly, then repeats its question"
                if gi.get("escalate_to"):
                    text = (f"Bot asks again; after {gi.get('escalate_after')} times in a row: "
                            f"{self.states[gi['escalate_to']].get('title', gi['escalate_to'])}")
            else:
                text = self.states[goto].get("title", goto)
            rows.append({"label": gi.get("label", g), "text": text, "to": None if goto == "$stay" else goto,
                         "stay": goto == "$stay"})
        for ge in self.flow.get("global_events") or []:
            rows.append({"label": self._label(ge["on"]), "text": self.states[ge["to"]].get("title", ge["to"]),
                         "to": ge["to"], "stay": False})
        return rows

    def diagrams_markdown(self) -> str:
        path = self._main_path()
        talk = self._talk_path()
        esc = lambda t: str(t).replace("|", "\\|")  # noqa: E731
        out = [
            "**How to read this:** the call is one straight line of steps. Each step has a small table: what the "
            "customer says, and what the bot does next. Anything off the main line is a short detour, listed "
            "after it. Lines are shown in English with example values; the Hindi lines are in `prompts/hi.yaml`.",
            "",
            "### The usual call",
            "",
            "```mermaid",
            "flowchart LR",
        ]
        nodes = []
        for i, n in enumerate(path):
            s = self.states[n]
            t = s.get("title", n).replace('"', "'")
            if s.get("type") == "terminal":
                nodes.append(f'  P{i}(["{t}"]):::end_')
            elif s.get("type") == "system":
                nodes.append(f'  P{i}["{t}"]:::sys')
            else:
                nodes.append(f'  P{i}["{talk.index(n) + 1}. {t}"]:::talk')
        out += nodes
        out.append("  " + " --> ".join(f"P{i}" for i in range(len(path))))
        out += ["  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41",
                "  classDef sys fill:#FFF4E0,stroke:#C98A1B,color:#4A3200",
                "  classDef end_ fill:#E6F4EA,stroke:#3C8D50,color:#1E4620", "```", ""]

        def step_block(n, heading):
            block = [heading, ""]
            line = self._sample_line(n)
            if line:
                block += [f"> {line}", ""]
            trs = self.states[n].get("transitions", []) or []
            if self.states[n].get("type") == "system":
                block += ["The bot doesn't speak here. " + "; ".join(
                    f"**{r['label']}** → {r['text']}" for r in (self._row(n, t) for t in trs)) + ".", ""]
                return block
            block += ["| Customer says | What happens |", "|---|---|"]
            block += [f"| {esc(r['label'])} | {esc(r['text'])} |" for r in (self._row(n, t) for t in trs)]
            return block + [""]

        for i, n in enumerate(talk):
            out += step_block(n, f"#### Step {i + 1}. {self.states[n].get('title', n)}")
        out += ["### Detours", ""]
        for d in self._detours():
            out += [f"#### {d['title']}", "", d["about"], ""]
            for n in d["steps"]:
                out += step_block(n, f"**{self.states[n].get('title', n)}**")
        out += ["### Any time in the call", "", "| Customer says | What happens |", "|---|---|"]
        out += [f"| {esc(r['label'])} | {esc(r['text'])} |" for r in self._anytime_rows()]
        wp = [n for n in (self.flow.get("sections") or {}).get("anytime", {}).get("states", [])
              if self.states[n].get("type") == "conversation"]
        for n in wp:
            out += [""] + step_block(n, f"**{self.states[n].get('title', n)}**")
        out += ["", "### How a call can end", "", "| Ending | Outcome code |", "|---|---|"]
        for n, s in self.states.items():
            if s.get("type") == "terminal":
                code = s.get("outcome") or " / ".join((s.get("outcome_map") or {}).values())
                out.append(f"| {esc(s.get('title', n))} | `{code}` |")
        post = ", ".join((self.flow.get("post_call") or {}).get("actions", []))
        out += ["", f"After every call, whatever the ending, the system runs: {post}."]
        return "\n".join(out)

    # ---------------------------------------------------------------- shareable HTML
    TEMPLATE = Path(__file__).resolve().parent / "flow_template.html"

    def _allowed(self, n) -> list:
        s = self.states[n]
        if s.get("type") != "conversation":
            return []
        disabled = set(s.get("disable_global") or [])
        return [g for g in self.global_intents if g not in disabled]

    def html_data(self) -> dict:
        secs = self.flow.get("sections") or {}
        where = self._section_of()
        langs = {"hi": "Hindi", "en": "English"}
        intents = {**self.global_intents, **self.local_intents}
        states = {}
        for n, s in self.states.items():
            nxt = []
            for tr in s.get("transitions", []) or []:
                nxt.append({"label": self._label(tr["on"], tr.get("when")), "to": tr["to"], "section": where.get(tr["to"]),
                            "to_title": self.states[tr["to"]].get("title", tr["to"])})
            anytime = []
            for g in self._allowed(n):
                gi = intents[g] or {}
                ex = (gi.get("examples") or {})
                sample = ", ".join(f"“{e}”" for e in (ex.get("hi", [])[:2] + ex.get("en", [])[:1]) if not e.startswith("("))
                anytime.append({"label": gi.get("label", g), "examples": sample})
            acts = []
            for a in s.get("actions", []) or []:
                ad = self.actions.get(a) or {}
                acts.append({"name": a, "status": ad.get("status", ""), "does": ad.get("does", ""), "fallback": ad.get("fallback")})
            lines = {lang: {k: v for k, v in ((p or {}).get(n) or {}).items()} for lang, p in self.prompts.items()}
            outcome = s.get("outcome") or (" / ".join(s.get("outcome_map", {}).values()) if s.get("outcome_map") else None)
            states[n] = {"title": s.get("title", n), "type": s.get("type"), "section": where.get(n), "lines": lines,
                         "next": nxt, "anytime": anytime, "actions": acts, "outcome": outcome}
        outcomes = []
        for n, s in self.states.items():
            vals = [s["outcome"]] if s.get("outcome") else list((s.get("outcome_map") or {}).values())
            for o in vals:
                outcomes.append({"outcome": o, "state": n, "title": s.get("title", n), "section": where.get(n),
                                 "section_title": secs.get(where.get(n), {}).get("title", "")})
        stay = []
        for g, gi in self.global_intents.items():
            if gi.get("goto") == "$stay":
                note = "The bot gives a short answer and repeats its question."
                if gi.get("escalate_to"):
                    note = (f"The bot asks again; after {gi.get('escalate_after')} in a row it goes to "
                            f"“{self.states[gi['escalate_to']].get('title', gi['escalate_to'])}”.")
                stay.append({"label": gi.get("label", g), "note": note})
        samples = self.flow.get("sample_values") or {}

        def fill(text, lang):
            vals = samples.get(lang) or {}
            return re.sub(r"\{(\w+)\}", lambda m: (f"[[{vals[m.group(1)]}]]" if vals[m.group(1)] else "")
                          if m.group(1) in vals else m.group(0), text)

        for n, st in states.items():
            st["say"] = {lang: {k: fill(v, lang) for k, v in keys.items()} for lang, keys in st["lines"].items()}
        glob = {lang: {k: fill(v, lang) for k, v in ((p or {}).get("_global") or {}).items()} for lang, p in self.prompts.items()}

        for n, st in states.items():
            st["rows"] = [self._row(n, t) for t in self.states[n].get("transitions", []) or []]
        path = [{"id": n, "type": self.states[n].get("type"), "title": self.states[n].get("title", n)} for n in self._main_path()]
        anytime_states = [n for n in (secs.get("anytime") or {}).get("states", []) if self.states[n].get("type") == "conversation"]
        title = (self.flow.get("title") or self.dir.name.replace("-", " ").capitalize() + " call flow")
        return {
            "flow": self.dir.name, "title": title, "version": str(self.flow.get("version")),
            "ticket": self.flow.get("ticket", ""),
            "subtitle": self.flow.get("summary", ""),
            "languages": {k: langs.get(k, k) for k in self.prompts},
            "counts": {"states": len(self.states), "outcomes": len(self.outcomes)},
            "path": path, "talk": self._talk_path(), "detours": self._detours(),
            "anytime": self._anytime_rows(), "anytime_states": anytime_states, "global_lines": glob,
            "states": states, "outcomes": outcomes, "stay": stay,
        }

    def html(self) -> str:
        data = json.dumps(self.html_data(), ensure_ascii=False, indent=None).replace("</", "<\\/")
        t = self.TEMPLATE.read_text(encoding="utf-8")
        title = self.flow.get("title") or "Call flow"
        return t.replace("__TITLE__", title).replace("__FLOW__", self.dir.name).replace("__DATA__", data)

    def write_html(self) -> bool:
        out = self.dir / "flow.html"
        new = self.html()
        if out.exists() and out.read_text(encoding="utf-8") == new:
            return False
        out.write_text(new, encoding="utf-8")
        return True

    def html_is_current(self) -> bool:
        out = self.dir / "flow.html"
        return out.exists() and out.read_text(encoding="utf-8") == self.html()

    def write_diagram(self) -> bool:
        readme = self.dir / "README.md"
        block = f"{DIAGRAM_START}\n{self.diagrams_markdown()}\n{DIAGRAM_END}"
        text = readme.read_text(encoding="utf-8") if readme.exists() else ""
        if DIAGRAM_START in text and DIAGRAM_END in text:
            new = re.sub(re.escape(DIAGRAM_START) + r".*?" + re.escape(DIAGRAM_END),
                         lambda _: block, text, flags=re.S)
        else:
            new = text.rstrip() + "\n\n## Flow diagrams\n\n" + block + "\n"
        if new != text:
            readme.write_text(new, encoding="utf-8")
            return True
        return False

    def diagram_is_current(self) -> bool:
        readme = self.dir / "README.md"
        if not readme.exists():
            return False
        return self.diagrams_markdown() in readme.read_text(encoding="utf-8")


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
            if fl.TEMPLATE.exists() and fl.write_html():
                print(f"  updated {folder.name}/flow.html")
        else:
            if not fl.diagram_is_current():
                fl.warn("README.md diagram is out of date; run with --write")
            if fl.TEMPLATE.exists() and not fl.html_is_current():
                fl.warn("flow.html is out of date; run with --write")

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
