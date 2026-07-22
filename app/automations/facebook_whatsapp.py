"""Cria conta WhatsApp Business no Business Manager, categoria 'Other', até o captcha
(que precisa ser resolvido manualmente).

Wizard tem 3 etapas: Details (nome + categoria) -> Phone number -> Phone verification.
Este módulo preenche a categoria (etapa Details) e para ali; o restante (telefone,
verificação, captcha) é responsabilidade do usuário via pausa manual no orquestrador.
"""
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .facebook_scope import ensure_business_scope


def whatsapp_url(business_id: str) -> str:
    return f"https://business.facebook.com/latest/settings/whatsapp_account?business_id={business_id}"


def _cdp_click(driver, element) -> None:
    """Clique de mouse real via Chrome DevTools Protocol — mais confiável que
    clique sintético (execute_script .click()) para botões cujo handler React
    exige um evento de mouse confiável (isTrusted=true). Mesma técnica usada
    em create_business_manager.py e facebook_login.py."""
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


def _find_visible_by_text(driver, texts: list[str]):
    """Busca o primeiro elemento visível cujo texto contenha qualquer uma das
    strings dadas (cobre PT-BR e EN, já que o idioma da conta pode variar)."""
    for xpath_text in texts:
        candidates = [
            el for el in driver.find_elements(By.XPATH, f"//*[contains(text(),'{xpath_text}')]")
            if el.is_displayed()
        ]
        if candidates:
            return candidates[0]
    return None


def start_create_whatsapp_account(driver, business_id: str) -> None:
    """Abre o wizard de criação de conta WhatsApp Business (até a tela de Details)."""
    ensure_business_scope(driver, business_id)
    driver.get(whatsapp_url(business_id))
    time.sleep(2)

    create_opt_texts = [
        "Crie uma nova conta do WhatsApp Business",
        "Criar uma nova conta do WhatsApp Business",
        "Create a new WhatsApp Business account",
    ]

    # clique via JS puro (sem human_click/wander_mouse) — o menu suspenso do botão
    # "Adicionar" some se o mouse real se mover para longe dele antes do clique,
    # então o clique humanizado estava abrindo e fechando o menu sem deixar rastro
    # de erro (o clique "funcionava" mas o menu já tinha sumido quando procurávamos
    # a opção seguinte).
    create_opt = None
    for attempt in range(4):
        create_opt = _find_visible_by_text(driver, create_opt_texts)
        if create_opt:
            break

        add_btns = [
            b for b in driver.find_elements(
                By.XPATH,
                "//div[@role='button' or self::button][contains(., 'Add') or contains(., 'Adicionar')]",
            )
            if b.is_displayed()
        ]
        if not add_btns:
            raise RuntimeError("Botão 'Adicionar/Add' de contas WhatsApp não encontrado — verifique manualmente.")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", add_btns[0])
        time.sleep(0.8)

    if create_opt is None:
        raise RuntimeError(
            "Opção 'Criar uma nova conta do WhatsApp Business' não encontrada — verifique manualmente."
        )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", create_opt)
    time.sleep(1.5)


_OTHER_CATEGORY_TEXTS = ("other", "outro", "outra")


def _find_other_option(listbox_el):
    for o in listbox_el.find_elements(By.CSS_SELECTOR, "[role='option']"):
        text = (o.get_attribute("textContent") or "").strip().lower()
        if text in _OTHER_CATEGORY_TEXTS:
            return o
    return None


def select_category_other(driver, max_attempts: int = 3) -> None:
    """Seleciona a categoria 'Other'/'Outro' no wizard de criação da conta
    WhatsApp.

    O display name já vem preenchido por padrão com o nome da página conectada,
    não precisa ser digitado. A categoria é um combobox customizado (div
    role=combobox); a lista de opções é virtualizada (scrollbar visível na
    tela) — 'Outro' geralmente é uma das últimas opções e só entra no DOM
    conforme a lista é rolada, então clicar em 'Mostrar mais opções' sozinho
    não é suficiente: é preciso rolar o listbox incrementalmente até o
    elemento aparecer.
    """
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[role='combobox']")))

    show_more = _find_visible_by_text(driver, ["Mostrar mais opções", "Show more options"])
    if show_more:
        driver.execute_script("arguments[0].click();", show_more)
        time.sleep(1)

    comboboxes = driver.find_elements(By.CSS_SELECTOR, "[role='combobox']")
    # o segundo combobox da tela é o de Category (o primeiro é o display name)
    category_field = comboboxes[1]

    for attempt in range(max_attempts):
        driver.execute_script("arguments[0].click();", category_field)
        time.sleep(0.6)

        listbox_els = driver.find_elements(By.CSS_SELECTOR, "[role='listbox']")
        if not listbox_els:
            continue
        listbox = listbox_els[0]

        other_opt = _find_other_option(listbox)
        if other_opt is None:
            # rola o listbox incrementalmente (não a página) até "Outro"
            # aparecer no DOM ou até parar de haver mais conteúdo pra rolar
            last_scroll_top = -1
            for _ in range(30):
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].clientHeight;",
                    listbox,
                )
                time.sleep(0.25)
                other_opt = _find_other_option(listbox)
                if other_opt is not None:
                    break
                scroll_top = driver.execute_script("return arguments[0].scrollTop;", listbox)
                if scroll_top == last_scroll_top:
                    break  # chegou ao fim da lista sem achar
                last_scroll_top = scroll_top

        if other_opt is not None:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", other_opt
            )
            time.sleep(1)
            return

    raise RuntimeError("Não foi possível localizar a opção 'Other'/'Outro' na lista de categorias do WhatsApp")


