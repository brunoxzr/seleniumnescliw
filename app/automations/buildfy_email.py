"""Confirma o e-mail comercial do Business Manager via link recebido na caixa de
entrada específica do site no Buildfy (aba Emails).

O Facebook envia um e-mail "Confirm your business email" com um link de
verificação (business.facebook.com/verify/email/checkpoint/?token=...) dentro de
um <iframe> que renderiza o corpo do e-mail. Esse mesmo e-mail também contém o
business_id do BM recém-criado, útil como confirmação cruzada.
"""
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import buildfy


def emails_tab_url(site_id: str) -> str:
    return f"{buildfy.BASE_URL}/sites/{site_id}?tab=emails"


def find_confirmation_link(driver, site_id: str, max_attempts: int = 5, wait_between: float = 5.0):
    """Procura o e-mail 'Confirm your business email' na caixa do site e retorna
    (confirm_url, business_id_no_email) — business_id pode ser None se não encontrado.

    O e-mail pode demorar para chegar; tenta várias vezes com espera entre elas.
    """
    for attempt in range(max_attempts):
        driver.get(emails_tab_url(site_id))
        time.sleep(1.5)

        email_items = driver.find_elements(By.XPATH, "//*[contains(text(),'Confirm your business email')]")
        if not email_items:
            time.sleep(wait_between)
            continue

        driver.execute_script("arguments[0].click();", email_items[0])
        time.sleep(1)

        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        if not iframes:
            time.sleep(wait_between)
            continue

        driver.switch_to.frame(iframes[0])
        try:
            links = driver.find_elements(By.TAG_NAME, "a")
            confirm_url = None
            business_id = None
            for link in links:
                href = link.get_attribute("href") or ""
                if "verify/email/checkpoint" in href:
                    confirm_url = href
                match = re.search(r"business_id=(\d+)", href)
                if match:
                    business_id = match.group(1)
            if confirm_url:
                return confirm_url, business_id
        finally:
            driver.switch_to.default_content()

        time.sleep(wait_between)

    raise RuntimeError(
        f"E-mail de confirmação do Facebook não chegou na caixa de entrada do site {site_id} "
        f"após {max_attempts} tentativas."
    )


def confirm_business_email(driver, site_id: str) -> str | None:
    """Localiza o link de confirmação, abre em nova aba, confirma, e retorna o
    business_id extraído do e-mail (se disponível) para servir de checagem cruzada."""
    confirm_url, business_id = find_confirmation_link(driver, site_id)

    original_window = driver.current_window_handle
    driver.switch_to.new_window("tab")
    try:
        driver.get(confirm_url)
        time.sleep(1.5)
    finally:
        driver.close()
        driver.switch_to.window(original_window)

    return business_id
