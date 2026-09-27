#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
mkdir -p models
model=models/Qwen3-0.6B-Q4_K_M.gguf
expected=ac2d97712095a558e31573f62f466a3f9d93990898b0ec79d7c974c1780d524a
if [[ -f "$model" ]] && echo "$expected  $model" | sha256sum --check --status; then
  exit 0
fi
curl --fail --location --retry 3 --output "$model.part" \
  https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/resolve/50968a4468ef4233ed78cd7c3de230dd1d61a56b/Qwen3-0.6B-Q4_K_M.gguf
echo "$expected  $model.part" | sha256sum --check
mv -- "$model.part" "$model"
