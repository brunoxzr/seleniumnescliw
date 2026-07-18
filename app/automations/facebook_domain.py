"""Adiciona e verifica domínio no Business Manager do Facebook.

Nota: o domínio precisa ser o subdomínio completo do site gerado pelo Buildfy
(ex: kalinejosecassemiro.dominiobrstore.com), não apenas o domínio raiz
compartilhado (dominiobrstore.com) — este último já pertence a outra conta.
"""
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .facebook_scope import ensure_business_scope

DOMAIN_INPUT_CSS = "input[placeholder*='example.com']"


def _cdp_fill(driver, element, text: str) -> None:
    driver.execute_script("arguments[0].focus(); arguments[0].value='';", element)
    driver.execute_cdp_cmd("Input.insertText", {"text": text})
    time.sleep(0.2)


def domains_url(business_id: str) -> str:
    return f"https://business.facebook.com/latest/settings/domains?business_id={business_id}"


def _wait_visible(driver, locator, timeout=6):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))


def _find_visible_buttons_with_text(driver, text):
    return [
        b for b in driver.find_elements(By.XPATH, f"//div[@role='button' or self::button][contains(., '{text}')]")
        if b.is_displayed()
    ]


def add_domain(driver, business_id: str, domain: str) -> str:
    """Adiciona o domínio ao BM e retorna o código do atributo content da meta-tag."""
    ensure_business_scope(driver, business_id)
    driver.get(domains_url(business_id))
    _wait_visible(driver, (By.XPATH, "//h1[contains(., 'Domains')] | //*[contains(text(),'No domains added')]"), timeout=10)

    add_btns = _find_visible_buttons_with_text(driver, "Add")
    if not add_btns:
        raise RuntimeError("Botão 'Add' de domínios não encontrado na tela — verifique manualmente.")
    driver.execute_script("arguments[0].click();", add_btns[0])

    # duas variações observadas nessa tela: às vezes "+ Add" abre direto o campo
    # de domínio, às vezes primeiro abre um seletor "What do you want to do?"
    # (Create a domain / Request access) que precisa de um clique extra.
    domain_field = None
    for attempt in range(3):
        try:
            domain_field = WebDriverWait(driver, 4).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, DOMAIN_INPUT_CSS))
            )
            break
        except TimeoutException:
            pass

        create_domain_candidates = driver.find_elements(
            By.XPATH,
            "//*[@role='dialog']//*[contains(normalize-space(.),'Create a domain') "
            "and not(.//*[contains(normalize-space(.),'Create a domain')])]",
        )
        if create_domain_candidates:
            target = create_domain_candidates[0]
            row = target.find_elements(By.XPATH, "./ancestor::div[@role='button' or @tabindex][1]")
            driver.execute_script("arguments[0].click();", row[0] if row else target)
            continue

        # nada apareceu ainda (modal em animação) ou o clique em Add não registrou
        add_btns = _find_visible_buttons_with_text(driver, "Add")
        if add_btns:
            driver.execute_script("arguments[0].click();", add_btns[0])

    if domain_field is None:
        raise RuntimeError(
            "Modal 'Add a domain' não abriu após múltiplas tentativas de clique em 'Add' "
            "— verifique manualmente a tela do navegador."
        )

    _cdp_fill(driver, domain_field, domain)

    # o preenchimento do campo dispara re-render do React (o nó [role='dialog'] e o
    # próprio botão 'Add' podem ser trocados/ficar stale); por isso localizamos e
    # clicamos no MESMO passo, com retry, em vez de guardar uma referência antiga.
    def _find_add_confirm_btn():
        dialogs = driver.find_elements(By.CSS_SELECTOR, "[role='dialog']")
        search_scope = dialogs[-1] if dialogs else driver
        buttons_in_scope = search_scope.find_elements(By.XPATH, ".//div[@role='button' or self::button]")
        candidates = [b for b in buttons_in_scope if b.text.strip() == "Add"]
        return candidates[-1] if candidates else None

    clicked = False
    last_error = None
    for attempt in range(4):
        try:
            btn = _find_add_confirm_btn()
            if btn is None:
                time.sleep(0.5)
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            try:
                btn.click()
            except Exception:
                # "element click intercepted" — outro elemento (overlay/backdrop) está
                # sobreposto no ponto do clique real; clique via JS ignora a posição
                # na tela e dispara o evento direto no elemento alvo.
                driver.execute_script("arguments[0].click();", btn)
            clicked = True
            break
        except Exception as e:
            last_error = e
            time.sleep(0.5)

    if not clicked:
        raise RuntimeError(
            f"Não foi possível clicar no botão 'Add' de confirmação do domínio após múltiplas "
            f"tentativas ({last_error}) — verifique manualmente a tela do navegador."
        )

    meta_tag_text = _wait_visible(
        driver, (By.XPATH, "//*[contains(text(), 'Copy this meta-tag')]"), timeout=10
    ).text
    # formato: 'Copy this meta-tag: <meta name="facebook-domain-verification" content="XXXX" />'
    return meta_tag_text.split('content="')[1].split('"')[0]


