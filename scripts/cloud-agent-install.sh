#!/usr/bin/env bash
# Idempotent Cloud Agent install for this repository.
set -euo pipefail

cd "$(dirname "$0")/.."

export PATH="${HOME}/.local/bin:${PATH}"

python3 -m pip install --user --upgrade pip
python3 -m pip install --user -r requirements_test.txt

mkdir -p "${HOME}/.local/bin"
for tool in pytest ruff; do
  if [ -x "${HOME}/.local/bin/${tool}" ]; then
    sudo ln -sf "${HOME}/.local/bin/${tool}" "/usr/local/bin/${tool}"
  fi
done