def wait_captcha_solved(driver, timeout: int = 60, appear_timeout: int = 6) -> None:
    """Aguarda a extensão CapSolver marcar o checkbox 'Não sou um robô' que
    ÀS VEZES aparece na etapa de Details (reCAPTCHA Enterprise) — nem toda
    tentativa mostra o captcha (confirmado ao vivo: em algumas execuções o
    formulário fica completo sem ele). A extensão resolve sozinha quando ele
    aparece, então aqui só esperamos passivamente o estado 'checked', sem
    interferir na resolução.

    `appear_timeout` é o tempo que esperamos o captcha sequer APARECER antes
    de assumir que essa tentativa não teve captcha nenhum — usar o mesmo
    timeout longo (60s) tanto para "esperar aparecer" quanto para "esperar
    resolver" fazia a função ficar presa até estourar e lançar erro sempre
    que o captcha simplesmente não aparecia, travando o fluxo antes do clique
    em Continuar mesmo sem nada para resolver.

    O captcha fica dentro de um iframe aninhado (mesma estrutura vista no
    checkpoint de login: iframe do Facebook > iframe title='reCAPTCHA' do
    Google > #recaptcha-anchor com aria-checked='true' quando resolvido).
    """
    appear_deadline = time.time() + appear_timeout
    seen_captcha = False
    while time.time() < appear_deadline:
        if driver.find_elements(By.CSS_SELECTOR, "iframe[title='reCAPTCHA'], iframe[title*='recaptcha']"):
            seen_captcha = True
            break
        time.sleep(0.5)

    if not seen_captcha:
        return  # essa tentativa não mostrou captcha nenhum — segue para o Continuar

    deadline = time.time() + timeout
    while time.time() < deadline:
        recaptcha_iframes = [
            f for f in driver.find_elements(By.CSS_SELECTOR, "iframe[title='reCAPTCHA'], iframe[title*='recaptcha']")
            if f.is_displayed()
        ]
        if not recaptcha_iframes:
            # já vimos o captcha aparecer antes e agora sumiu — típico de
            # ter sido resolvido (o widget colapsa/esconde após validado)
            return
        for frame in recaptcha_iframes:
            try:
                driver.switch_to.frame(frame)
                anchor = driver.find_elements(By.CSS_SELECTOR, "#recaptcha-anchor")
                checked = bool(anchor) and anchor[0].get_attribute("aria-checked") == "true"
                driver.switch_to.parent_frame()
                if checked:
                    return
            except Exception:
                try:
                    driver.switch_to.parent_frame()
                except Exception:
                    pass
        time.sleep(1)
    raise RuntimeError(
        f"Captcha do WhatsApp não foi marcado como resolvido em {timeout}s — "
        "verifique se a extensão CapSolver está ativa e com API key configurada nesse perfil."
    )


def click_continue_to_phone_step(driver) -> None:
    continue_btn = driver.find_element(
        By.XPATH,
        "//div[@role='button' or self::button][contains(., 'Continue') or contains(., 'Continuar')]",
    )
    _cdp_click(driver, continue_btn)
    time.sleep(2)


def select_display_name_only(driver, timeout: int = 15) -> None:
    """Na etapa 'Escolha o número que deseja usar' (após Details), seleciona
    'Usar somente um nome de exibição' — evita o fluxo de adicionar/verificar
    um número novo por SMS/ligação — e clica em Continuar.

    Aguarda o modal terminar de renderizar antes de procurar a opção — sem
    esse wait, a busca rodava rápido demais logo após o clique em Continuar
    da etapa anterior, antes do DOM do novo modal existir, e sempre falhava
    com "opção não encontrada" mesmo com a tela certa carregando um instante
    depois.
    """
    option_texts = ["Usar somente um nome de exibição", "Use display name only"]

    def _find_option():
        for text in option_texts:
            candidates = [
                el for el in driver.find_elements(By.XPATH, f"//*[contains(text(),'{text}')]")
                if el.is_displayed()
            ]
            if candidates:
                return candidates[0]
        return None

    deadline = time.time() + timeout
    option_label = _find_option()
    while option_label is None and time.time() < deadline:
        time.sleep(0.3)
        option_label = _find_option()

    if option_label is None:
        raise RuntimeError(
            "Opção 'Usar somente um nome de exibição' não encontrada na etapa de Telefone do WhatsApp."
        )

    row = option_label.find_elements(
        By.XPATH, "./ancestor::div[.//input[@type='radio'] or @role='radio'][1]"
    )
    click_target = row[0] if row else option_label
    radio = click_target.find_elements(By.CSS_SELECTOR, "input[type='radio']")
    _cdp_click(driver, radio[0] if radio else click_target)
    time.sleep(0.5)

    continue_btn = driver.find_element(
        By.XPATH,
        "//div[@role='button' or self::button][contains(., 'Continue') or contains(., 'Continuar')]",
    )
    _cdp_click(driver, continue_btn)
    time.sleep(2)
