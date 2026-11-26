"""Who decides which parameters are missing? This file does, three ways:

1. Coverage gate. Every knob in agent/config.py FIELDS must appear in eval/plan.yaml as either
   swept in a stage or pinned with a written reason. A new knob added to the /voice page without
   a test decision fails `python -m eval.run check`. Every option of a swept select must be tried
   or excluded with a reason.
2. Provider diff. Reads the parameters the providers actually accept (pipecat's Sarvam STT/TTS,
   Gemini, Silero VAD, the user-turn aggregator) and reports the ones the bot never sets.
   Each must be reviewed once in plan.yaml `provider_review` (expose / ignore + reason).
   Library upgrades that add parameters show up here automatically.
3. Failure triage (in run.py report). Cases that fail under EVERY config tried can't be fixed by
   any existing knob: that is a missing parameter or a code/prompt change, and it's listed for a human.
"""
from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path

from agent import config as call_config

BOT_SRC = Path(__file__).resolve().parent.parent / "agent" / "bot.py"


def _names(obj) -> list[str]:
    if dataclasses.is_dataclass(obj):
        return [f.name for f in dataclasses.fields(obj)]
    if hasattr(obj, "model_fields"):
        return list(obj.model_fields)
    return [p for p in inspect.signature(obj).parameters if p not in ("self", "args", "kwargs")]


def provider_params() -> dict[str, list[str]]:
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
    from pipecat.services.google.llm import GoogleLLMService
    from pipecat.services.sarvam.stt import SarvamRealtimeSTTService
    from pipecat.services.sarvam.tts import SarvamTTSService

    return {
        "sarvam_stt": _names(SarvamRealtimeSTTService.__init__) + _names(SarvamRealtimeSTTService.Settings),
        "sarvam_tts": _names(SarvamTTSService.__init__) + _names(SarvamTTSService.Settings),
        "gemini_llm": _names(GoogleLLMService.__init__) + _names(GoogleLLMService.Settings),
        "silero_vad": _names(VADParams),
        "user_turns": _names(LLMUserAggregatorParams),
    }


# Plumbing every service takes; never a tuning decision.
_ALWAYS_IGNORE = {"api_key", "settings", "params", "extra", "model", "language", "sample_rate", "base_url", "url",
                  "http_options", "system_instruction", "tools", "tool_config", "voice_id"}


def unwired(review: dict) -> dict[str, list[dict]]:
    """Provider params bot.py never passes, split into reviewed / NEW (needs a decision)."""
    src = BOT_SRC.read_text()
    out = {"new": [], "reviewed": [], "hardcoded": []}
    for prov, names in provider_params().items():
        for n in sorted(set(names) - _ALWAYS_IGNORE - {"language"} | ({"language"} if prov == "sarvam_stt" else set())):
            # set to a literal / constant instead of a config value: works, but nobody can tune it
            lit = re.search(rf"\b{n}\s*=\s*([\"'][^\"']*[\"']|[A-Z][A-Z_]+\b|Language\.\w+|-?\d[\d.]*)(?=\s*[,)\n])", src)
            if lit and f"{prov}.{n}" not in review:
                out["hardcoded"].append({"param": f"{prov}.{n}", "value": lit.group(1)})
                continue
            if re.search(rf"\b{n}\s*=", src) or re.search(rf"[\"']{n}[\"']", src):
                continue  # wired to config
            key = f"{prov}.{n}"
            item = {"param": key, **({"decision": review[key]} if key in review else {})}
            out["reviewed" if key in review else "new"].append(item)
    return out


def coverage(plan: dict) -> dict:
    """Errors (block the run) and warnings for plan.yaml against config.FIELDS."""
    fields = {f["key"]: f for f in call_config.FIELDS}
    swept: dict[str, list] = {}
    for st in plan["stages"].values():
        for k, spec in st.get("sweep", {}).items():
            swept[k] = spec["values"] if isinstance(spec, dict) else spec
        for arm in st.get("arms", {}).values():
            for k, v in arm.items():
                swept.setdefault(k, []).append(v)
    pinned = plan.get("pinned", {})
    errors, warnings = [], []
    for k in fields:
        if k not in swept and k not in pinned:
            errors.append(f"'{k}' ({fields[k]['label']}) has no test decision: sweep it in a stage or pin it "
                          f"with a reason in plan.yaml")
        if k in pinned and not str(pinned[k].get("reason", "")).strip():
            errors.append(f"'{k}' is pinned without a reason")
    for k in list(swept) + list(pinned):
        if k not in fields:
            errors.append(f"plan.yaml names '{k}', which is not a setting in agent/config.py (stale or typo)")
    for k, vals in swept.items():
        f = fields.get(k)
        if f and f["type"] == "select":
            skipped = plan.get("skip_options", {}).get(k, {})
            for opt, _ in f["options"]:
                if opt not in vals and opt not in skipped:
                    warnings.append(f"'{k}' option '{opt}' is never tested (add it, or list it in skip_options with a reason)")
        if f and f["type"] == "bool" and set(vals) != {True, False}:
            warnings.append(f"'{k}' is a switch but only {vals} is tested")
    return {"errors": errors, "warnings": warnings, "swept": sorted(swept), "pinned": sorted(pinned)}
