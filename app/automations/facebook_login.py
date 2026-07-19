"""Login completo no Facebook: autofill do AdsPower (usuário/senha) + código 2FA via 2fa.cn."""
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.adspower.facebook_creds import get_facebook_credentials
from .driver_utils import safe_url
from .totp import get_totp_code

_SET_VALUE_JS = """
const el = arguments[0];
const value = arguments[1];
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
nativeSetter.call(el, value);
el.dispatchEvent(new Event('input', { bubbles: true }));
el.dispatchEvent(new Event('change', { bubbles: true }));
"""


def _set_value(driver, element, value: str) -> None:
    driver.execute_script(_SET_VALUE_JS, element, value)


def _cdp_click(driver, element) -> None:
    """Clique de mouse real via Chrome DevTools Protocol — mais confiável que
    clique sintético (element.click() ou dispatchEvent) para elementos cujo
    handler React exige um evento de mouse confiável (isTrusted=true).
    Diagnosticado em outras telas do fluxo (verificação de negócio) onde
    cliques sintéticos silenciosamente não tinham efeito nenhum."""
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


def _click_continue_button(driver) -> bool:
    """Tenta clicar no botão 'Continue' da tela de código 2FA via CDP. Retorna
    True se encontrou e clicou, False se não achou o botão.

    BUG anterior corrigido: a query usava //div[@role='button' or
    @aria-label='Continue'], mas o "or" aplica só à condição @role='button'
    sozinha — ou seja, casava com QUALQUER div[role='button'] da página
    (inclusive o botão "X" de limpar o campo de código, que é role='button'
    também), não exigia relação nenhuma com o texto "Continue". Isso fazia o
    código clicar no botão errado, apagando o código já preenchido. Agora a
    busca é estritamente por elemento com aria-label='Continue' OU que
    contenha o texto 'Continue' de fato."""
    candidates = [
        el for el in driver.find_elements(
            By.XPATH,
            "//div[@role='button'][@aria-label='Continue'] "
            "| //button[@aria-label='Continue'] "
            "| //div[@role='button' or self::button][contains(.,'Continue')]",
        )
        if el.is_displayed()
    ]
    if not candidates:
        text_nodes = [
            el for el in driver.find_elements(By.XPATH, "//*[contains(text(),'Continue')]")
            if el.is_displayed()
        ]
        if not text_nodes:
            return False
        btn = text_nodes[0].find_elements(
            By.XPATH, "./ancestor-or-self::div[@role='button'] | ./ancestor-or-self::button"
        )
        candidates = [btn[0]] if btn else text_nodes[:1]
    _cdp_click(driver, candidates[0])
    return True


def _fill_totp_code_field(driver, totp_secret: str) -> str:
    """Preenche o campo de código 2FA (sem clicar em Continue) e retorna o código usado."""
    code = get_totp_code(driver, totp_secret)

    code_field = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Enter code'], input[type='text']")
    # foca via JS (em vez de .click() real, que é interceptado por elementos
    # sobrepostos nessa tela) — o foco em si não precisa de clique físico.
    # A limpeza usa seleção real (Ctrl+A + Delete) pois arguments[0].value='' via JS
    # não dispara onChange do React, e o CDP insertText seguinte grudaria o código
    # novo em cima do valor antigo que o React ainda "lembra"
    driver.execute_script("arguments[0].focus();", code_field)
    code_field.send_keys(Keys.CONTROL, "a")
    code_field.send_keys(Keys.DELETE)
    time.sleep(0.3)
    driver.execute_cdp_cmd("Input.insertText", {"text": code})
    time.sleep(0.3)
    return code


def _is_app_approval_screen(driver) -> bool:
    """Alguns perfis usam authenticator no celular (aprovar notificação push) em vez
    de digitar código TOTP — essa tela não tem campo de código nenhum, então
    tentar preenchê-la travaria o fluxo. Detecta pela ausência de QUALQUER input
    de texto visível (não só o placeholder exato 'Enter code', que pode variar
    ou não existir em todas as variações da tela de código real) combinada com
    textos típicos da variante de aprovação por app ('Check your notifications',
    'Open your authentication app', 'Waiting for approval')."""
    has_any_text_input = bool([
        i for i in driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input:not([type])")
        if i.is_displayed()
    ])
    if has_any_text_input:
        return False  # tem algum campo de texto visível — assume fluxo de código normal
    texts = [
        "Check your notifications", "Open your authentication app", "Check your other device",
        "Waiting for approval",
        "Verifique sua identidade", "Verifique suas notificações", "Abra seu app de autenticação",
    ]
    return any(driver.find_elements(By.XPATH, f"//*[contains(text(),'{t}')]") for t in texts)


