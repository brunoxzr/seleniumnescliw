"""Extrai credenciais do Facebook armazenadas no campo 'remark' de um perfil AdsPower.

Formato observado do remark: username:password:email:password:totp_secret:access_token:cookies_b64

Alguns perfis têm o remark em ordens diferentes (ex: totp_secret na posição 3
em vez de 5). Em vez de confiar cegamente na posição fixa, o totp_secret é
identificado pelo FORMATO do valor (Base32-like: só A-Z/2-7, sem @, sem
prefixo de token tipo "RefreshToken="), com a posição padrão como primeira
tentativa e uma busca por conteúdo em todos os campos como fallback.

Um segundo modelo de remark usa '|' como separador, em ordem diferente:
username|password|totp_secret|email|password2|email_recovery
(ex: telefone|senha|BASE32SECRET|email@provedor.com|senha2|email_recuperacao).
O e-mail principal aqui é identificado pelo formato (contém '@'), assim como
o totp_secret — não dá pra usar posição fixa entre os dois modelos.
"""
import re
from dataclasses import dataclass

import requests

from .client import BASE_URL, AdsPowerError, _headers

# TOTP secrets Base32 usam só A-Z e 2-7 (RFC 4648), tipicamente 16-32 chars.
# Único o bastante para não colidir com e-mail (tem @), senha (costuma ter
# minúsculas/símbolos) ou tokens longos (têm "=", "*", ou prefixo tipo "Token=").
_TOTP_SECRET_RE = re.compile(r"^[A-Z2-7]{16,32}$")


@dataclass
class FacebookCredentials:
    username: str
    password: str
    email: str
    totp_secret: str
    access_token: str = ""


def _looks_like_totp_secret(value: str) -> bool:
    return bool(_TOTP_SECRET_RE.match(value))


def _find_totp_secret(parts: list[str], positional_guess: str) -> str:
    """Usa o valor na posição padrão (índice 4) se ele de fato parecer um TOTP
    secret válido; senão, procura em todos os campos por um que bata com o
    formato — cobre perfis com o remark em ordem diferente do padrão."""
    if _looks_like_totp_secret(positional_guess):
        return positional_guess

    candidates = [p for p in parts if _looks_like_totp_secret(p)]
    if candidates:
        return candidates[0]

    # nenhum campo bate com o formato esperado — mantém o palpite posicional
    # como último recurso (comportamento anterior), o chamador vê o erro do
    # Facebook se estiver errado mesmo assim
    return positional_guess


def get_facebook_credentials(profile_id: str) -> FacebookCredentials:
    resp = requests.get(
        f"{BASE_URL}/api/v1/user/list",
        params={"user_id": profile_id},
        headers=_headers(),
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise AdsPowerError(data.get("msg", "Falha ao buscar dados do perfil"))

    items = data["data"]["list"]
    if not items:
        raise AdsPowerError(f"Perfil {profile_id} não encontrado")

    remark = items[0].get("remark", "")

    # o modelo com '|' (username|password|totp_secret|email|password2|email_recovery)
    # usa outra ordem e outro separador do modelo original com ':' — detecta pelo
    # separador presente no remark, já que os dois nunca se misturam
    if "|" in remark:
        parts = remark.split("|")
        if len(parts) < 4:
            raise AdsPowerError(
                f"Remark do perfil {profile_id} não está no formato esperado "
                "(username|password|totp_secret|email|password2|email_recovery)"
            )
        username, password = parts[0], parts[1]
        totp_secret = _find_totp_secret(parts, parts[2])
        email_candidates = [p for p in parts if "@" in p]
        if not email_candidates:
            raise AdsPowerError(f"Remark do perfil {profile_id} não tem nenhum campo de e-mail reconhecível.")
        email = email_candidates[0]
        access_token = ""
        return FacebookCredentials(
            username=username,
            password=password,
            email=email,
            totp_secret=totp_secret,
            access_token=access_token,
        )

    parts = remark.split(":")
    if len(parts) < 5:
        raise AdsPowerError(f"Remark do perfil {profile_id} não está no formato esperado (username:password:email:password:totp_secret:...)")

    username, password, email = parts[0], parts[1], parts[2]
    totp_secret = _find_totp_secret(parts, parts[4])
    access_token = parts[5] if len(parts) > 5 else ""

    return FacebookCredentials(
        username=username,
        password=password,
        email=email,
        totp_secret=totp_secret,
        access_token=access_token,
    )
