"""Lê o cnpjs.csv e cruza com o tracker para mostrar status de cada linha no dashboard."""
import csv
import os

from . import tracker

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cnpjs.csv")


def list_cnpjs_with_status(limit: int = 200) -> list[dict]:
    if not os.path.exists(CSV_PATH):
        return []

    rows = []
    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            cnpj = (row.get("CNPJ") or "").strip()
            if not cnpj:
                continue
            record = tracker.get_record(cnpj)
            status = record.get("status") or "pendente" if record else "pendente"
            rows.append({
                "cnpj": cnpj,
                "razao_social": (row.get("Razao Social") or "").strip(),
                "municipio": (row.get("Municipio") or "").strip(),
                "uf": (row.get("UF") or "").strip(),
                "status": status,
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
