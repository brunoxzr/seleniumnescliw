"""Resolve captchas (reCAPTCHA v2/v3, hCaptcha) via API HTTP do CapSolver
(capsolver.com), sem depender de extensão de navegador — o Chrome é aberto
pelo AdsPower, fora do nosso controle de lançamento, então uma extensão não
pode ser injetada via Selenium no momento de abrir o perfil.

Fluxo: extrai o site-key do captcha presente na página, manda pra API do
CapSolver, aguarda a resolução (polling) e injeta o token de volta na página
via JS, disparando o callback que o site espera para validar a resposta.

No checkpoint de segurança do Facebook ("Não sou um robô"), o widget não fica
na página de topo: o Facebook embute um iframe próprio
(id="captcha-recaptcha", src=/common/referer_frame.php) e É DENTRO DESSE
IFRAME que fica o <div class="g-recaptcha" data-sitekey="..."
data-callback="successCallback">, o textarea #g-recaptcha-response e o script
recaptcha/enterprise.js (confirmado inspecionando o DOM real). Por isso a
resolução precisa trocar de contexto para esse iframe (driver.switch_to.frame)
antes de ler o sitekey e antes de injetar o token/disparar o callback — fazer
isso na página de topo não encontra nada e não teria efeito nenhum mesmo que
encontrasse, já que o grecaptcha.execute daquele widget vive no frame.
"""
import os
import time

import requests
from selenium.webdriver.common.by import By

API_BASE = "https://api.capsolver.com"

_FB_CAPTCHA_FRAME_SELECTOR = "iframe#captcha-recaptcha, iframe[src*='referer_frame.php']"


class CapSolverError(Exception):
    pass


def _api_key() -> str:
    key = os.environ.get("CAPSOLVER_API_KEY", "")
    if not key:
        raise CapSolverError(
            "CAPSOLVER_API_KEY não configurada. Defina a variável de ambiente com a "
            "chave encontrada em capsolver.com > Dashboard > API Key."
        )
    return key


def _create_task(task: dict) -> str:
    resp = requests.post(
        f"{API_BASE}/createTask",
        json={"clientKey": _api_key(), "task": task},
        timeout=30,
    )
    data = resp.json()
    if data.get("errorId"):
        raise CapSolverError(f"CapSolver createTask falhou: {data.get('errorDescription')}")
    return data["taskId"]


