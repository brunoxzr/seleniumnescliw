"""Cria um Business Manager novo no Facebook a partir dos dados de um site do Buildfy."""
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import facebook_scope
from .driver_utils import safe_url

CREATE_BM_URL = (
    "https://business.facebook.com/business/loginpage/"
    "?login_options[0]=FB&login_options[1]=IG&login_options[2]=SSO"
    "&config_ref=biz_login_tool_flavor_mbs&create_business_portfolio_for_bm=1"
)

ALL_BUSINESSES_URL = "https://business.facebook.com/business_locations"


def find_business_id_by_name(driver, business_name: str) -> str | None:
    """Busca o business_id pelo nome exato na lista de portfólios do usuário —
    fallback para quando o BM foi criado mas a aba/URL de criação não está mais
    disponível para ler o business_id (ex: aba fechada manualmente)."""
    driver.get(ALL_BUSINESSES_URL)
    time.sleep(2)

    links = driver.find_elements(By.XPATH, "//a[contains(@href,'business_id=')]")
    for link in links:
        row_text = link.text.strip()
        if not row_text:
            row_text = (link.get_attribute("aria-label") or "").strip()
        if business_name.strip().lower() in row_text.lower():
            href = link.get_attribute("href") or ""
            if "business_id=" in href:
                return href.split("business_id=")[1].split("&")[0]
    return None


def _cdp_fill(driver, element, text: str) -> None:
    driver.execute_script("arguments[0].focus(); arguments[0].value='';", element)
    driver.execute_cdp_cmd("Input.insertText", {"text": text})
    time.sleep(0.3)


def _cdp_click(driver, element) -> None:
    """Clique de mouse real via Chrome DevTools Protocol — mais confiável que
    clique sintético (execute_script .click()) para botões cujo handler React
    exige um evento de mouse confiável (isTrusted=true)."""
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


def _click_submit_button(driver) -> bool:
    """Encontra o botão 'Submit' do formulário de criação do Business Manager
    e clica via CDP. Retorna True se encontrou e clicou."""
    candidates = [
        el for el in driver.find_elements(
            By.XPATH, "//div[@role='button' or self::button][contains(.,'Submit')]"
        )
        if el.is_displayed()
    ]
    if not candidates:
        return False
    _cdp_click(driver, candidates[0])
    return True


def _click_done_button(driver) -> bool:
    """Encontra o botão 'Done' da tela de confirmação (aparece depois do
    Submit carregar) e clica via CDP. Retorna True se encontrou e clicou."""
    candidates = [
        el for el in driver.find_elements(
            By.XPATH, "//div[@role='button' or self::button][contains(.,'Done')]"
        )
        if el.is_displayed()
    ]
    if not candidates:
        return False
    _cdp_click(driver, candidates[0])
    return True


