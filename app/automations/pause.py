"""Mecanismo de pausa para intervenção manual durante um fluxo de automação.

Uma etapa chama `wait_for_manual_step(...)` que bloqueia a thread da automação
até que a interface web sinalize retomada via `resume()`. Suporta múltiplos
slots independentes (Robô A / Robô B) — cada um com seu próprio estado de
pausa, para não bloquear um robô ao confirmar o outro.
"""
import threading
from dataclasses import dataclass, field

DEFAULT_SLOT = "A"


@dataclass
class ManualStepState:
    active: bool = False
    message: str = ""
    _event: threading.Event = field(default_factory=threading.Event)


_states: dict[str, ManualStepState] = {}
_lock = threading.Lock()


def _get(slot: str) -> ManualStepState:
    if slot not in _states:
        _states[slot] = ManualStepState()
    return _states[slot]


def wait_for_manual_step(message: str, timeout: int = 900, slot: str = DEFAULT_SLOT) -> None:
    state = _get(slot)
    with _lock:
        state.active = True
        state.message = message
        state._event.clear()

    resumed = state._event.wait(timeout=timeout)

    with _lock:
        state.active = False
        state.message = ""

    if not resumed:
        raise TimeoutError(f"Etapa manual não confirmada a tempo: {message}")


def resume(slot: str = DEFAULT_SLOT) -> None:
    _get(slot)._event.set()


def get_status(slot: str = DEFAULT_SLOT) -> dict:
    with _lock:
        state = _get(slot)
        return {"active": state.active, "message": state.message}
