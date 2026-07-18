"""Checa a aba Pages do Business Manager: se houver cadeado (página já vinculada/bloqueada),
o processo deve abortar e registrar no log local em vez de continuar."""
import time

from selenium.webdriver.common.by import By

from .facebook_scope import ensure_business_scope


def pages_url(business_id: str) -> str:
    return f"https://business.facebook.com/latest/settings/pages?business_id={business_id}"


def has_locked_page(driver, business_id: str) -> bool:
    """True se existir algum ícone de cadeado na lista de Pages (indicando bloqueio)."""
    ensure_business_scope(driver, business_id)
    driver.get(pages_url(business_id))
    time.sleep(2)

    locked = driver.find_elements(
        By.CSS_SELECTOR, "svg.lucide-lock, [aria-label*='lock' i], [aria-label*='Lock']"
    )
    return len(locked) > 0