def create_business_manager(
    driver, business_name: str, person_name: str, business_email: str, on_manual_step=None
) -> str:
    """Preenche o formulário de criação de portfólio empresarial e aguarda o usuário
    clicar em Submit manualmente — esse clique se mostrou instável quando automatizado
    (elemento interceptado de forma intermitente), então preenchemos os campos e
    deixamos a confirmação final na mão do usuário via pausa no dashboard.

    Retorna o business_id extraído da URL após a criação.
    """
    # se uma tentativa anterior já criou o BM (só demorou mais que o timeout do
    # polling pra redirecionar, ou a aba de criação foi fechada manualmente), o BM
    # já existe — não recarrega a página de criação nesse caso, senão duplica.
    # Checa primeiro a URL atual (rápido) e depois a lista de portfólios pelo nome
    # (cobre o caso de aba fechada, onde a URL atual não é mais a de criação).
    current_url = safe_url(driver)
    if "business_id=" in current_url:
        business_id = current_url.split("business_id=")[1].split("&")[0]
        facebook_scope._last_scoped_business_id = business_id
        return business_id

    # a checagem por nome (find_business_id_by_name) só entra em cena se a janela
    # não existir mais (retry após aba fechada) — no caminho comum (primeira
    # tentativa, janela ativa) pularia direto pra criação sem esse custo extra.
    try:
        driver.current_window_handle
    except Exception:
        existing_id = find_business_id_by_name(driver, business_name)
        if existing_id:
            facebook_scope._last_scoped_business_id = existing_id
            return existing_id
        raise RuntimeError(
            "A janela do navegador foi fechada e o Business Manager não foi encontrado "
            f"na lista de portfólios pelo nome '{business_name}'. Abra o perfil de novo "
            "no AdsPower e clique Continuar."
        )

    driver.get(CREATE_BM_URL)
    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder=\"Jasper's Market\"]"))
        )
    except TimeoutException:
        pass  # segue para a checagem de sessão caída abaixo, que dá o erro certo

    # detecta sessão caída/checkpoint de segurança antes de tentar preencher o formulário
    if "work.meta.com" in safe_url(driver) or driver.find_elements(By.NAME, "email"):
        raise RuntimeError(
            "Sessão do Facebook não está mais válida (caiu para tela de login/checkpoint). "
            "Provavelmente um checkpoint de segurança deslogou a conta. "
            "Resolva manualmente pelo AdsPower antes de tentar novamente."
        )

    business_name_field = driver.find_element(By.CSS_SELECTOR, "input[placeholder=\"Jasper's Market\"]")
    your_name_field = driver.find_element(By.CSS_SELECTOR, "input[placeholder*='first and last name']")
    email_field = driver.find_element(By.XPATH, "//input[not(@placeholder)]")

    _cdp_fill(driver, business_name_field, business_name)
    _cdp_fill(driver, your_name_field, person_name)
    _cdp_fill(driver, email_field, business_email)
    time.sleep(0.4)

    # clique automático via CDP (mouse real, isTrusted=true) — mais confiável
    # que o clique sintético antigo, que se mostrava instável nesse botão.
    # Tenta várias vezes (até ~20s no total) antes de recorrer à pausa manual —
    # a criação do BM pode demorar mais que poucos segundos para redirecionar,
    # e desistir cedo demais fazia pausar à toa mesmo quando o clique já tinha
    # funcionado e só faltava a página terminar de processar.
    for _attempt in range(4):
        _click_submit_button(driver)
        deadline = time.time() + 5
        while time.time() < deadline:
            if "business_id=" in safe_url(driver):
                break
            time.sleep(0.3)
        if "business_id=" in safe_url(driver):
            break

    if "business_id=" not in safe_url(driver) and on_manual_step:
        on_manual_step(
            "Campos do Business Manager preenchidos, mas o clique automático em 'Submit' "
            "não confirmou. Clique em 'Submit' na tela do Facebook e depois clique "
            "'Continuar' aqui no dashboard."
        )
    elif "business_id=" not in safe_url(driver) and not on_manual_step:
        _click_submit_button(driver)

    # a criação demora alguns segundos e redireciona para business_id na URL,
    # ou o Facebook rejeita com "Unable to Create Account" (nome já em uso)
    for _ in range(60):
        time.sleep(1)
        if "business_id=" in safe_url(driver):
            break
        error_els = driver.find_elements(By.XPATH, "//*[contains(text(), 'Unable to Create Account')]")
        if error_els:
            raise RuntimeError(
                f"Facebook rejeitou a criação do BM: nome '{business_name}' já está em uso. "
                "Use um nome de negócio distinto (ex: incluir CNPJ ou sufixo)."
            )
    else:
        # a URL não trouxe business_id (pode ser que a aba tenha sido fechada/trocada
        # manualmente antes do redirect) — antes de desistir, busca o BM pelo nome
        # exato na lista de portfólios, já que ele pode ter sido criado de verdade.
        fallback_id = find_business_id_by_name(driver, business_name)
        if fallback_id:
            facebook_scope._last_scoped_business_id = fallback_id
            return fallback_id
        raise RuntimeError(
            "Business Manager não foi criado a tempo (URL não mudou em 60s) e não foi "
            f"encontrado na lista de portfólios pelo nome '{business_name}'. Verifique "
            "manualmente se ele foi criado antes de tentar de novo."
        )

    # tela de confirmação "Portfólio criado!" pode ter um botão 'Done' pra
    # fechar o modal — clica automaticamente (mesma técnica CDP), sem pausar
    # pra isso: se não existir ou não confirmar, não é bloqueante (a URL já
    # tem o business_id, o fluxo pode seguir mesmo com o modal ainda aberto).
    if _click_done_button(driver):
        time.sleep(1)

    url = safe_url(driver)
    business_id = url.split("business_id=")[1].split("&")[0]
    # o BM recém-criado já é o escopo ativo da sessão; registra para evitar
    # uma navegação redundante na próxima chamada de ensure_business_scope
    facebook_scope._last_scoped_business_id = business_id
    return business_id
