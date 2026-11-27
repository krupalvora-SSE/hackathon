# Eval pipeline

Finds the best combination of call settings (STT engine, turn detection, LLM) by measuring, not guessing.

```bash
cd bot
uv run python -m eval.run check          # does every setting have a test decision? (no API calls)
uv run python -m eval.corpus             # build the audio corpus once (local Piper voices, cached)
uv run python -m eval.run all --quick    # smoke run: 24 clips, 4 personas (~10 min)
uv run python -m eval.run all            # full run; add --apply to save the winner to the /voice page
```

Results go to `eval/runs/<time>/`: `report.md`, `recommended.json`, and per-config rows (`*.jsonl`) with each transcript.

## Stages

| Stage | What runs | Scored on |
|---|---|---|
| `stt` | Every clip in `corpus/` transcribed locally by Whisper, segmented by the call's Silero settings | call-critical words heard (yes/no, day, date, band, ladder, water), script-normalised CER, latency from end of speech, noise heard as speech, thinking pauses that ended the turn |
| `conv` | Simulated customers (`personas.yaml`) talking to the real prompt, tools and flow via `simulate.run_call` | correct outcome, compliance (no prices), LLM latency, cost |

Each stage compares **arms** head to head (one arm per LLM model). Within an arm it tries one setting at a time, keeps the values that beat the baseline by `min_gain`, re-runs them combined (settings interact), and falls back to adding them one by one if the combination is worse. The winning STT settings carry into the conversation stage. Weights and gates are in `plan.yaml`.

## Who decides what's missing

Not a person's memory. The pipeline checks three things:

1. **Coverage gate** (`check`): every setting in `agent/config.py` must be swept in `plan.yaml` or pinned with a written reason. A new /voice setting without a test decision fails the check (and `tests/test_eval.py`). Select options never tried are warned.
2. **Provider diff** (`check`): reads the parameters pipecat's Piper TTS, Gemini, Silero and turn aggregator accept, and lists the ones the bot never sets, plus values hard-coded in `agent/bot.py`. A library upgrade that adds a parameter shows up here automatically. Decide each once in `plan.yaml` `provider_review` ("expose: …" or "ignore: …"); `check --strict` fails while any are undecided.
3. **Failure triage** (`report.md`): clips or personas that failed under **every** config tried. No existing setting fixes them, so each one is a missing parameter or a prompt, flow or code change. A person reads this list and decides which.

A person still makes the final decisions: which flagged parameters to expose, and the score weights in `plan.yaml`. The pipeline makes sure nothing goes unnoticed.

## Adding test material

- New mishearing: add a line to `utterances.yaml` and rebuild the corpus.
- Real recordings: `corpus/real/<name>.wav` plus `<name>.txt` (exact words), optional `<name>.json` `{"expect": [...]}`. `corpus/` is git-ignored, so customer audio stays local.
- New conversation bug: add a persona to `personas.yaml` with the expected outcomes.

## Limits

- Corpus voices are local Piper speakers (Pratham, Rohan) put through a simulated G.711 phone line with noise. Add real call audio before trusting small differences.
- Local turn detection is simulated with Silero alone; Smart Turn, which a live call also runs, is not.
- The conversation stage calls Gemini through its OpenAI-compatible endpoint, as `simulate.py` does; live calls use pipecat's native Gemini service.
- TTS settings are pinned: they need listening tests, not a script.
