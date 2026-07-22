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


def select_category_other(driver, max_attempts: int = 3) -> None:
    """Seleciona a categoria 'Other' no wizard de criação da conta WhatsApp.

    O display name já vem preenchido por padrão com o nome da página conectada,
    não precisa ser digitado. A categoria é um combobox customizado (div role=combobox)
    cuja lista visível inicialmente não inclui 'Other' — é preciso clicar em
    'Show more options' antes de abrir a lista completa.
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

        listbox = driver.find_elements(By.CSS_SELECTOR, "[role='listbox']")
        if not listbox:
            continue
        options = listbox[0].find_elements(By.CSS_SELECTOR, "[role='option']")

        other_opt = None
        for o in options:
            text = driver.execute_script("return arguments[0].textContent;", o).strip().lower()
            if text in ("other", "outro", "outra"):
                other_opt = o
                break

        if other_opt:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", other_opt
            )
            time.sleep(1)
            return

    raise RuntimeError("Não foi possível localizar a opção 'Other' na lista de categorias do WhatsApp")


def click_continue_to_phone_step(driver) -> None:
    continue_btn = driver.find_element(
        By.XPATH, "//div[@role='button' or self::button][contains(., 'Continue')]"
    )
    driver.execute_script("arguments[0].click();", continue_btn)
    time.sleep(2)
