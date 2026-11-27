# Month-name voice training

The main Piper voice mispronounces month names (नवंबर, अक्टूबर, फ़रवरी…). This folder holds a short
extra script that drills them, and a short fine-tune of the already-trained voice on just those clips.
The main dataset and the main training run are only read, never changed.

| File | What |
|---|---|
| `script_months.txt` | 40 lines: dates and months the bot actually says, plus all 12 months in a row |
| `prepare_months.sh` | recording(s) → `data/<name>_months.zip` (month clips only) |
| `train_months_colab.ipynb` | Colab: continues from the trained main voice on the month clips + 30 random main clips (~300 epochs) |
| `data/` | output, git-ignored |

## Steps

1. Read `script_months.txt` aloud. **Pause about half a second after each line**; lines run together
   are cut apart only if there's a short pause between them, and otherwise they're dropped.
2. Run:
   ```bash
   bot/voice_training/months/prepare_months.sh ~/Downloads/months.mp3
   ```
   Several recordings are fine; pass them in reading order. `NAME=jasir` for another speaker.
3. Check `data/<name>_months/report.md`: it lists the missing lines (re-record just those) and clips worth a listen.
4. Upload `data/<name>_months.zip` to `My Drive/voice_training/` and run `train_months_colab.ipynb` in Colab
   (`SPEAKER = "<name>"`). It starts from the newest checkpoint of the main run (`<name>_run/` on Drive,
   read only) and mixes in `REPLAY` random clips from `<name>.zip` (already on Drive) so the rest of the
   voice doesn't drift. Output goes to `<name>_months_run/`; the old run and voice stay as they are.
5. Step 4 of the notebook plays all twelve months; when they sound right, download
   `hi_IN-<name>_months-medium.onnx` + `.onnx.json` and point the bot at that voice.