def _get_result(task_id: str, timeout: int = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.post(
            f"{API_BASE}/getTaskResult",
            json={"clientKey": _api_key(), "taskId": task_id},
            timeout=30,
        )
        data = resp.json()
        if data.get("errorId"):
            raise CapSolverError(f"CapSolver getTaskResult falhou: {data.get('errorDescription')}")
        if data.get("status") == "ready":
            return data["solution"]
        time.sleep(2)
    raise CapSolverError(f"CapSolver não resolveu o captcha em {timeout}s (task {task_id}).")


_INJECT_TOKEN_JS = """
const token = arguments[0];
let textarea = document.getElementById('g-recaptcha-response');
if (!textarea) {
    textarea = document.createElement('textarea');
    textarea.id = 'g-recaptcha-response';
    textarea.name = 'g-recaptcha-response';
    textarea.style.display = 'none';
    document.body.appendChild(textarea);
}
textarea.innerHTML = token;
textarea.value = token;

// dispara o callback declarado em data-callback do widget (ex: "successCallback"
// no checkpoint do Facebook). CRÍTICO: quando o captcha está dentro de um
// iframe embutido pelo Facebook (fbsbx.com/captcha/recaptcha/iframe), o
// callback real costuma estar definido no documento PAI (facebook.com), não
// dentro do próprio iframe — é o pai quem precisa saber que o captcha foi
// resolvido para navegar a página. Chamar só window[callbackName] aqui dentro
// não tem efeito nenhum se o pai nunca é notificado. Tenta os dois: o
// contexto atual E window.parent/window.top (se acessível — mesma origem
// não é garantida, então falhas de cross-origin são engolidas).
const widget = document.querySelector('.g-recaptcha[data-callback]');
const callbackName = widget ? widget.getAttribute('data-callback') : null;
const attempts = [];
if (callbackName) {
    if (typeof window[callbackName] === 'function') {
        try { window[callbackName](token); attempts.push('window'); } catch (e) {}
    }
    try {
        if (window.parent && window.parent !== window && typeof window.parent[callbackName] === 'function') {
            window.parent[callbackName](token);
            attempts.push('parent');
        }
    } catch (e) {}
    try {
        if (window.top && window.top !== window && typeof window.top[callbackName] === 'function') {
            window.top[callbackName](token);
            attempts.push('top');
        }
    } catch (e) {}
}

if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
    for (const clientId in window.___grecaptcha_cfg.clients) {
        const client = window.___grecaptcha_cfg.clients[clientId];
        for (const key in client) {
            const obj = client[key];
            if (obj && typeof obj === 'object') {
                for (const innerKey in obj) {
                    const cb = obj[innerKey] && obj[innerKey].callback;
                    if (typeof cb === 'function') {
                        try { cb(token); attempts.push('grecaptcha_cfg'); } catch (e) {}
                    }
                }
            }
        }
    }
}

// dispara também um evento 'change' no textarea — alguns integradores
// escutam isso em vez de (ou além de) chamar o callback diretamente
textarea.dispatchEvent(new Event('change', { bubbles: true }));
textarea.dispatchEvent(new Event('input', { bubbles: true }));

return {callbackName: callbackName, attempts: attempts};
"""


def _find_recaptcha_sitekey_in_current_context(driver) -> str | None:
    els = driver.find_elements(By.CSS_SELECTOR, "[data-sitekey]")
    visible = [el for el in els if el.is_displayed()]
    if visible:
        sitekey = visible[0].get_attribute("data-sitekey")
        if sitekey:
            return sitekey
    return None


def solve_recaptcha_v2(driver, timeout: int = 120) -> bool:
    """Detecta e resolve um reCAPTCHA v2/Enterprise visível na página atual OU
    dentro do iframe embutido que o Facebook usa nos checkpoints de segurança
    ("Não sou um robô"). Retorna False se não encontrar nenhum captcha (não é
    erro — pode simplesmente não ter aparecido).
    """
    # 1. tenta achar o widget direto na página de topo (caso genérico, usado
    # por outras telas do fluxo que não passam pelo iframe do Facebook)
    sitekey = _find_recaptcha_sitekey_in_current_context(driver)
    in_fb_frame = False

    if sitekey is None:
        # 2. procura o iframe próprio do Facebook e entra nele — é lá que o
        # widget real (com data-sitekey e data-callback) fica renderizado
        fb_frames = driver.find_elements(By.CSS_SELECTOR, _FB_CAPTCHA_FRAME_SELECTOR)
        print(f"[capsolver] sitekey não achado no topo. iframes candidatos do Facebook: {len(fb_frames)}")
        for frame in fb_frames:
            try:
                driver.switch_to.frame(frame)
                sitekey = _find_recaptcha_sitekey_in_current_context(driver)
                if sitekey:
                    in_fb_frame = True
                    break
                driver.switch_to.default_content()
            except Exception as e:
                print(f"[capsolver] erro ao entrar/ler iframe do Facebook: {type(e).__name__}: {e}")
                driver.switch_to.default_content()

    if sitekey is None:
        print("[capsolver] nenhum sitekey encontrado (nem no topo, nem nos iframes) — retornando False")
        driver.switch_to.default_content()
        return False

    print(f"[capsolver] sitekey encontrado: {sitekey!r} in_fb_frame={in_fb_frame}")

    # clica no checkbox "Não sou um robô" ANTES de sequer chamar a API do
    # CapSolver — descoberto ao vivo: injetar o token direto no textarea sem
    # nunca clicar no widget não tem efeito nenhum (o checkbox nunca aparece
    # marcado e a página não avança), porque o reCAPTCHA v2 checkbox só entra
    # em estado "verificando" (e só então aceita/consulta o token) depois de
    # uma interação real do usuário no checkbox. O clique em si não resolve o
    # captcha sozinho (ainda pede o token via CapSolver), mas é o gatilho que
    # falta para a página aceitar o resultado da API.
    _click_checkbox_if_present(driver)

    page_url = driver.current_url
    task_id = _create_task({
        "type": "ReCaptchaV2EnterpriseTaskProxyLess" if in_fb_frame else "ReCaptchaV2TaskProxyLess",
        "websiteURL": page_url,
        "websiteKey": sitekey,
    })
    print(f"[capsolver] task criada: {task_id}")
    try:
        solution = _get_result(task_id, timeout=timeout)
        token = solution["gRecaptchaResponse"]
        print(f"[capsolver] token recebido (len={len(token)}). injetando na página...")
        # a injeção acontece no MESMO contexto onde o sitekey foi encontrado
        # (dentro do iframe do Facebook, se foi lá que achamos) — o
        # driver.switch_to.frame de cima já deixou o driver nesse contexto
        callback_info = driver.execute_script(_INJECT_TOKEN_JS, token)
        print(f"[capsolver] token injetado. callback={callback_info}")
    finally:
        driver.switch_to.default_content()
    return True


def _click_checkbox_if_present(driver) -> None:
    """Clica no checkbox 'Não sou um robô' via CDP (mouse real, isTrusted=true)
    se ele estiver visível no frame atual — dentro do sub-iframe title='reCAPTCHA'
    que o próprio widget do Google renderiza (aninhado dentro do iframe do
    Facebook). Não é erro se não encontrar; nesse caso segue sem clicar."""
    recaptcha_iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[title='reCAPTCHA'], iframe[title*='recaptcha']")
    for frame in recaptcha_iframes:
        if not frame.is_displayed():
            continue
        try:
            driver.switch_to.frame(frame)
            checkbox = driver.find_elements(By.CSS_SELECTOR, "#recaptcha-anchor, .recaptcha-checkbox-border")
            if checkbox and checkbox[0].is_displayed():
                _cdp_click(driver, checkbox[0])
                print("[capsolver] checkbox 'Não sou um robô' clicado")
                time.sleep(1.5)
            driver.switch_to.parent_frame()
            return
        except Exception as e:
            print(f"[capsolver] erro ao clicar no checkbox: {type(e).__name__}: {e}")
            try:
                driver.switch_to.parent_frame()
            except Exception:
                pass
    print("[capsolver] nenhum iframe de checkbox reCAPTCHA visível encontrado")


def _cdp_click(driver, element) -> None:
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


# mantido como alias — o checkpoint do Facebook e as demais telas do fluxo
# usam o mesmo caminho de detecção (topo da página ou iframe embutido) agora
# unificado em solve_recaptcha_v2.
solve_recaptcha_enterprise = solve_recaptcha_v2
