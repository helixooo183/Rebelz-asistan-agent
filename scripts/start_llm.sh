#!/usr/bin/env bash
# Yerel model sunucusunu başlatır (OpenAI uyumlu API: http://127.0.0.1:8080/v1).
# Telefon RAM'i azsa daha küçük bir model kullan (örn. Qwen2.5 1.5B Q4_K_M).
set -euo pipefail

SERVER="${LLAMA_SERVER:-$HOME/llama.cpp/build/bin/llama-server}"
MODEL="${REBELZ_MODEL_FILE:-$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf}"
CTX="${REBELZ_CTX:-4096}"
THREADS="${REBELZ_THREADS:-4}"

if [ ! -x "$SERVER" ]; then
  echo "llama-server bulunamadı: $SERVER (önce scripts/termux_setup.sh çalıştır)" >&2
  exit 1
fi
if [ ! -f "$MODEL" ]; then
  echo "Model bulunamadı: $MODEL" >&2
  exit 1
fi

# --jinja: modelin araç çağırma (function calling) şablonunu kullanır.
exec "$SERVER" -m "$MODEL" --host 127.0.0.1 --port 8080 -c "$CTX" -t "$THREADS" --jinja
