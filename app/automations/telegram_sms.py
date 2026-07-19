"""Automação do bot @numero_virtual_bot no Telegram Web: gera um número
virtual brasileiro para usar no telefone comercial do Facebook.

A conta do Telegram é ÚNICA e compartilhada entre os três robôs (A/B/C) — só
um pode mexer no chat do bot por vez (gerar número), então todo acesso passa
por `_lock` (serializa as chamadas entre threads dos robôs).

Cada número gerado tem um "ID" próprio dentro da mesma conversa (ex: "ID: 2").
A leitura do código SMS que chega depois é MANUAL por decisão do usuário — a
extração automatizada das mensagens do bot se mostrou frágil (Telegram Web
mantém as bolhas de mensagem com display:none/opacity:0 fora de uma janela de
transição, dificultando distinguir mensagens de robôs diferentes de forma
confiável). O dashboard mostra o `sms_id` salvo por CNPJ para que o usuário
localize manualmente o bloco certo no chat do Telegram.
"""
import re
import threading
import time

from selenium.webdriver.common.by import By

BASE_URL = "https://web.telegram.org/a/"
BOT_USERNAME = "numero_virtual_bot"

_lock = threading.Lock()


def _cdp_click(driver, element) -> None:
    """Clique de mouse real via Chrome DevTools Protocol — mesma técnica
    usada no restante do fluxo (Facebook), onde cliques sintéticos se
    mostraram pouco confiáveis em elementos com handlers React/similares."""
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


def _find_visible_by_text(driver, text: str, scope=None):
    root = scope or driver
    candidates = [
        el for el in root.find_elements(By.XPATH, f".//*[contains(text(),'{text}') or contains(.,'{text}')]")
        if el.is_displayed()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda e: len(e.text or ""))
    return candidates[0]


def _is_visible_text(driver, text: str) -> bool:
    return _find_visible_by_text(driver, text) is not None


