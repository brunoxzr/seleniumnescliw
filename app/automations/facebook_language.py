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


def _set_language(driver, target_text: str, row_label_texts: list[str]) -> None:
    """Abre a tela de idioma/região e seleciona o idioma cujo texto do rádio
    contém `target_text` (ex: 'Português (Brasil)' ou 'English (US)').

    `row_label_texts` são as variações possíveis do texto da linha "Account
    language" que abre o modal — o rótulo da linha muda conforme o idioma
    ATUAL da conta, então passamos as duas formas conhecidas (inglês e
    português) e usamos a primeira que aparecer na tela.
    """
    driver.get(LANGUAGE_URL)
    time.sleep(1)

    account_language_row = _find_visible_by_text(driver, row_label_texts)
    if account_language_row is None:
        raise RuntimeError(
            f"Linha 'Account language' não encontrada na tela de idioma/região "
            f"(tentado: {row_label_texts})."
        )
    _click_js(driver, account_language_row)

    wait = WebDriverWait(driver, 10)
    try:
        target_candidates = wait.until(
            EC.presence_of_all_elements_located((By.XPATH, f"//*[contains(text(),'{target_text}')]"))
        )
    except TimeoutException as e:
        raise RuntimeError(
            "Modal de seleção de idioma não abriu ao clicar em 'Account language'."
        ) from e

    target = [c for c in target_candidates if c.is_displayed()][0]
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


def set_language_pt_br(driver) -> None:
    """Garante que o idioma da conta está definido como Português (Brasil)."""
    _set_language(driver, "Português (Brasil)", ["Account language", "Idioma da conta"])


def set_language_english(driver) -> None:
    """Garante que o idioma da conta está definido como English (US) — usado
    antes da criação do Business Manager, cujo formulário só tem seletores
    estáveis mapeados na versão em inglês da tela."""
    _set_language(driver, "English (US)", ["Account language", "Idioma da conta"])
