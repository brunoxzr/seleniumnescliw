"""Log em memória do progresso da automação, consumido pelo dashboard web via polling.

Suporta múltiplos "slots" de execução independentes (ex: Robô A / Robô B),
cada um rodando em sua própria thread com seu próprio log/estado — permite
duas automações em paralelo sem uma interferir na outra.
"""
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_SLOT = "A"


@dataclass
class RunLog:
    entries: list = field(default_factory=list)
    running: bool = False
    current_cnpj: str = ""
    pause_requested: bool = False


_logs: dict[str, RunLog] = {}
_lock = threading.Lock()


def _get(slot: str) -> RunLog:
    if slot not in _logs:
        _logs[slot] = RunLog()
    return _logs[slot]


def begin(slot: str = DEFAULT_SLOT) -> None:
    """Marca o robô como rodando ANTES de saber qual CNPJ será processado —
    cobre erros que acontecem antes disso (abrir o driver do AdsPower, login
    no Buildfy) para que apareçam no log em vez de desaparecer silenciosamente
    (a exceção subia direto pro caller, que só sabe logar via start_run/
    finish_run, e start_run só era chamado depois de escolher o CNPJ)."""
    with _lock:
        log = _get(slot)
        log.running = True
        log.current_cnpj = ""
        log.entries = []
        log.pause_requested = False


def start_run(cnpj: str, slot: str = DEFAULT_SLOT) -> None:
    with _lock:
        log = _get(slot)
        log.running = True
        log.current_cnpj = cnpj
        log.pause_requested = False


def request_pause(slot: str = DEFAULT_SLOT) -> None:
    with _lock:
        _get(slot).pause_requested = True


def is_pause_requested(slot: str = DEFAULT_SLOT) -> bool:
    with _lock:
        return _get(slot).pause_requested


class PausedByUser(Exception):
    """Levantada entre etapas quando o usuário pede pausa pelo dashboard."""
    pass


def check_pause(slot: str = DEFAULT_SLOT) -> None:
    if is_pause_requested(slot):
        raise PausedByUser("Execução pausada pelo usuário")


def add(message: str, level: str = "info", slot: str = DEFAULT_SLOT) -> None:
    with _lock:
        _get(slot).entries.append({
            "message": message,
            "level": level,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


def finish_run(success: bool, message: str = "", slot: str = DEFAULT_SLOT) -> None:
    with _lock:
        log = _get(slot)
        log.running = False
        if message:
            log.entries.append({
                "message": message,
                "level": "success" if success else "error",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })


def get_state(slot: str = DEFAULT_SLOT) -> dict:
    with _lock:
        log = _get(slot)
        return {
            "running": log.running,
            "current_cnpj": log.current_cnpj,
            "entries": list(log.entries),
            "pause_requested": log.pause_requested,
        }


def list_slots() -> list[str]:
    with _lock:
        return sorted(_logs.keys()) or [DEFAULT_SLOT]


def get_cnpjs_in_use(exclude_slot: str | None = None) -> set[str]:
    """CNPJs que estão sendo processados agora em algum robô — usado para tirar
    da lista de seleção dos OUTROS robôs enquanto a automação está rodando,
    evitando dois robôs pegarem o mesmo CNPJ ao mesmo tempo."""
    with _lock:
        return {
            log.current_cnpj
            for slot, log in _logs.items()
            if log.running and log.current_cnpj and slot != exclude_slot
        }
