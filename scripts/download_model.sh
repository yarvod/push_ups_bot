#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
mkdir -p models
model=models/Vikhr-Qwen-2.5-0.5B-instruct-Q5_K_M.gguf
expected=7a37b861539603247887fe4044d69535c0230291e9707718dfd106efc6dbe81e
if [[ -f "$model" ]] && echo "$expected  $model" | sha256sum --check --status; then
  exit 0
fi
curl --fail --location --retry 3 --output "$model.part" \
  https://huggingface.co/Vikhrmodels/Vikhr-Qwen-2.5-0.5B-instruct-GGUF/resolve/a2bc2797ec4dd39dd99f20a92f7a05a71c8122bf/Vikhr-Qwen-2.5-0.5B-instruct-Q5_K_M.gguf
echo "$expected  $model.part" | sha256sum --check
mv -- "$model.part" "$model"
