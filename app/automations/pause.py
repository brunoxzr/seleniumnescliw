"""Mecanismo de pausa para intervenção manual durante um fluxo de automação.

Uma etapa chama `wait_for_manual_step(...)` que bloqueia a thread da automação
até que a interface web sinalize retomada via `resume()`.
"""
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ManualStepState:
    active: bool = False
    message: str = ""
    _event: threading.Event = field(default_factory=threading.Event)


_state = ManualStepState()
_lock = threading.Lock()


def wait_for_manual_step(message: str, timeout: int = 900) -> None:
    with _lock:
        _state.active = True
        _state.message = message
        _state._event.clear()

    resumed = _state._event.wait(timeout=timeout)

    with _lock:
        _state.active = False
        _state.message = ""

    if not resumed:
        raise TimeoutError(f"Etapa manual não confirmada a tempo: {message}")


def resume() -> None:
    _state._event.set()


def get_status() -> dict:
    with _lock:
        return {"active": _state.active, "message": _state.message}
