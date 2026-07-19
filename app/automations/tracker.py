"""Controle local de progresso por CNPJ no fluxo Facebook Business, com checkpoints por etapa."""
import hashlib
import json
import os
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
PROCESSED_FILE = os.path.join(DATA_DIR, "processed_cnpjs.json")
CSV_HASH_FILE = os.path.join(DATA_DIR, "cnpjs_csv.hash")

# ordem das etapas do fluxo — usada para saber onde retomar
STEPS = [
    "site_data_obtida",
    "business_manager_criado",
    "email_confirmado",
    "pages_ok",
    "dominio_adicionado",
    "meta_tag_aplicada",
    "dominio_verificado",
    "business_info_preenchido",
    "idioma_pt_br",
    "whatsapp_categoria_preenchida",
    "whatsapp_concluido",
    "verificacao_negocio_iniciada",
    "concluido",
]


def _load() -> dict:
    if not os.path.exists(PROCESSED_FILE):
        return {}
    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_processed(cnpj: str) -> bool:
    record = _load().get(cnpj)
    return bool(record) and record.get("status") == "concluido"


def get_all_processed_cnpjs() -> set:
    """CNPJs com status final 'concluido' — usados para não repetir na fila de pendentes."""
    return {cnpj for cnpj, r in _load().items() if r.get("status") == "concluido"}


def get_record(cnpj: str) -> dict | None:
    return _load().get(cnpj)


def get_in_progress_cnpj() -> str | None:
    """Retorna o CNPJ com progresso salvo mas ainda não concluído nem abortado (para retomar)."""
    for cnpj, record in _load().items():
        status = record.get("status")
        if status and status not in ("concluido", "abortado"):
            return cnpj
    return None


def save_checkpoint(cnpj: str, step: str, data: dict | None = None) -> None:
    """Marca uma etapa como concluída para o CNPJ, guardando dados acumulados (business_id, site_id, etc.)."""
    all_data = _load()
    record = all_data.get(cnpj, {"steps_done": [], "data": {}})
    if step not in record["steps_done"]:
        record["steps_done"].append(step)
    if data:
        record["data"].update(data)
    record["status"] = step
    record["timestamp"] = datetime.now(timezone.utc).isoformat()
    all_data[cnpj] = record
    _save(all_data)


def mark_processed(cnpj: str, status: str, details: str = "") -> None:
    """Compatibilidade: marca status final (ex: 'concluido', 'abortado')."""
    all_data = _load()
    record = all_data.get(cnpj, {"steps_done": [], "data": {}})
    record["status"] = status
    record["details"] = details
    record["timestamp"] = datetime.now(timezone.utc).isoformat()
    all_data[cnpj] = record
    _save(all_data)


def get_status(cnpj: str) -> dict | None:
    return _load().get(cnpj)


def set_profile_id(cnpj: str, profile_id: str) -> None:
    """Associa o perfil AdsPower usado para processar este CNPJ — permite o
    dashboard selecionar automaticamente o CNPJ certo ao trocar de perfil."""
    all_data = _load()
    record = all_data.get(cnpj, {"steps_done": [], "data": {}})
    record["profile_id"] = profile_id
    all_data[cnpj] = record
    _save(all_data)


def get_cnpj_by_profile_id(profile_id: str) -> str | None:
    """Retorna o CNPJ mais recente associado a um perfil AdsPower (prioriza o
    que está em andamento; senão, o mais recentemente atualizado)."""
    matches = [
        (cnpj, record) for cnpj, record in _load().items()
        if record.get("profile_id") == profile_id
    ]
    if not matches:
        return None
    in_progress = [
        (cnpj, r) for cnpj, r in matches if r.get("status") not in ("concluido", "abortado")
    ]
    pool = in_progress or matches
    pool.sort(key=lambda item: item[1].get("timestamp", ""), reverse=True)
    return pool[0][0]


def reset_all() -> None:
    """Apaga todo o progresso salvo — usado quando o cnpjs.csv muda (lista nova
    de empresas, então o histórico antigo deixa de fazer sentido)."""
    _save({})


def _csv_content_hash(csv_path: str) -> str | None:
    if not os.path.exists(csv_path):
        return None
    with open(csv_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sync_with_csv(csv_path: str) -> bool:
    """Compara o conteúdo atual do cnpjs.csv com o hash salvo da última vez que
    foi lido; se mudou (nova lista de CNPJs colada/substituída), reseta todo o
    progresso salvo — evita misturar checkpoints de uma leva antiga de CNPJs
    com uma nova. Retorna True se resetou."""
    current_hash = _csv_content_hash(csv_path)
    if current_hash is None:
        return False

    os.makedirs(DATA_DIR, exist_ok=True)
    previous_hash = None
    if os.path.exists(CSV_HASH_FILE):
        with open(CSV_HASH_FILE, "r", encoding="utf-8") as f:
            previous_hash = f.read().strip()

    if previous_hash is not None and previous_hash != current_hash:
        reset_all()
        with open(CSV_HASH_FILE, "w", encoding="utf-8") as f:
            f.write(current_hash)
        return True

    if previous_hash is None:
        with open(CSV_HASH_FILE, "w", encoding="utf-8") as f:
            f.write(current_hash)
    return False
