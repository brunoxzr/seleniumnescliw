"""Preenche Business Info (Legal name, endereço, CNPJ, website) no Business Manager."""
import time

from selenium.webdriver.common.by import By

from .facebook_scope import ensure_business_scope


def _cdp_fill(driver, element, text: str, retries: int = 3) -> None:
    for attempt in range(retries):
        driver.execute_script("arguments[0].focus(); arguments[0].value='';", element)
        driver.execute_cdp_cmd("Input.insertText", {"text": text})
        time.sleep(0.2)
        # confirma que o valor de fato colou — o React pode re-renderizar o
        # formulário (validação assíncrona, autocomplete do Facebook) e limpar
        # o campo depois do insertText; sem essa checagem o campo fica vazio
        # silenciosamente e só se percebe no submit.
        if element.get_attribute("value") == text:
            return
        time.sleep(0.3)
    raise RuntimeError(f"Não foi possível preencher o campo com '{text}' após {retries} tentativas.")


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


def _cdp_click(driver, element) -> None:
    """Clique de mouse real via Chrome DevTools Protocol — cliques sintéticos
    (execute_script .click()) se mostram instáveis em vários componentes React
    do Facebook (handler não dispara de forma confiável); o CDP simula um
    clique de mouse de fato (isTrusted=true)."""
    rect = driver.execute_script(
        """
        const el = arguments[0];
        el.scrollIntoView({block: 'center', inline: 'center'});
        const r = el.getBoundingClientRect();
        return {left: r.left, top: r.top, width: r.width, height: r.height};
        """,
        element,
    )
    cx = rect["left"] + rect["width"] / 2
    cy = rect["top"] + rect["height"] / 2
    driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
    driver.execute_cdp_cmd(
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1},
    )
    driver.execute_cdp_cmd(
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1},
    )


def _find_country_combobox(driver, retries: int = 6):
    """O campo 'Country' é um combobox customizado (não necessariamente um
    <input> com aria-labelledby como os outros campos) — localiza pelo label
    de texto 'Country' e sobe até o elemento clicável mais próximo (role
    combobox/button, ou o próprio container do dropdown)."""
    for _ in range(retries):
        labels = [
            el for el in driver.find_elements(By.XPATH, "//*[normalize-space(text())='Country']")
            if el.is_displayed()
        ]
        for label in labels:
            candidates = label.find_elements(
                By.XPATH,
                "./following::*[@role='combobox' or @role='button' or self::input][1]",
            )
            if candidates:
                return candidates[0]
        time.sleep(0.3)
    raise RuntimeError("Combobox 'Country' não encontrado.")


def fill_country(driver, country: str = "Brazil", search_text: str = "bra", retries: int = 3) -> None:
    """Clica no combobox 'Country', digita um termo de busca e seleciona a opção
    correspondente da lista — o campo não aceita um valor de texto direto (é um
    dropdown de busca), diferente dos outros campos do formulário. Sem selecionar
    um país aqui, o Facebook rejeita o Business phone number com 'Please enter a
    valid phone number' (o telefone é validado contra o código do país)."""
    for attempt in range(retries):
        combobox = _find_country_combobox(driver)
        _cdp_click(driver, combobox)
        time.sleep(0.3)

        # depois do clique, o Facebook troca o combobox por um input de busca —
        # relocaliza o campo (o elemento anterior pode não ser mais o input certo)
        search_input = None
        for _ in range(8):
            candidates = [
                i for i in driver.find_elements(By.TAG_NAME, "input")
                if i.is_displayed() and i.get_attribute("role") != "switch"
            ]
            if candidates:
                search_input = candidates[-1]
                break
            time.sleep(0.3)
        if search_input is None:
            time.sleep(0.5)
            continue

        driver.execute_script("arguments[0].focus();", search_input)
        driver.execute_cdp_cmd("Input.insertText", {"text": search_text})
        time.sleep(0.5)

        option = None
        for _ in range(8):
            # o texto "Brazil" pode vir fragmentado entre nós (ex: <span>Bra</span>zil,
            # onde o trecho digitado na busca fica destacado num <span> filho e o
            # resto é texto solto do elemento pai) — contains(text(), ...) só olha
            # nós de texto diretos e não acha nada nesse caso. contains(., ...) usa
            # o texto concatenado de todo o subtree, então casa corretamente.
            candidates = [
                el for el in driver.find_elements(
                    By.XPATH,
                    f"//*[@role='option' or @role='menuitem' or @role='button'][contains(., '{country}')]",
                )
                if el.is_displayed()
            ]
            if not candidates:
                # fallback: nem todo item de lista tem role explícito — pega o
                # elemento mais específico (menos descendentes) que contém o
                # texto completo, evitando clicar num ancestral grande demais
                # (ex: o próprio container da lista inteira).
                all_matches = [
                    el for el in driver.find_elements(By.XPATH, f"//*[contains(., '{country}')]")
                    if el.is_displayed()
                ]
                if all_matches:
                    candidates = [min(
                        all_matches,
                        key=lambda el: len(el.find_elements(By.XPATH, ".//*")),
                    )]
            if candidates:
                option = candidates[0]
                break
            time.sleep(0.3)
        if option is None:
            time.sleep(0.5)
            continue

        _cdp_click(driver, option)
        time.sleep(0.5)

        # confirma que a seleção de fato colou — a lista de busca deve fechar
        # (o combobox volta a mostrar "Brazil" selecionado, não mais o input
        # de busca) antes de seguir para os próximos campos.
        remaining_search_inputs = [
            i for i in driver.find_elements(By.TAG_NAME, "input")
            if i.is_displayed() and (i.get_attribute("value") or "") == search_text
        ]
        if not remaining_search_inputs:
            return
        time.sleep(0.5)

    raise RuntimeError(f"Não foi possível selecionar '{country}' no campo Country após {retries} tentativas.")


