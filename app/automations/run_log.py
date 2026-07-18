"""Log em memória do progresso da automação, consumido pelo dashboard web via polling."""
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RunLog:
    entries: list = field(default_factory=list)
    running: bool = False
    current_cnpj: str = ""
    pause_requested: bool = False


_log = RunLog()
_lock = threading.Lock()


def start_run(cnpj: str) -> None:
    with _lock:
        _log.running = True
        _log.current_cnpj = cnpj
        _log.entries = []
        _log.pause_requested = False


def request_pause() -> None:
    with _lock:
        _log.pause_requested = True


def is_pause_requested() -> bool:
    with _lock:
        return _log.pause_requested


class PausedByUser(Exception):
    """Levantada entre etapas quando o usuário pede pausa pelo dashboard."""
    pass


def check_pause() -> None:
    if is_pause_requested():
        raise PausedByUser("Execução pausada pelo usuário")


def add(message: str, level: str = "info") -> None:
    with _lock:
        _log.entries.append({
            "message": message,
            "level": level,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


def finish_run(success: bool, message: str = "") -> None:
    with _lock:
        _log.running = False
        if message:
            _log.entries.append({
                "message": message,
                "level": "success" if success else "error",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })


def get_state() -> dict:
    with _lock:
        return {
            "running": _log.running,
            "current_cnpj": _log.current_cnpj,
            "entries": list(_log.entries),
            "pause_requested": _log.pause_requested,
        }
