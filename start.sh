#!/bin/bash
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

# Se já está rodando, só abre o browser
if curl -s --max-time 1 http://127.0.0.1:5050/ > /dev/null 2>&1; then
    setsid xdg-open http://127.0.0.1:5050 >/dev/null 2>&1
    exit 0
fi

echo "Iniciando Remove Certo..."
echo "(aguarde ~30 segundos enquanto o modelo de IA carrega)"

# Inicia Flask em nova sessão — sobrevive ao fechamento do terminal
setsid "$APP_DIR/RemoveCerto" > "$APP_DIR/removecerto.log" 2>&1 &

# Aguarda o Flask ficar pronto
for i in $(seq 1 120); do
    sleep 1
    if curl -s --max-time 1 http://127.0.0.1:5050/ > /dev/null 2>&1; then
        echo "Pronto! Abrindo navegador..."
        setsid xdg-open http://127.0.0.1:5050 >/dev/null 2>&1
        exit 0
    fi
done

echo "Erro: app não respondeu em 2 minutos"