def _click_visible_text(driver, texts: list[str]) -> bool:
    for text in texts:
        candidates = [
            el for el in driver.find_elements(By.XPATH, f"//*[contains(text(),'{text}')]")
            if el.is_displayed()
        ]
        if candidates:
            btn = candidates[0].find_elements(
                By.XPATH, "./ancestor-or-self::div[@role='button'] | ./ancestor-or-self::button"
            )
            driver.execute_script("arguments[0].click();", btn[0] if btn else candidates[0])
            return True
    return False


def _try_another_way(driver) -> bool:
    """Clica em 'Try another way' / 'Tente de outro jeito' para sair da tela de
    aprovação por app no celular. Isso abre o modal 'Choose a way to confirm it's
    you', com 'Notification on another device' pré-selecionado por padrão — então
    é preciso selecionar explicitamente 'Authentication app' e clicar Continue
    para cair no fluxo de código TOTP. Retorna True se todo o percurso funcionou."""
    if not _click_visible_text(driver, ["Try another way", "Tente de outro jeito", "Tentar de outra forma"]):
        return False
    time.sleep(1.5)

    auth_app_option = None
    for text in ["Authentication app", "App de autenticação", "Aplicativo de autenticação"]:
        candidates = [
            el for el in driver.find_elements(By.XPATH, f"//*[contains(text(),'{text}')]")
            if el.is_displayed()
        ]
        if candidates:
            auth_app_option = candidates[0]
            break
    if auth_app_option is None:
        return False

    row = auth_app_option.find_elements(
        By.XPATH, "./ancestor::div[.//input[@type='radio'] or @role='radio'][1]"
    )
    click_target = row[0] if row else auth_app_option
    radio = click_target.find_elements(By.CSS_SELECTOR, "input[type='radio']")
    driver.execute_script("arguments[0].click();", radio[0] if radio else click_target)
    time.sleep(0.5)

    if not _click_visible_text(driver, ["Continue", "Continuar"]):
        return False
    time.sleep(1.5)
    return True


def _is_captcha_screen(driver) -> bool:
    """Tela 'Enter the characters you see' (captcha visual) — tem um input de
    texto genérico que colidiria com a detecção do campo de código TOTP se não
    checado separadamente."""
    return bool(
        driver.find_elements(By.XPATH, "//*[contains(text(),'Enter the characters you see')]")
        or driver.find_elements(By.XPATH, "//*[contains(text(),'characters you see')]")
    )