def fill_business_details(driver, legal_name: str, street_address: str, bairro: str,
                           city: str, state: str, zip_code: str, tax_id: str, website: str,
                           check_pause=None) -> None:
    def _checked_fill(label_substr: str, value: str) -> None:
        if check_pause:
            check_pause()
        _cdp_fill(driver, _field_by_label(driver, label_substr), value)

    _checked_fill("Legal name", legal_name)
    if check_pause:
        check_pause()
    fill_country(driver)
    _checked_fill("Street address", street_address)
    if bairro:
        if check_pause:
            check_pause()
        # o label exato varia (Street address 2 / Address line 2 / Apartment, suite, etc.)
        _cdp_fill(
            driver,
            _field_by_any_label(driver, ["Street address 2", "Address line 2", "Apartment, suite"]),
            bairro,
        )
    _checked_fill("City", city)
    _checked_fill("State", state)
    _checked_fill("Zip", zip_code)
    _checked_fill("Tax ID", tax_id)
    _checked_fill("Business website", website)


def get_phone_field(driver):
    return _field_by_label(driver, "Business phone")


def fill_phone(driver, phone: str = "11999999999") -> None:
    _cdp_fill(driver, get_phone_field(driver), phone)


def submit_business_details(driver) -> None:
    # pequeno retry: o modal pode demorar a re-renderizar logo após o preenchimento
    # dos campos, então uma checagem única e imediata é instável demais — dá falso
    # negativo mesmo quando o modal ainda está lá.
    dialogs = []
    for _ in range(6):
        dialogs = driver.find_elements(By.CSS_SELECTOR, "[role='dialog']")
        if dialogs:
            break
        time.sleep(0.5)

    if not dialogs:
        # sem pausa manual no fluxo atual, o modal só desaparece sozinho se algo
        # deu errado (ex: navegação/refresh inesperado) — não há mais um "usuário
        # clicou Save manualmente" que justifique assumir sucesso aqui.
        raise RuntimeError(
            "O modal de Business Info não está mais na tela antes de clicar 'Save' "
            "— pode ter fechado sozinho antes da hora."
        )

    save_candidates = dialogs[0].find_elements(
        By.XPATH, ".//div[@role='button' or self::button][contains(., 'Save')]"
    )
    if not save_candidates:
        raise RuntimeError("Botão 'Save' não encontrado no modal de Business Info.")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", save_candidates[0])

    # só considera salvo se o modal de fato fechar — se o Facebook rejeitar algum
    # campo (validação, formato de telefone), o modal continua aberto e o
    # checkpoint não deve ser marcado como concluído
    for _ in range(10):
        time.sleep(0.5)
        if not driver.find_elements(By.CSS_SELECTOR, "[role='dialog']"):
            return
    raise RuntimeError(
        "O modal de Business Info não fechou após clicar 'Save' — o Facebook pode "
        "ter rejeitado algum campo (ex: formato de telefone inválido)."
    )
