"""Conecta o Selenium a um perfil Chrome DEDICADO (não o perfil padrão do
usuário) via debuggerAddress, usado só para automatizar o bot de números
virtuais no Telegram Web.

Descoberta ao vivo: o Chrome recusa habilitar debug remoto no diretório de
perfil PADRÃO do usuário por segurança ("DevTools remote debugging requires
a non-default data directory") — não tem como contornar isso apontando
'--user-data-dir' direto para 'AppData\\Local\\Google\\Chrome\\User Data'.

data/telegram_chrome_profile/ é criado automaticamente pelo próprio Chrome na
primeira vez que abre (fica vazio até isso acontecer, e é ignorado pelo git —
não é dado versionado, é estado local de sessão). Quem clona o projeto do
zero vai encontrar esse Chrome pedindo login do Telegram na primeira
execução; depois de logar uma vez, a sessão fica salva ali permanentemente,
igual qualquer perfil Chrome comum — não precisa logar de novo.
"""
import os
import subprocess
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

DEBUG_PORT = 9333

_USER_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "telegram_chrome_profile"
)
_CHROME_EXE_CANDIDATES = [
    os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
]

_driver: webdriver.Chrome | None = None


def _find_chrome_exe() -> str:
    for path in _CHROME_EXE_CANDIDATES:
        if path and os.path.exists(path):
            return path
    raise RuntimeError(
        "Não foi possível encontrar o executável do Chrome nos caminhos padrão. "
        "Verifique se o Google Chrome está instalado."
    )


def _is_driver_alive(driver: webdriver.Chrome) -> bool:
    try:
        driver.current_window_handle
        return True
    except Exception:
        return False


def _launch_chrome_with_debug_port() -> None:
    """Abre o Chrome no perfil dedicado (data/telegram_chrome_profile/) com
    debug remoto ativado. Não mexe no Chrome pessoal do usuário — é um
    perfil isolado, criado automaticamente pelo Chrome na primeira vez."""
    os.makedirs(_USER_DATA_DIR, exist_ok=True)
    chrome_exe = _find_chrome_exe()
    subprocess.Popen([
        chrome_exe,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={_USER_DATA_DIR}",
        "--profile-directory=Default",
        "--no-first-run",
    ])
    time.sleep(2.5)


def _connect() -> webdriver.Chrome:
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    return webdriver.Chrome(options=options)


def open_telegram_driver() -> webdriver.Chrome:
    """Abre (ou reaproveita) o Chrome comum do usuário com debug remoto
    ativado. Mantém a janela aberta entre chamadas — evita reabrir/relogar
    a cada geração de número."""
    global _driver
    if _driver is not None and _is_driver_alive(_driver):
        return _driver

    try:
        _driver = _connect()
        if _is_driver_alive(_driver):
            return _driver
    except Exception:
        pass

    # não conseguiu anexar a uma instância já rodando com debug ativo —
    # fecha e reabre o Chrome comum com a flag necessária
    _launch_chrome_with_debug_port()
    _driver = _connect()
    return _driver


def force_close_telegram_driver() -> None:
    global _driver
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None