def _submit_totp_code(driver, totp_secret: str, on_manual_step=None) -> None:
    """Preenche o código TOTP e clica em Continue automaticamente (via CDP —
    ver _click_continue_button). Só recorre à pausa manual no dashboard como
    último recurso, se várias tentativas de clique não confirmarem a saída da
    tela de código.

    on_manual_step(message): callback que bloqueia até o usuário confirmar pelo
    dashboard (ex: pause.wait_for_manual_step). Se None, faz um sleep fixo — usado
    só em scripts de teste isolados, não no fluxo real do orquestrador.
    """
    if _is_captcha_screen(driver):
        # captcha visual ("Enter the characters you see") — não dá pra
        # automatizar de forma confiável (é justamente pra bloquear isso).
        # Preenchimento é manual; o usuário resolve e clica Continuar.
        if on_manual_step:
            on_manual_step(
                "Captcha visual detectado ('Enter the characters you see'). Resolva "
                "manualmente na tela do Facebook e clique 'Continuar' aqui no dashboard "
                "quando o Facebook avançar para a tela seguinte."
            )
        else:
            time.sleep(20)
        deadline = time.time() + 10
        while time.time() < deadline:
            if not _is_captcha_screen(driver):
                break
            time.sleep(1)

    if _is_app_approval_screen(driver):
        # authenticator por notificação no celular, não por código — clica "Try
        # another way", seleciona explicitamente "Authentication app" no modal de
        # escolha (o padrão pré-selecionado é "Notification on another device") e
        # confirma, em vez de esperar aprovação manual no celular.
        clicked = _try_another_way(driver)
        if not clicked:
            raise RuntimeError(
                "Tela de aprovação por app detectada, mas não foi possível completar o "
                "percurso 'Try another way' -> selecionar 'Authentication app' -> Continue."
            )
        # espera ativa em vez de sleep único curto — a tela de código pode demorar
        # um pouco mais para renderizar após o Continue do modal de escolha. O
        # placeholder exato varia entre variações da tela ("Enter code" nem sempre
        # está presente), então aceita qualquer input de texto visível como sinal
        # de que a tela de código chegou — MAS não se for a tela de captcha, que
        # também tem um input de texto genérico e seria confundida com ela.
        code_field_deadline = time.time() + 8
        found_code_screen = False
        while time.time() < code_field_deadline:
            if _is_captcha_screen(driver):
                time.sleep(0.3)
                continue
            if driver.find_elements(By.CSS_SELECTOR, "input[placeholder='Enter code']"):
                found_code_screen = True
                break
            visible_text_inputs = [
                i for i in driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input:not([type])")
                if i.is_displayed()
            ]
            if visible_text_inputs:
                found_code_screen = True
                break
            time.sleep(0.3)
        if not found_code_screen:
            raise RuntimeError(
                "Segui o percurso 'Try another way' mas o campo de código TOTP não apareceu — "
                "verifique manualmente qual opção alternativa o Facebook ofereceu."
            )

    _fill_totp_code_field(driver, totp_secret)
    time.sleep(0.4)  # dá um instante pro React registrar o valor antes do clique

    # clique automático via CDP (mouse real, isTrusted=true) — o clique
    # sintético antigo era instável nesse botão específico, mas a técnica CDP
    # se provou confiável em outras telas do fluxo (verificação de negócio).
    #
    # BUG corrigido: a checagem de sucesso usava só "two_factor" not in url,
    # mas depois do código confirmado o Facebook costuma redirecionar para
    # 'two_factor/remember_browser' (tela "confiar neste navegador?"), que
    # AINDA contém "two_factor" na URL — a checagem simplista sempre achava
    # que o clique tinha falhado mesmo quando funcionou de verdade, e pausava
    # pedindo confirmação manual à toa em praticamente todo login. Usa a
    # mesma lógica (mais precisa) do final da função: só considera "ainda
    # precisa de código" quando é o FORMULÁRIO de código mesmo, não a tela
    # de pós-confirmação.
    def _still_needs_code() -> bool:
        url = safe_url(driver)
        return "two_factor/two_factor" in url or ("two_factor" in url and "remember_browser" not in url)

    for _attempt in range(4):
        _click_continue_button(driver)
        deadline = time.time() + 5
        while time.time() < deadline:
            if not _still_needs_code():
                return
            time.sleep(0.3)

    if not _still_needs_code():
        return

    if on_manual_step:
        on_manual_step(
            "Código 2FA preenchido, mas o clique automático em 'Continue' não confirmou. "
            "Clique em 'Continue' na tela do Facebook e depois clique 'Continuar' aqui no dashboard."
        )
    else:
        time.sleep(20)

    deadline = time.time() + 10
    while time.time() < deadline:
        if not _still_needs_code():
            return
        time.sleep(1)
    # não força erro aqui — se o usuário confirmou mas a tela ainda mostra a URL
    # antiga por lentidão de rede, deixa o fluxo seguir; a checagem final de
    # ensure_logged_in pega o caso de realmente não ter saído do 2FA


