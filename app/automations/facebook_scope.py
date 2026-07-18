"""Garante que o Business Manager correto está 'ativo' na sessão antes de navegar
para telas de Settings específicas.

Descoberta: o Facebook ignora o parâmetro business_id em URLs de /settings/* se o
portfólio ativo da sessão for outro (definido pela última navegação ou seleção manual
na interface) — ele redireciona silenciosamente para o portfólio errado. Navegar
primeiro para /latest/business_home?business_id=X "define o escopo" corretamente
antes de ir para qualquer /settings/*.
"""
import time

from .driver_utils import safe_url

_last_scoped_business_id: str | None = None


def close_extra_tabs(driver) -> None:
    """Fecha todas as abas exceto a ativa. Abas extras (de navegação anterior,
    target="_blank" que não foi tratado, etc.) confundem o Selenium — ele
    continua mandando comandos para o driver mas o navegador pode trocar o foco
    visual para outra aba, fazendo a automação clicar/preencher na tela errada."""
    handles = driver.window_handles
    if len(handles) <= 1:
        return
    current = driver.current_window_handle
    for handle in handles:
        if handle != current:
            driver.switch_to.window(handle)
            driver.close()
    driver.switch_to.window(current)


def ensure_business_scope(driver, business_id: str) -> None:
    global _last_scoped_business_id
    close_extra_tabs(driver)

    if _last_scoped_business_id == business_id:
        return  # já é o escopo ativo, evita navegação redundante

    home_url = f"https://business.facebook.com/latest/business_home?business_id={business_id}"
    driver.get(home_url)

    # espera ativa em vez de sleep fixo — sai assim que a URL bater com o
    # business_id esperado, em vez de sempre aguardar o tempo máximo
    deadline = time.time() + 6
    url = safe_url(driver)
    while time.time() < deadline and f"business_id={business_id}" not in url:
        time.sleep(0.3)
        url = safe_url(driver)

    if f"business_id={business_id}" not in url:
        raise RuntimeError(
            f"Não foi possível definir o escopo para business_id={business_id} "
            f"(URL ficou: {url})"
        )

    _last_scoped_business_id = business_id
