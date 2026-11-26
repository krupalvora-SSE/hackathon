"""Offline checks for the eval pipeline (no API calls)."""
import copy

import yaml

from eval import params
from eval.run import ROOT, matches, normalise, score
from eval.textnorm import cer, entity_score, has_entity


def test_scripts_score_alike():
    assert cer("हाँ जी, ठीक है", "haan ji, theek hai") < 0.25
    assert cer("शनिवार सुबह आ जाओ", "शनिवार सुबह आ जाओ।") == 0


def test_entities_both_scripts():
    assert has_entity("शनिवार सुबह", "saturday") and has_entity("Shanivar subah", "morning")
    assert has_entity("28 tarikh ko", "num:28")
    hits, total, misses = entity_score("हाँ ठीक है", [True], [False])  # YAML yes/no arrive as bools
    assert (hits, total, misses) == (2, 2, [])
    assert entity_score("नहीं", ["yes"], [])[2] == ["yes"]


def test_plan_covers_every_setting():
    plan = yaml.safe_load((ROOT / "plan.yaml").read_text())
    cov = params.coverage(plan)
    assert cov["errors"] == [], cov["errors"]


def test_gate_catches_unplanned_setting():
    plan = yaml.safe_load((ROOT / "plan.yaml").read_text())
    p = copy.deepcopy(plan)
    p["pinned"].pop("idle_timeout")
    assert any("idle_timeout" in e for e in params.coverage(p)["errors"])


def test_normalise_and_when():
    cfg = normalise({"stt_provider": "indic", "indic_decoding": "ctc"})
    assert matches(cfg, {"stt_provider": "indic"}) and not matches(cfg, {"stt_provider": "whisper"})
    assert matches(cfg, None)


def test_score_gates():
    w = {"entity_acc": 100, "latency_p50": -0.005}
    assert score({"clips": 10, "errors": 0, "entity_acc": 0.9, "latency_p50": 1000}, w, {"errors_rate": 0.05}) == 85
    assert score({"clips": 10, "errors": 3, "entity_acc": 1}, w, {"errors_rate": 0.05}) is None
    assert score({"skipped": "no model"}, w, {}) is None
