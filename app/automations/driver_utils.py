"""Helpers defensivos para interações comuns com o driver Selenium."""
import time


def safe_url(driver, retries: int = 3, delay: float = 0.3) -> str:
    """driver.current_url pode retornar None ou lançar durante uma navegação em
    andamento (ex: troca de página, redirecionamento). Nunca deve travar o fluxo
    com um TypeError/AttributeError — sempre retorna string, tentando de novo
    algumas vezes antes de desistir e retornar vazio."""
    for _ in range(retries):
        try:
            url = driver.current_url
        except Exception:
            url = None
        if url:
            return url
        time.sleep(delay)
    return ""
