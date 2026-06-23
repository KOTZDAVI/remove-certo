#!/bin/bash
# Instala o Remove Certo no menu de aplicativos do Linux

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

INSTALL_DIR="$HOME/.local/share/RemoveCerto"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
DESKTOP_DIR="$HOME/.local/share/applications"

echo "=== Instalando Remove Certo ==="
echo "Destino: $INSTALL_DIR"
echo ""

# ── Copia o app ──────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
echo "[1/4] Copiando arquivos..."
cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/RemoveCerto"

# ── Instala o ícone ──────────────────────────────────────────────────────────
echo "[2/4] Instalando ícone..."
mkdir -p "$ICON_DIR"
cp "$INSTALL_DIR/icon.png" "$ICON_DIR/remove-certo.png"

# ── Cria o arquivo .desktop ──────────────────────────────────────────────────
echo "[3/4] Registrando no menu de aplicativos..."
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/remove-certo.desktop" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Remove Certo
GenericName=Removedor de Fundo
Comment=Remove o fundo de imagens com IA — processamento local
Exec=$INSTALL_DIR/RemoveCerto
Icon=remove-certo
Terminal=true
Categories=Graphics;Photography;
Keywords=fundo;background;remove;imagem;foto;IA;
StartupNotify=true
DESKTOP

chmod +x "$DESKTOP_DIR/remove-certo.desktop"

# ── Atualiza banco de dados do desktop ───────────────────────────────────────
echo "[4/4] Atualizando menu..."
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
xdg-desktop-menu forceupdate 2>/dev/null || true

echo ""
echo "=== Instalação concluída! ==="
echo "O Remove Certo aparecerá no menu de aplicativos em alguns segundos."
echo "Também pode ser iniciado com: $INSTALL_DIR/RemoveCerto"
echo ""
echo "Para desinstalar: bash $INSTALL_DIR/desinstalar.sh"
