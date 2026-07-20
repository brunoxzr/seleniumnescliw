"""Lê o cnpjs.csv e cruza com o tracker para mostrar status de cada linha no dashboard."""
import csv
import os

from .. import paths
from . import run_log, tracker

CSV_PATH = os.path.join(paths.BASE_DIR, "cnpjs.csv")


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