def _click_text(driver, text: str, scope=None, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        el = _find_visible_by_text(driver, text, scope)
        if el is not None:
            _cdp_click(driver, el)
            return True
        time.sleep(0.3)
    return False


def _scroll_chat_to_bottom(driver) -> None:
    """Rola a conversa até a última mensagem — o teclado de botões do bot
    (Gerar Número, etc.) fica anexado à mensagem de boas-vindas mais recente;
    se a conversa abrir com o scroll no meio do histórico (comum quando há
    muitas mensagens antigas), esse elemento fica fora da área visível e
    is_displayed() retorna False mesmo ele existindo no DOM."""
    containers = driver.find_elements(By.CSS_SELECTOR, ".bubbles, [class*='MessageList'], [class*='messages-container']")
    for container in containers:
        try:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
        except Exception:
            pass
    time.sleep(0.5)


def _send_text_message(driver, text: str) -> None:
    inputs = [el for el in driver.find_elements(By.CSS_SELECTOR, "[contenteditable=true]") if el.is_displayed()]
    if not inputs:
        raise RuntimeError("Campo de mensagem do Telegram não encontrado.")
    inp = inputs[0]
    driver.execute_script("arguments[0].focus();", inp)
    driver.execute_cdp_cmd("Input.insertText", {"text": text})
    time.sleep(0.3)
    from selenium.webdriver.common.keys import Keys
    inp.send_keys(Keys.ENTER)
    time.sleep(2)


def open_bot_chat(driver) -> None:
    """Abre o chat do bot de números virtuais no Telegram Web e garante que
    o teclado principal ("Gerar Número" etc.) está visível.

    Navegar direto pela URL com hash (#@username) não abre o chat de forma
    confiável nessa versão do Telegram Web — o chat é aberto clicando nele na
    lista de conversas. O bot fica fixado no topo da lista, identificado pelo
    nome do chat, não pelo @username (que não aparece na UI de lista).

    O teclado de botões (Gerar Número, Recarregar, ...) é anexado à mensagem
    de boas-vindas que o bot manda em resposta a /start — não fica sempre
    visível/ativo; se a conversa já tiver histórico mais recente (ex: um
    número gerado antes), é preciso enviar /start de novo para o bot mandar
    esse teclado como a mensagem mais nova (e então rolar até ela)."""
    if BASE_URL not in (driver.current_url or ""):
        driver.get(BASE_URL)
        time.sleep(3)

    chat_link = _find_visible_by_text(driver, "Número virtual")
    if chat_link is None:
        raise RuntimeError(
            "Chat 'Número virtual - Receber SMS' não encontrado na lista de conversas — "
            "verifique se o Telegram Web está logado e a conversa com o bot existe."
        )
    clickable = chat_link.find_elements(By.XPATH, "./ancestor::a | ./ancestor::li")
    _cdp_click(driver, clickable[0] if clickable else chat_link)
    time.sleep(1.5)
    _scroll_chat_to_bottom(driver)

    if _is_visible_text(driver, "Gerar Número"):
        return

    _send_text_message(driver, "/start")
    _scroll_chat_to_bottom(driver)

    deadline = time.time() + 10
    while time.time() < deadline:
        if _is_visible_text(driver, "Gerar Número"):
            return
        _scroll_chat_to_bottom(driver)
        time.sleep(0.4)
    raise RuntimeError(
        f"Não foi possível abrir o menu do bot @{BOT_USERNAME} (botão 'Gerar Número' não "
        "apareceu mesmo após /start) — verifique manualmente o chat."
    )


def _conversation_text(driver) -> str:
    """Texto bruto de toda a área de mensagens visível.

    Descoberta ao vivo: as bolhas individuais (.bubble) ficam com
    display:none/opacity:0 na maior parte do tempo mesmo com conteúdo real
    dentro — o Telegram Web parece usar isso para transições/virtualização
    de lista, então checar .is_displayed() bolha por bolha não é confiável.
    O texto agregado do container (.messages-container ou .bubbles) reflete
    o conteúdo real renderizado independente desse estado, então é a fonte
    usada para extrair dados (ID, número, código SMS) via regex."""
    containers = driver.find_elements(By.CSS_SELECTOR, ".messages-container, .bubbles")
    for container in containers:
        text = (container.text or "").strip()
        if text:
            return text
    return ""


def _latest_message_text(driver) -> str:
    _scroll_chat_to_bottom(driver)
    return _conversation_text(driver)


def generate_number(driver, service: str = "Facebook", option_label: str = "Opção 1") -> tuple[str, str]:
    """Gera um número virtual novo para o serviço indicado.

    Retorna (sms_id, phone_without_ddi). `sms_id` é o "ID: N" que o bot
    associa a esse número — usado depois para achar o SMS certo no meio da
    conversa compartilhada. `phone_without_ddi` já vem sem o DDI (55) e sem
    o DDD internacional, pronto para colar no campo de telefone do Facebook.
    """
    with _lock:
        if not _click_text(driver, "Gerar Número"):
            raise RuntimeError("Botão 'Gerar Número' não encontrado no chat do bot.")
        time.sleep(1.5)

        if not _click_text(driver, service):
            raise RuntimeError(f"Serviço '{service}' não encontrado na lista do bot.")
        time.sleep(1.5)

        if not _click_text(driver, option_label):
            raise RuntimeError(f"Opção '{option_label}' não encontrada para o serviço '{service}'.")
        time.sleep(2)

        text = _latest_message_text(driver)
        id_match = re.search(r"ID:\s*(\d+)", text)
        phone_match = re.search(r"Sem DDI:\s*(\d+)", text) or re.search(r"Número:\s*\d*?(\d{10,11})\b", text)
        if not id_match or not phone_match:
            raise RuntimeError(
                f"Não foi possível extrair ID/número da resposta do bot. Mensagem recebida: {text[:300]}"
            )
        return id_match.group(1), phone_match.group(1)


