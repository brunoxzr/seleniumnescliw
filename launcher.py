"""Entry point do executável — inicia o dashboard Flask e abre no navegador padrão.
Roda sem janela de console (veja MavioRobot.spec, console=False); todo o
feedback de execução acontece no dashboard web em si."""
import threading
import time
import webbrowser

from app.web.server import app

PORT = 5050


def _open_browser():
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
