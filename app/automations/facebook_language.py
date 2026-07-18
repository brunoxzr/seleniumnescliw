"""Muda o idioma da conta do Facebook para Português (Brasil).

Necessário antes da etapa de criação da conta WhatsApp: o fluxo de verificação
por telefone/captcha do WhatsApp se comporta melhor (ou é exigido pelo usuário)
com a conta em pt-BR.

Fluxo real (mapeado pelo usuário): a página /settings/?tab=language_and_region
mostra uma linha "Account language" que precisa ser clicada primeiro — isso abre
um modal separado com a lista de idiomas em rádio buttons, onde então se
seleciona "Português (Brasil)".
"""
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

LANGUAGE_URL = "https://www.facebook.com/settings/?tab=language_and_region"


def _find_visible_by_text(driver, texts: list[str]):
    for xpath_text in texts:
        candidates = [
            el for el in driver.find_elements(By.XPATH, f"//*[contains(text(),'{xpath_text}')]")
            if el.is_displayed()
        ]
        if candidates:
            return candidates[0]
    return None


def _click_js(driver, element) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", element)


def set_language_pt_br(driver) -> None:
    """Garante que o idioma da conta está definido como Português (Brasil)."""
    driver.get(LANGUAGE_URL)
    time.sleep(1)

    account_language_row = _find_visible_by_text(driver, ["Account language"])
    if account_language_row is None:
        raise RuntimeError("Linha 'Account language' não encontrada na tela de idioma/região.")
    _click_js(driver, account_language_row)

    wait = WebDriverWait(driver, 10)
    try:
        pt_br_candidates = wait.until(
            EC.presence_of_all_elements_located((By.XPATH, "//*[contains(text(),'Português (Brasil)')]"))
        )
    except TimeoutException as e:
        raise RuntimeError(
            "Modal de seleção de idioma não abriu ao clicar em 'Account language'."
        ) from e

    target = [c for c in pt_br_candidates if c.is_displayed()][0]
    row = target.find_elements(
        By.XPATH,
        "./ancestor::label[1] | ./ancestor::div[.//input[@type='radio'] or @role='radio'][1]",
    )
    click_target = row[0] if row else target

    radio = click_target.find_elements(By.CSS_SELECTOR, "input[type='radio']")
    if radio and radio[0].get_attribute("checked"):
        return  # já está selecionado

    _click_js(driver, radio[0] if radio else click_target)
    time.sleep(1)
