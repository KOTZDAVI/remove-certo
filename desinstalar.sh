#!/bin/bash
echo "=== Desinstalando Remove Certo ==="
rm -f "$HOME/.local/share/applications/remove-certo.desktop"
rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/remove-certo.png"
rm -rf "$HOME/.local/share/RemoveCerto"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "Remove Certo desinstalado."
