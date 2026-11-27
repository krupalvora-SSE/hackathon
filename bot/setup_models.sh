#!/usr/bin/env bash
# One-time setup of the local models the bot needs (run from anywhere).
#   1. Copies our trained Piper voices (bot/models/piper) into ~/.cache/pipecat/piper
#   2. Fetches the stock Piper English voices (Ryan, Alan) and Hindi voices (Pratham, Rohan) if missing
#   3. Fetches Whisper large-v3-turbo into the local model cache (MLX on Apple Silicon, CTranslate2 elsewhere)
# After this the bot runs fully offline (HF_HUB_OFFLINE=1); only Gemini is a cloud call.
set -euo pipefail
cd "$(dirname "$0")"

PIPER_DIR="$HOME/.cache/pipecat/piper"
mkdir -p "$PIPER_DIR"

echo "==> Python deps (uv sync --extra whisper)"
uv sync --extra whisper

echo "==> Trained voices -> $PIPER_DIR"
cp -v models/piper/*.onnx models/piper/*.onnx.json "$PIPER_DIR/"

echo "==> Stock Piper voices"
for v in en_US-ryan-medium en_GB-alan-medium hi_IN-pratham-medium hi_IN-rohan-medium; do
  if [ -f "$PIPER_DIR/$v.onnx" ]; then echo "  $v: present"; else
    uv run python -m piper.download_voices --download-dir "$PIPER_DIR" "$v"; fi
done

echo "==> Whisper large-v3-turbo (~1.6 GB, once)"
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  MODEL=mlx-community/whisper-large-v3-turbo
else
  MODEL=deepdml/faster-whisper-large-v3-turbo-ct2
fi
HF_HUB_OFFLINE=0 uv run python -c "from huggingface_hub import snapshot_download as s; print(s('$MODEL'))"

[ -f .env ] || { cp .env.example .env; echo "==> Created bot/.env: set GOOGLE_API_KEY"; }
echo "Done. Start: uv run --extra whisper uvicorn server:app --port 7860   (and: cd ../app && npm install && npm start)"
