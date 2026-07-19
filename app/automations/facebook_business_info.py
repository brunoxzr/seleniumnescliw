"""Preenche Business Info (Legal name, endereço, CNPJ, website) no Business Manager."""
import time

from selenium.webdriver.common.by import By

from .facebook_scope import ensure_business_scope


def _cdp_fill(driver, element, text: str) -> None:
    driver.execute_script("arguments[0].focus(); arguments[0].value='';", element)
    driver.execute_cdp_cmd("Input.insertText", {"text": text})
    time.sleep(0.2)


def _field_by_label(driver, label_substr: str, retries: int = 6):
    # pequeno retry: o modal pode ainda estar renderizando os campos (React) no
    # instante em que a busca roda, especialmente logo após abrir o modal
    for attempt in range(retries):
        text_inputs = [
            i for i in driver.find_elements(By.TAG_NAME, "input")
            if i.get_attribute("role") != "switch"
        ]
        for i in text_inputs:
            labelledby = i.get_attribute("aria-labelledby")
            if not labelledby:
                continue
            try:
                label_text = driver.find_element(By.ID, labelledby).text
            except Exception:
                continue
            if label_substr.lower() in label_text.lower():
                return i
        time.sleep(0.4)
    raise RuntimeError(f"Campo com label '{label_substr}' não encontrado")


def info_url(business_id: str) -> str:
    return f"https://business.facebook.com/settings/info?business_id={business_id}"


def open_edit_business_details(driver, business_id: str) -> None:
    ensure_business_scope(driver, business_id)
    driver.get(info_url(business_id))
    time.sleep(2)

    # a página tem um "Edit" por linha de perfil/admin, além do "Edit" da seção
    # "Business details" — por isso não dá para confiar num índice fixo (o número
    # de perfis listados varia). Localiza o "Edit" pela proximidade com o
    # cabeçalho "Business details" em vez de por posição.
    edit_btn = None
    for _ in range(8):
        headers = [
            h for h in driver.find_elements(By.XPATH, "//*[contains(text(),'Business details')]")
            if h.is_displayed()
        ]
        if headers:
            section = headers[0].find_elements(
                By.XPATH, "./ancestor::div[.//div[@role='button' or self::button][contains(., 'Edit')]][1]"
            )
            if section:
                candidates = section[0].find_elements(
                    By.XPATH, ".//div[@role='button' or self::button][contains(., 'Edit')]"
                )
                if candidates:
                    edit_btn = candidates[0]
                    break
        time.sleep(0.4)

    if edit_btn is None:
        raise RuntimeError("Botão 'Edit' de 'Business details' não encontrado na página de Info.")

    driver.execute_script("arguments[0].click();", edit_btn)

    # espera ativa pelo modal renderizar de fato, em vez de sleep fixo
    for _ in range(8):
        if driver.find_elements(By.CSS_SELECTOR, "[role='dialog']"):
            break
        time.sleep(0.3)


def _field_by_any_label(driver, label_substrs: list[str]):
    last_error = None
    for substr in label_substrs:
        try:
            return _field_by_label(driver, substr)
        except RuntimeError as e:
            last_error = e
    raise last_error


def fill_business_details(driver, legal_name: str, street_address: str, bairro: str,
                           city: str, state: str, zip_code: str, tax_id: str, website: str) -> None:
    _cdp_fill(driver, _field_by_label(driver, "Legal name"), legal_name)
    _cdp_fill(driver, _field_by_label(driver, "Street address"), street_address)
    if bairro:
        # o label exato varia (Street address 2 / Address line 2 / Apartment, suite, etc.)
        _cdp_fill(
            driver,
            _field_by_any_label(driver, ["Street address 2", "Address line 2", "Apartment, suite"]),
            bairro,
        )
    _cdp_fill(driver, _field_by_label(driver, "City"), city)
    _cdp_fill(driver, _field_by_label(driver, "State"), state)
    _cdp_fill(driver, _field_by_label(driver, "Zip"), zip_code)
    _cdp_fill(driver, _field_by_label(driver, "Tax ID"), tax_id)
    _cdp_fill(driver, _field_by_label(driver, "Business website"), website)


def get_phone_field(driver):
    return _field_by_label(driver, "Business phone")


def fill_business_phone(driver, phone_without_ddi: str) -> None:
    """Preenche o campo 'Business phone number' com um telefone brasileiro
    (sem DDI, ex: 11977411205). O campo já vem com o seletor de país fixo em
    Brasil (+55) por padrão nessa tela, então só o número local é digitado."""
    field = get_phone_field(driver)
    _cdp_fill(driver, field, phone_without_ddi)


def submit_business_details(driver) -> None:
    # pequeno retry: o modal pode demorar a re-renderizar logo após a pausa manual
    # (usuário confirmando o telefone), então uma checagem única e imediata é
    # instável demais — dá falso negativo mesmo quando o modal ainda está lá.
    dialogs = []
    for _ in range(6):
        dialogs = driver.find_elements(By.CSS_SELECTOR, "[role='dialog']")
        if dialogs:
            break
        time.sleep(0.5)

    if not dialogs:
        # o modal pode ter sido fechado porque o usuário já clicou "Save" manualmente
        # durante a pausa (telefone + salvar de uma vez) — isso não é erro, é um
        # atalho válido; segue em frente em vez de travar o fluxo pedindo retry.
        return

    save_candidates = dialogs[0].find_elements(
        By.XPATH, ".//div[@role='button' or self::button][contains(., 'Save')]"
    )
    if not save_candidates:
        raise RuntimeError("Botão 'Save' não encontrado no modal de Business Info.")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", save_candidates[0])
    time.sleep(1.5)
