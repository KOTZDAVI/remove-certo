"""
Verifica e baixa atualizações do GitHub sem bloquear a inicialização.
Arquivos atualizados ficam ao lado do executável e têm prioridade sobre
os arquivos embutidos no binário compilado.
"""
import os
import sys
import json
import urllib.request
import threading

_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.abspath(__file__))

_CONFIG_FILE  = os.path.join(_BASE_DIR, "update_config.json")
_VERSION_FILE = os.path.join(_BASE_DIR, "version.json")

# Resultado da última verificação — lido pelo endpoint /update-status
update_result = {"checked": False, "updated": False, "new_version": None, "error": None}


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fetch_json(url, timeout=6):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _fetch_bytes(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def check_and_update():
    """Roda em background — verifica GitHub e baixa arquivos se houver versão nova."""
    global update_result
    try:
        # Lê configuração
        if not os.path.exists(_CONFIG_FILE):
            return
        cfg = _read_json(_CONFIG_FILE)
        if not cfg.get("enabled"):
            return
        base_url = cfg.get("github_raw", "").rstrip("/") + "/"
        if "SEU_USUARIO" in base_url:
            return  # URL não configurada ainda

        # Versão local
        local_ver = 0
        if os.path.exists(_VERSION_FILE):
            local_ver = _read_json(_VERSION_FILE).get("version", 0)

        # Versão remota
        remote = _fetch_json(base_url + "version.json")
        remote_ver = remote.get("version", 0)

        update_result["checked"] = True

        if remote_ver <= local_ver:
            return  # já está atualizado

        print(f"[update] Nova versão disponível: v{local_ver} → v{remote_ver}")

        # Baixa cada arquivo listado
        for rel_path in remote.get("files", []):
            url  = base_url + rel_path.replace("\\", "/")
            dest = os.path.join(_BASE_DIR, rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            data = _fetch_bytes(url)
            # Escreve em arquivo temporário e substitui atomicamente
            tmp = dest + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            print(f"[update] {rel_path} atualizado")

        # Salva nova versão local
        with open(_VERSION_FILE, "w", encoding="utf-8") as f:
            json.dump(remote, f, indent=2)

        update_result["updated"]     = True
        update_result["new_version"] = remote_ver
        print(f"[update] Atualizado para v{remote_ver} — reinicie o app para aplicar.")

    except Exception as e:
        update_result["error"] = str(e)


def start_background():
    """Inicia a verificação em thread daemon (não bloqueia o Flask)."""
    t = threading.Thread(target=check_and_update, daemon=True)
    t.start()
