#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== Remove Certo — Build Linux ==="

# ── Verificações ──────────────────────────────────────────────────────────────
command -v pyinstaller >/dev/null || { echo "ERRO: pyinstaller não encontrado. Rode: pip3 install pyinstaller"; exit 1; }
python3 -c "import rembg" 2>/dev/null || { echo "ERRO: rembg não instalado. Rode: pip3 install rembg"; exit 1; }

# ── Build ─────────────────────────────────────────────────────────────────────
echo "[1/3] Compilando com PyInstaller..."
pyinstaller remove_certo.spec --noconfirm

# ── Copiar modelo ─────────────────────────────────────────────────────────────
echo "[2/3] Copiando modelo de IA..."
DIST_DIR="dist/RemoveCerto"
mkdir -p "$DIST_DIR/models"

MODEL_DIR="${REMBG_HOME:-$HOME/.u2net}"
if [ -f "$MODEL_DIR/isnet-general-use.onnx" ]; then
    cp "$MODEL_DIR/isnet-general-use.onnx" "$DIST_DIR/models/"
    echo "    isnet-general-use.onnx copiado."
elif [ -f "$MODEL_DIR/u2net.onnx" ]; then
    cp "$MODEL_DIR/u2net.onnx" "$DIST_DIR/models/"
    echo "    u2net.onnx copiado."
else
    echo "    AVISO: modelo não encontrado em $MODEL_DIR"
    echo "    O app vai baixar o modelo (~171MB) na primeira execução."
fi

# Criar pastas de trabalho vazias
mkdir -p "$DIST_DIR/results" "$DIST_DIR/originals" "$DIST_DIR/backgrounds"

# Copiar ícone, scripts e configs
cp icon.png            "$DIST_DIR/icon.png"
cp instalar.sh         "$DIST_DIR/instalar.sh"
cp desinstalar.sh      "$DIST_DIR/desinstalar.sh"
cp version.json        "$DIST_DIR/version.json"
cp update_config.json  "$DIST_DIR/update_config.json"
chmod +x "$DIST_DIR/instalar.sh" "$DIST_DIR/desinstalar.sh"

# ── Empacotar ─────────────────────────────────────────────────────────────────
echo "[3/3] Criando RemoveCerto-linux.tar.gz..."
cd dist
tar -czf RemoveCerto-linux.tar.gz RemoveCerto/
cd ..

SIZE=$(du -sh dist/RemoveCerto-linux.tar.gz | cut -f1)
echo ""
echo "=== Pronto! ==="
echo "Arquivo: dist/RemoveCerto-linux.tar.gz ($SIZE)"
echo ""
echo "Para usar em outro computador Linux:"
echo "  1. Extraia: tar -xzf RemoveCerto-linux.tar.gz"
echo "  2. Entre na pasta: cd RemoveCerto"
echo "  3. Execute: ./RemoveCerto"
echo "  (o navegador abre automaticamente em http://localhost:5050)"
