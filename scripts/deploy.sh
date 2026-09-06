#!/usr/bin/env bash
set -euo pipefail

deploy() {
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

  if [[ "$(git branch --show-current)" != main ]]; then
    echo "Деплой разрешён только из ветки main." >&2
    return 1
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "На сервере есть незакоммиченные изменения. Сначала сохраните их в Git." >&2
    return 1
  fi
  if [[ ! -s .env || ! -s secrets/google-service-account.json ]]; then
    echo "Нужны серверные .env и secrets/google-service-account.json." >&2
    return 1
  fi

  git pull --ff-only origin main
  docker compose config --quiet
  docker compose build bot
  docker compose up -d --no-build --wait --wait-timeout 120 bot worker redis
  docker compose ps
  echo "Развёрнут коммит $(git rev-parse --short HEAD)"
}

# Parse the deployment body before git pull can update this very script.
deploy "$@"; exit

