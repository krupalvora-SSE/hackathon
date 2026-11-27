#!/usr/bin/env bash
# Month-name training set, kept apart from the main Hinglish set.
#
#   bot/voice_training/months/prepare_months.sh ~/Downloads/months-1.mp3 [~/Downloads/months-2.mp3 ...]
#
# Env: NAME (default krupal) — speaker.
#
# Writes months/data/${NAME}_months/ + ${NAME}_months.zip (git-ignored): the month lines only
# (script_months.txt); report.md lists what was kept and dropped. The main dataset is not touched;
# the Colab notebook mixes in a few of its clips itself.
set -euo pipefail
[ $# -ge 1 ] || { sed -n 2,12p "$0"; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
BOT="$(cd "$HERE/../.." && pwd)"
NAME="${NAME:-krupal}"
OUT="$HERE/data"
mkdir -p "$OUT"

# Month lines are often read back to back: re-cut anything over 5 s at 0.15 s pauses (34/40 vs 27/40 kept).
(cd "$BOT" && uv run --extra whisper python voice_training/prepare.py "$@" \
    --name "${NAME}_months" --script "$HERE/script_months.txt" --out-dir "$OUT" \
    --long-piece 5 --short-pause 0.15)

echo
echo "Colab: upload $OUT/${NAME}_months.zip to My Drive/voice_training/, run months/train_months_colab.ipynb"