def _wait_for_login_state(driver, timeout_s: float = 90.0) -> bool:
    """Aguarda a página estabilizar em um dos estados reconhecíveis (form de login,
    2FA, ou logado) antes de decidir o que fazer — o Facebook pode redirecionar de
    facebook.com para a tela de 2FA com atraso (passando antes por uma checagem de
    segurança 'two_step_verification/authentication'), e checar a URL cedo demais
    faz o código tratar erroneamente uma sessão pendente de 2FA como "já logada".

    Essa tela intermediária pode durar bastante quando o 2FA é por aprovação no
    celular (o usuário precisa notar a notificação e aprovar), por isso o timeout
    é generoso. Retorna True se saiu para um estado reconhecido, False se ainda
    estava preso na tela intermediária ao esgotar o timeout — o chamador NÃO deve
    assumir "logado" nesse caso.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        url = safe_url(driver)
        if "two_step_verification/authentication" in url:
            time.sleep(0.5)
            continue  # checagem de segurança Arkose em andamento, ainda não é o estado final
        if "two_factor" in url:
            return True
        if driver.find_elements(By.NAME, "email"):
            return True
        if "facebook.com" in url and "two_step_verification" not in url and "checkpoint" not in url:
            return True  # navegou pra algum outro estado estável (feed, etc.)
        time.sleep(0.5)
    return False


def ensure_logged_in(driver, profile_id: str, on_manual_step=None) -> None:
    driver.get("https://www.facebook.com/")
    time.sleep(1)
    _wait_for_login_state(driver)

    # a sessão pode já ter usuário/senha aceitos de uma tentativa anterior e cair
    # direto na tela de 2FA (sem passar pelo form de email/senha) — trata esse caso
    # antes de assumir "sem campo de email = já logado por completo"
    if "two_factor" in safe_url(driver):
        creds = get_facebook_credentials(profile_id)
        _submit_totp_code(driver, creds.totp_secret, on_manual_step)

    email_fields = driver.find_elements(By.NAME, "email")
    if not email_fields and "two_factor" not in safe_url(driver):
        return  # já logado, sem form de login e sem pendência de 2FA

    if email_fields:
        # falha rápido e com mensagem clara se o perfil não tem credenciais do Facebook
        # configuradas no remark do AdsPower, em vez de esperar o autofill inutilmente
        # e falhar depois com um erro genérico de elemento não encontrado
        creds = get_facebook_credentials(profile_id)
        if not creds.username or not creds.password:
            raise RuntimeError(
                f"Perfil {profile_id} não tem usuário/senha do Facebook configurados no remark "
                "do AdsPower. Configure o remark no formato "
                "'username:senha:email:senha:totp_secret:...' antes de usar este perfil."
            )

        email_field = email_fields[0]
        pass_field = driver.find_element(By.NAME, "pass")

        # aguarda o autofill do AdsPower estabilizar (digita devagar, letra por letra) —
        # exige valores NÃO VAZIOS antes de considerar estável, senão dois campos vazios
        # "estabilizam" instantaneamente e o login segue sem credenciais reais.
        # Janela mais curta que antes (14 tentativas ~5s) porque agora há um
        # fallback: se o autofill não vier a tempo, preenchemos manualmente com
        # as credenciais do remark em vez de travar o fluxo inteiro.
        last_email, last_pass = "", ""
        stable_count = 0
        stabilized = False
        navigated_away = False
        for _ in range(14):
            time.sleep(0.35)
            try:
                email_val = email_field.get_attribute("value")
                pass_val = pass_field.get_attribute("value")
            except Exception:
                # elemento ficou stale — só é seguro seguir em frente se a página
                # realmente navegou para longe do formulário de login (ex: já passou
                # pro 2FA ou pro feed); senão, tenta reobter os campos e continuar esperando
                if "login" not in safe_url(driver).lower() and driver.find_elements(By.NAME, "email") == []:
                    navigated_away = True
                    break
                new_email_fields = driver.find_elements(By.NAME, "email")
                new_pass_fields = driver.find_elements(By.NAME, "pass")
                if not new_email_fields or not new_pass_fields:
                    navigated_away = True
                    break
                email_field, pass_field = new_email_fields[0], new_pass_fields[0]
                continue
            if email_val and pass_val and email_val == last_email and pass_val == last_pass:
                stable_count += 1
                if stable_count >= 3:
                    stabilized = True
                    break
            else:
                stable_count = 0
            last_email, last_pass = email_val, pass_val

        if not stabilized and not navigated_away:
            # autofill do AdsPower não veio (ou não completou) a tempo — preenche
            # manualmente com as credenciais já extraídas do remark em vez de
            # travar o fluxo esperando algo que pode nunca vir.
            _set_value(driver, email_field, creds.username)
            _set_value(driver, pass_field, creds.password)
            time.sleep(0.3)
            email_val = email_field.get_attribute("value")
            pass_val = pass_field.get_attribute("value")
            if not email_val or not pass_val:
                raise RuntimeError(
                    f"Não foi possível preencher usuário/senha para o perfil {profile_id} "
                    "(nem via autofill do AdsPower, nem manualmente com as credenciais do remark). "
                    "Verifique se as credenciais estão salvas corretamente no AdsPower."
                )

        if "two_factor" not in safe_url(driver):
            time.sleep(0.3)
            submit_els = driver.find_elements(By.CSS_SELECTOR, "input[type=submit]")
            if submit_els:
                driver.execute_script("arguments[0].click();", submit_els[0])
                # checagem de segurança Arkose Labs: pode ficar em
                # 'two_step_verification/authentication' por bastante tempo antes de
                # resolver sozinha ou redirecionar — usa o mesmo timeout generoso de
                # _wait_for_login_state em vez de um deadline curto e fixo.
                deadline = time.time() + 90
                while time.time() < deadline:
                    url = safe_url(driver)
                    if "two_step_verification/authentication" in url:
                        time.sleep(0.5)
                        continue
                    if "two_factor" in url or not driver.find_elements(By.NAME, "email"):
                        break
                    time.sleep(0.3)

        if "two_factor" in safe_url(driver):
            creds = get_facebook_credentials(profile_id)
            _submit_totp_code(driver, creds.totp_secret, on_manual_step)

    # tela "Trust this device?" (rota two_factor/remember_browser) — pergunta se quer
    # lembrar o navegador; escolhe "Always confirm it's me" para não persistir
    # confiança no perfil. Esta tela contém "two_factor" na URL mas NÃO significa que
    # o 2FA ainda está pendente — é um passo pós-autenticação, então é tratada à parte
    # da checagem final de "login não concluído". Clique via CDP (mouse real):
    # o clique sintético (execute_script) usado aqui antes podia não confirmar,
    # deixando a automação presa nessa tela até a pausa manual do dashboard.
    if "two_factor/remember_browser" in safe_url(driver):
        try:
            confirm_btn = driver.find_element(
                By.XPATH, "//div[@role='button' or self::button][contains(., \"Always confirm\")]"
            )
            _cdp_click(driver, confirm_btn)
            time.sleep(2)
        except Exception:
            pass

    # confirma de fato que o login foi concluído antes de retornar — evita seguir
    # em frente com uma sessão inválida/checkpoint pendente. "two_factor/two_factor"
    # (o formulário de código) é falha real; "two_factor/remember_browser" (pergunta
    # pós-login sobre lembrar o navegador) não é.
    final_url = safe_url(driver)
    still_needs_code = "two_factor/two_factor" in final_url or (
        "two_factor" in final_url and "remember_browser" not in final_url
    )
    # cobre a tela de aprovação por app no celular: a URL pode não conter
    # "two_factor" nessa variante, então checar só a URL não é suficiente —
    # também confere se a tela de aprovação pendente ainda está visível.
    still_awaiting_app_approval = _is_app_approval_screen(driver)
    if driver.find_elements(By.NAME, "email") or still_needs_code or still_awaiting_app_approval:
        raise RuntimeError(
            f"Login no Facebook não foi concluído para o perfil {profile_id} "
            f"(URL final: {final_url}, aguardando aprovação por app: {still_awaiting_app_approval})"
        )


def resolve_reauth_2fa(driver, profile_id: str) -> None:
    """Resolve a tela de reautenticação 2FA que aparece ao acessar áreas sensíveis
    do Business Manager (ex: lista de portfólios), mesmo já estando logado."""
    if "twofactor/reauth" not in safe_url(driver):
        return

    creds = get_facebook_credentials(profile_id)
    code = get_totp_code(driver, creds.totp_secret)

    code_field = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Enter code']")
    driver.execute_script("arguments[0].focus(); arguments[0].value='';", code_field)
    driver.execute_cdp_cmd("Input.insertText", {"text": code})
    time.sleep(0.5)

    confirm_btn = driver.find_element(By.XPATH, "//div[@role='button' or self::button][contains(., 'Confirm')]")
    driver.execute_script("arguments[0].click();", confirm_btn)

    deadline = time.time() + 15
    while time.time() < deadline:
        if "twofactor/reauth" not in safe_url(driver):
            return
        time.sleep(1)

    raise RuntimeError(f"Reautenticação 2FA não foi concluída para o perfil {profile_id}")
