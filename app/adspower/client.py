"""Cliente para a AdsPower Local API (app precisa estar aberto)."""
import os

import requests

BASE_URL = "http://local.adspower.net:50325"


class AdsPowerError(Exception):
    pass


def _api_key() -> str:
    key = os.environ.get("ADSPOWER_API_KEY", "")
    if not key:
        raise AdsPowerError(
            "ADSPOWER_API_KEY não configurada. Defina a variável de ambiente com a "
            "chave encontrada em Configurações > Local API no app do AdsPower."
        )
    return key


def _headers() -> dict:
    return {"Authorization": f"Bearer {_api_key()}"}


def start_profile(profile_id: str) -> dict:
    """Abre o perfil pelo AdsPower e retorna dados de conexão do Chrome (ws, debug_port)."""
    resp = requests.get(
        f"{BASE_URL}/api/v1/browser/start",
        params={"user_id": profile_id},
        headers=_headers(),
        timeout=30,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise AdsPowerError(data.get("msg", "Falha ao abrir perfil no AdsPower"))
    return data["data"]


def stop_profile(profile_id: str) -> None:
    resp = requests.get(
        f"{BASE_URL}/api/v1/browser/stop",
        params={"user_id": profile_id},
        headers=_headers(),
        timeout=30,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise AdsPowerError(data.get("msg", "Falha ao fechar perfil no AdsPower"))


def list_profiles(page: int = 1, page_size: int = 100) -> list[dict]:
    """Lista os perfis cadastrados no AdsPower (id, nome, grupo)."""
    resp = requests.get(
        f"{BASE_URL}/api/v1/user/list",
        params={"page": page, "page_size": page_size},
        headers=_headers(),
        timeout=30,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise AdsPowerError(data.get("msg", "Falha ao listar perfis no AdsPower"))
    items = data["data"]["list"]
    return [
        {
            "user_id": item.get("user_id"),
            "name": item.get("name"),
            "group_name": item.get("group_name"),
            "serial_number": item.get("serial_number"),
        }
        for item in items
    ]


def get_active_profile_status(profile_id: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/api/v1/browser/active",
        params={"user_id": profile_id},
        headers=_headers(),
        timeout=30,
    )
    return resp.json()
