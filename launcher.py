import os
import sys
import time
import threading
import webbrowser
import importlib.util

# Garante que o diretório de trabalho seja o do executável (modo frozen)
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))

_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.abspath(__file__))

# ── Carrega o app: usa app_update.py (baixado pelo updater) se existir ────────
_override = os.path.join(_BASE_DIR, "app_update.py")
if os.path.exists(_override):
    print("[launcher] Carregando versão atualizada (app_update.py)...")
    spec = importlib.util.spec_from_file_location("app", _override)
    _mod = importlib.util.module_from_spec(spec)
    sys.modules["app"] = _mod
    spec.loader.exec_module(_mod)
    app = _mod.app
else:
    from app import app

# ── Rota extra: status de atualização (consultada pelo frontend) ──────────────
from flask import jsonify
import updater as _updater

@app.route("/update-status")
def update_status():
    return jsonify(_updater.update_result)

@app.route("/restart", methods=["POST"])
def restart_app():
    """Reinicia o processo para aplicar atualização baixada."""
    def _do_restart():
        time.sleep(0.5)
        os.execv(sys.executable, sys.argv)
    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"ok": True})

# ── Inicia verificação de updates em background ───────────────────────────────
_updater.start_background()

PORT = 5050
URL  = f"http://localhost:{PORT}"


def _open_browser_when_ready():
    """Espera o Flask subir de verdade antes de abrir o navegador."""
    import urllib.request
    for _ in range(120):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1)
            webbrowser.open(URL)
            # Fecha o terminal após abrir o navegador
            if sys.stdout.isatty():
                time.sleep(1)
                os.system("pkill -P $$ 2>/dev/null; kill $(ps -o ppid= -p $$) 2>/dev/null || true")
            return
        except Exception:
            pass


if __name__ == "__main__":
    print(f"Remove Certo iniciando em {URL}")
    print("Pressione Ctrl+C para encerrar.")
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    app.run(debug=False, host="127.0.0.1", port=PORT, use_reloader=False)
