"""Entry point do executável — inicia o dashboard Flask numa janela de console
(veja MavioRobot.spec, console=True) e abre o navegador padrão automaticamente.
A janela mostra os logs do servidor; fechá-la (X ou Ctrl+C) encerra o robô.

Usa um mutex nomeado do Windows para garantir uma única instância: se o app
já estiver rodando (ex: usuário clicou duas vezes no .exe), a segunda cópia
só abre o navegador na instância existente e encerra — evita duas instâncias
disputando a porta e travando o dashboard."""
import ctypes
import sys
import threading
import time
import webbrowser

PORT = 5051
_MUTEX_NAME = "Global\\MavioRobotSingleInstanceMutex"


def _acquire_single_instance_lock() -> bool:
    """True se esta é a única instância; False se outra já está rodando."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _open_browser():
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


if __name__ == "__main__":
    if not _acquire_single_instance_lock():
        print("O Mavio Robot já está rodando — abrindo o dashboard existente.")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        sys.exit(0)

    from app.web.server import app

    print("Mavio Robot iniciando...")
    print(f"Dashboard: http://127.0.0.1:{PORT}")
    print("Feche esta janela (ou Ctrl+C) para encerrar o robô.\n")

    threading.Thread(target=_open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)
