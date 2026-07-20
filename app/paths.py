"""Resolve o diretório base do projeto — funciona tanto rodando via
`python -m app.web.server` quanto empacotado com PyInstaller.

Empacotado, `sys.frozen` é True e os arquivos editáveis pelo usuário (.env,
cnpjs.csv, data/) devem ficar ao lado do .exe, não dentro do bundle interno
(_MEIPASS ou a pasta do onedir), senão o usuário não conseguiria editá-los."""
import os
import sys


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


BASE_DIR = _base_dir()
