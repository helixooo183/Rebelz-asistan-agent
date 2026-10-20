#!/usr/bin/env bash
# Telefon tarayıcısı arayüzünü başlatır: http://127.0.0.1:8787
# Aynı ağdaki başka cihazlardan erişmek için:
#   REBELZ_WEB_HOST=0.0.0.0 REBELZ_WEB_TOKEN=uzun-gizli-bir-anahtar bash scripts/start_web.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m rebelz web
