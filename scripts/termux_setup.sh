#!/data/data/com.termux/files/usr/bin/bash
# Rebelz'i Android telefonda (Termux) kurar:
#  1) Gerekli paketleri kurar
#  2) llama.cpp'yi derler (yerel model sunucusu, kota yok)
#  3) Qwen2.5 3B (Q4_K_M, ~2.1 GB) modelini indirir
#
# Kullanım:  bash scripts/termux_setup.sh
# Farklı model için:  MODEL_URL=https://.../model.gguf bash scripts/termux_setup.sh
set -euo pipefail

MODEL_URL="${MODEL_URL:-https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf}"
MODELS_DIR="$HOME/models"
LLAMA_DIR="$HOME/llama.cpp"
JOBS="${JOBS:-4}"

echo "==> Paketler kuruluyor"
pkg update -y
pkg install -y python git cmake clang make wget curl

echo "==> llama.cpp derleniyor"
if [ ! -d "$LLAMA_DIR" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
fi
cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$LLAMA_DIR/build" --config Release -j "$JOBS" --target llama-server

echo "==> Model indiriliyor"
mkdir -p "$MODELS_DIR"
MODEL_FILE="$MODELS_DIR/$(basename "$MODEL_URL")"
if [ ! -f "$MODEL_FILE" ]; then
  wget -c -O "$MODEL_FILE.part" "$MODEL_URL"
  mv "$MODEL_FILE.part" "$MODEL_FILE"
fi

echo "==> Tamam. Model: $MODEL_FILE"
echo "Sonraki adım: bash scripts/start_llm.sh  (ayrı bir Termux oturumunda)"
echo "Ardından:      bash scripts/start_web.sh  veya  bash scripts/start_cli.sh"
