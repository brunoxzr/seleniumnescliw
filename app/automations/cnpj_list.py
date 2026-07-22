"""Lê o cnpjs.csv e cruza com o tracker para mostrar status de cada linha no dashboard."""
import csv
import json
import os
from datetime import datetime, timezone

from .. import paths
from . import run_log, tracker

CSV_PATH = os.path.join(paths.BASE_DIR, "cnpjs.csv")
AVULSOS_PATH = os.path.join(paths.BASE_DIR, "data", "cnpjs_avulsos.json")


def list_cnpjs_with_status(limit: int = 200, exclude_slot: str | None = None) -> list[dict]:
    """CNPJs do cnpjs.csv AINDA DISPONÍVEIS pra seleção — exclui qualquer CNPJ
    que já tenha registro no tracker (em progresso, concluído ou abortado; um
    robô já "usou" ele) e qualquer CNPJ sendo processado agora por outro robô
    (exclude_slot é o slot do próprio dashboard fazendo a consulta, pra ele
    continuar vendo o CNPJ que ELE MESMO está rodando no momento)."""
    if not os.path.exists(CSV_PATH):
        return []

    # se o cnpjs.csv mudou de conteúdo desde a última leitura (nova leva de
    # CNPJs colada), zera o progresso salvo — evita misturar checkpoints da
    # leva antiga com CNPJs que não têm relação nenhuma com eles
    tracker.sync_with_csv(CSV_PATH)

    in_use_elsewhere = run_log.get_cnpjs_in_use(exclude_slot=exclude_slot)

    rows = []
    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            cnpj = (row.get("CNPJ") or "").strip()
            if not cnpj:
                continue
            if cnpj in in_use_elsewhere:
                continue
            record = tracker.get_record(cnpj)
            if record:
                continue  # já usado por algum robô (em progresso, concluído ou abortado)
            rows.append({
                "cnpj": cnpj,
                "razao_social": (row.get("Razao Social") or "").strip(),
                "municipio": (row.get("Municipio") or "").strip(),
                "uf": (row.get("UF") or "").strip(),
                "status": "pendente",
            })
            if len(rows) >= limit:
                break
    return rows


def _load_avulsos() -> list[dict]:
    if not os.path.exists(AVULSOS_PATH):
        return []
    with open(AVULSOS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_avulsos(items: list[dict]) -> None:
    os.makedirs(os.path.dirname(AVULSOS_PATH), exist_ok=True)
    with open(AVULSOS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def add_avulso(cnpj: str) -> None:
    """Registra um CNPJ avulso na lista persistente — mantém o histórico de
    CNPJs digitados manualmente (fora do cnpjs.csv) para reaparecerem na
    lista do dashboard mesmo depois de já terem sido processados."""
    items = _load_avulsos()
    if any(item["cnpj"] == cnpj for item in items):
        return
    items.append({"cnpj": cnpj, "added_at": datetime.now(timezone.utc).isoformat()})
    _save_avulsos(items)


def list_avulsos_with_status(exclude_slot: str | None = None) -> list[dict]:
    """Lista de CNPJs avulsos com status atual — ao contrário da lista
    principal (list_cnpjs_with_status), NÃO some da lista quando já
    processado: o usuário pediu para continuar vendo os avulsos que já
    adicionou, com o status atualizado, em vez de desaparecerem."""
    in_use_elsewhere = run_log.get_cnpjs_in_use(exclude_slot=exclude_slot)
    rows = []
    for item in _load_avulsos():
        cnpj = item["cnpj"]
        record = tracker.get_record(cnpj)
        if cnpj in in_use_elsewhere:
            status = "em_uso"
        else:
            status = (record.get("status") if record else None) or "pendente"
        rows.append({
            "cnpj": cnpj,
            "razao_social": (record or {}).get("data", {}).get("empresa", ""),
            "status": status,
        })
    return rows


def get_cnpj_row(cnpj: str) -> dict | None:
    """Busca uma linha específica do CSV pelo CNPJ (usada para criar site novo no Buildfy)."""
    if not os.path.exists(CSV_PATH):
        return None

    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            row_cnpj = (row.get("CNPJ") or "").strip()
            if row_cnpj == cnpj:
                return {
                    "cnpj": row_cnpj,
                    "razao_social": (row.get("Razao Social") or "").strip(),
                    "email": (row.get("E-mail") or "").strip(),
                }
    return None