def verify_domain(driver, business_id: str, domain: str, max_attempts: int = 3) -> bool:
    """Clica em Verify domain. Deve ser chamado após aplicar a meta-tag no site.

    Retorna True se o status mudou para Verified.
    """
    try:
        driver.current_window_handle
    except Exception as e:
        raise RuntimeError(
            f"Sessão do navegador não está mais ativa ({type(e).__name__}: {e}) — "
            "a janela pode ter sido fechada. Abra o perfil de novo e clique Continuar."
        ) from e

    ensure_business_scope(driver, business_id)
    driver.get(domains_url(business_id))

    def _is_verified() -> bool:
        # quando já verificado, o botão 'Verify domain' desaparece da tela — por
        # isso essa checagem precisa vir ANTES de tentar clicar, não só depois.
        if driver.find_elements(By.XPATH, "//*[contains(text(),'Not Verified')]"):
            return False
        return bool(driver.find_elements(By.XPATH, "//*[contains(text(),'Verified')]"))

    # espera ativa até o status (Verified/Not Verified) OU o botão de verificar
    # aparecer no DOM — um sleep fixo curto podia checar antes da página
    # terminar de carregar, dando falso negativo mesmo com o domínio já Verified
    deadline = time.time() + 8
    while time.time() < deadline:
        if driver.find_elements(By.XPATH, "//*[contains(text(),'Verified')]"):
            break
        time.sleep(0.3)
    else:
        time.sleep(1)  # nada apareceu no timeout — segue mesmo assim, sem travar

    if _is_verified():
        return True

    def _find_verify_buttons():
        # cobre PT ("Verificar domínio") e EN ("Verify Domain"/"Verify domain") sem
        # usar translate() com acentos (unreliable em alguns ChromeDriver/XPath 1.0);
        # filtra em Python (case-insensitive) em vez de no próprio XPath.
        all_btns = driver.find_elements(By.XPATH, "//div[@role='button'] | //button")
        found = []
        for b in all_btns:
            try:
                if not b.is_displayed():
                    continue
                txt = b.text.strip().lower()
                if "verify" in txt and "domain" in txt:
                    found.append(b)
                elif "verificar" in txt and "dom" in txt:
                    found.append(b)
            except Exception:
                continue
        return found

    for _ in range(max_attempts):
        clicked = False
        last_error = None
        for click_attempt in range(4):
            # o botão 'Verify domain' desaparece assim que o domínio é
            # verificado — se isso já aconteceu (ex: verificação anterior deste
            # mesmo loop, ou clique já processado), não há botão para achar e
            # isso não é erro, é sucesso.
            if _is_verified():
                return True
            try:
                candidates = _find_verify_buttons()
                if not candidates:
                    time.sleep(0.5)
                    continue
                verify_btn = candidates[-1]
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", verify_btn
                )
                clicked = True
                break
            except Exception as e:
                last_error = e
                time.sleep(0.5)
        if not clicked:
            if _is_verified():
                return True
            raise RuntimeError(
                f"Botão 'Verify domain' não pôde ser clicado após múltiplas tentativas "
                f"({last_error}) — verifique manualmente."
            )
        # espera ativa pelo status atualizar após o clique — o React pode demorar
        # mais que um sleep fixo curto pra refletir 'Verified' na tela, e um
        # recarregamento prematuro da página descartava esse progresso
        verify_deadline = time.time() + 6
        while time.time() < verify_deadline:
            if _is_verified():
                return True
            time.sleep(0.5)

        driver.get(domains_url(business_id))
        time.sleep(2)

    return False
