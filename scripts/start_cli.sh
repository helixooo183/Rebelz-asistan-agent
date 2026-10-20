#!/usr/bin/env bash
# Terminal sohbetini başlatır.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m rebelz cli
