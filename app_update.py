"""
Shim carregado pelo launcher no lugar do app embutido no binário.

O updater baixa este arquivo junto com o app.py atualizado para a pasta do
executável. O launcher prioriza app_update.py, e este módulo apenas carrega
o app.py que está ao lado dele — assim as atualizações valem mesmo nos
binários compilados, onde o app.py embutido não pode ser substituído.
"""
import os
import sys
import importlib.util

_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.abspath(__file__))

_APP_PATH = os.path.join(_BASE_DIR, "app.py")

if os.path.isfile(_APP_PATH):
    _spec = importlib.util.spec_from_file_location("app_atualizado", _APP_PATH)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["app_atualizado"] = _mod
    _spec.loader.exec_module(_mod)
    app = _mod.app
else:
    # app.py baixado não existe — volta ao app embutido no binário
    sys.modules.pop("app", None)
    import app as _embutido
    app = _embutido.app
