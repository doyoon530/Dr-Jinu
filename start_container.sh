#!/bin/sh
set -eu

MODEL_PATH="${MODEL_PATH:-/data/models/EXAONE-3.5-7.8B-Instruct-Q8_0.gguf}"

if [ ! -f "$MODEL_PATH" ] && [ -n "${MODEL_DOWNLOAD_URL:-}" ]; then
  echo "Model file not found. Downloading to $MODEL_PATH"
  mkdir -p "$(dirname "$MODEL_PATH")"
  curl -L "$MODEL_DOWNLOAD_URL" -o "$MODEL_PATH"
fi

exec python app.py
