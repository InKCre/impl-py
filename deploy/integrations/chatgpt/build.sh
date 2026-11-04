#!/usr/bin/env bash
set -euo pipefail
# Accept the exact locally built Core image too; never build Core in this directory.
base="${1:?usage: build.sh CORE_IMAGE OUTPUT_IMAGE}"
output="${2:?usage: build.sh CORE_IMAGE OUTPUT_IMAGE}"
if [ "$base" = "$output" ]; then
  echo 'Core input and composition output must have distinct tags' >&2
  exit 2
fi
docker build --platform linux/amd64 --provenance=false \
  --build-arg "CORE_IMAGE=$base" --tag "$output" "$(dirname "$0")"
