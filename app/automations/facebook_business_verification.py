"""Inicia a verificação de negócio no Business Manager, a partir da tela de
Contas do WhatsApp (link 'Iniciar verificação' no painel de detalhes da conta).

Fluxo mapeado manualmente pelo usuário:
0. Contas do WhatsApp -> conta criada -> "Verificação da empresa: Não
   verificado" -> link "Iniciar verificação" (abre o mesmo wizard que a
   Central de Segurança)
1. Modal "Verificar <nome da empresa>" -> "Começar"
2. País (já vem Brasil) -> "Avançar"
3. Tipo de empresa: "Empresa individual" -> "Avançar"
4. "Tem registro" -> "Avançar"
5. CNPJ no campo de identificação fiscal -> "Avançar" (nome/endereço já vêm preenchidos)
6. Endereço: "Avançar" (já preenchido)
7. Telefone/site: aqui a automação PARA — o próximo passo envia um código de
   confirmação por SMS/ligação para o número, que precisa ser resolvido manualmente.

A função é escrita como uma máquina de estados: a cada iteração detecta em qual
tela o navegador está (por marcadores visuais únicos) e executa só o passo
correspondente. Isso a torna idempotente — se uma etapa anterior falhar, pausar
para o usuário resolver manualmente, e o orquestrador chamar a função de novo,
ela não recomeça do zero: reconhece a tela atual (que pode já estar mais
avançada por causa da intervenção manual) e continua dali.

Clique: os botões dessa tela ("Iniciar verificação", "Começar", "Avançar") são
divs role=button sem <button>/<a> nativo por baixo. Testado ao vivo: eventos de
mouse SINTÉTICOS (element.dispatchEvent(new MouseEvent(...))) não disparam o
handler de clique do React aqui (dispatchEvent gera isTrusted=false, e o
listener parece exigir um evento confiável). Um clique de mouse REAL via
Chrome DevTools Protocol (Input.dispatchMouseEvent, isTrusted=true) é a única
forma que funcionou nos testes manuais — e funciona em ~50ms quando aplicado
uma única vez. Reclicar no mesmo elemento antes do efeito do primeiro clique
renderizar pode DESFAZER o próprio progresso (toggle do botão/modal), então
aqui cada clique é feito uma única vez, seguido de uma espera fixa generosa —
sem lógica de "confirmar e reclicar", que se mostrou instável.
"""
import time

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .facebook_scope import ensure_business_scope
from .facebook_whatsapp import whatsapp_url


def _find_visible_by_text(driver, text: str):
    """Primeiro elemento visível cujo texto (próprio ou de descendentes)
    contém `text`, priorizando o nó mais interno (menos texto ao redor) —
    tende a ser o rótulo real do botão, não um container maior que também
    contém o mesmo texto em algum lugar dentro dele."""
    candidates = [
        el for el in driver.find_elements(By.XPATH, f"//*[contains(text(),'{text}') or contains(.,'{text}')]")
        if el.is_displayed()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda e: len(e.text or ""))
    return candidates[0]


def _is_visible_text(driver, text: str) -> bool:
    return _find_visible_by_text(driver, text) is not None


def _resolve_clickable(el):
    """Sobe do nó de texto até o ancestral clicável mais próximo (div
    role=button, <button> nativo, ou <a>). Se nenhum for encontrado, usa o
    próprio nó de texto como alvo do clique."""
    btn = el.find_elements(
        By.XPATH,
        "./ancestor-or-self::div[@role='button'] | ./ancestor-or-self::button "
        "| ./ancestor-or-self::a[@role='link' or @href]",
    )
    return btn[0] if btn else el


def _cdp_click(driver, element) -> None:
    """Clique de mouse real via Chrome DevTools Protocol (ver docstring do módulo)."""
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


def _wait_page_settled(driver, timeout: float = 4.0) -> None:
    """Espera document.readyState virar 'complete' e dá uma folga extra curta
    depois disso. Diagnosticado ao vivo: um clique disparado logo após
    navegar para uma página nova pode encontrar o texto do botão no HTML
    (presente desde o primeiro paint) mas o handler de clique do React ainda
    não foi anexado (a hidratação/montagem dos componentes interativos
    acontece depois do primeiro paint) — o clique "não dá erro" mas não tem
    efeito nenhum, porque bateu num elemento que ainda não está de fato
    interativo. Esperar o carregamento assentar antes de clicar evita isso."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if driver.execute_script("return document.readyState;") == "complete":
                break
        except Exception:
            pass
        time.sleep(0.1)
    time.sleep(0.5)  # folga extra: readyState 'complete' não garante que o React já hidratou


def _click_text_once(driver, text: str, after: float = 1.0, settle_first: bool = False) -> bool:
    """Encontra o elemento com `text`, clica nele UMA VEZ via CDP, espera
    `after` segundos (tempo fixo para o efeito renderizar) e retorna se o
    elemento foi encontrado. Não reclica, não confirma — a máquina de estados
    do loop principal é quem decide, na PRÓXIMA iteração, se o clique teve
    efeito (reconhecendo a tela seguinte) ou não (reconhecendo a mesma tela
    de novo, e então é a própria iteração seguinte que tenta de novo).

    `settle_first`: espera a página estabilizar (ver _wait_page_settled) antes
    de clicar — usar quando esse clique pode acontecer logo após uma navegação
    (driver.get) recente, onde o handler de clique real pode ainda não ter
    sido anexado pelo React."""
    if settle_first:
        _wait_page_settled(driver)
    el = _find_visible_by_text(driver, text)
    if el is None:
        return False
    _cdp_click(driver, _resolve_clickable(el))
    time.sleep(after)
    return True


def _click_radio_option(driver, label_text: str) -> bool:
    option = _find_visible_by_text(driver, label_text)
    if option is None:
        return False
    radio_row = option.find_elements(By.XPATH, "./ancestor::div[.//input[@type='radio'] or @role='radio'][1]")
    target = radio_row[0].find_elements(By.CSS_SELECTOR, "input[type='radio']") if radio_row else []
    _cdp_click(driver, target[0] if target else (radio_row[0] if radio_row else option))
    return True


def _url_has_security_settings(driver) -> bool:
    try:
        return "/settings/security" in driver.current_url
    except Exception:
        return False


def open_verification_link(driver, business_id: str) -> bool:
    """Navega para a Central de Segurança usando o href resolvido do link
    'Iniciar verificação' da tela de Contas do WhatsApp
    (<a href="/settings/security/?business_id=X" target="_blank">).

    Esse é um <a href> real — driver.get(href) é o método confiável para ele
    (cliques sintéticos não navegam links reais; ver docstring do módulo).
    Retorna True se a URL confirmou a navegação."""
    if _url_has_security_settings(driver):
        return True

    link = None
    deadline = time.time() + 15
    while time.time() < deadline:
        links = [el for el in driver.find_elements(By.CSS_SELECTOR, "a[href*='/settings/security/']") if el.is_displayed()]
        if links:
            link = links[0]
            break
        time.sleep(0.3)
    if link is None:
        return False

    href = link.get_attribute("href") or f"https://business.facebook.com/settings/security/?business_id={business_id}"
    driver.get(href)
    time.sleep(1.5)
    return _url_has_security_settings(driver)


def start_business_verification(driver, business_id: str, cnpj: str) -> None:
    """Percorre o wizard de verificação de negócio até a tela de telefone/site,
    onde para para o usuário confirmar manualmente (SMS/ligação).

    Idempotente: detecta em qual tela o wizard já está antes de agir, em vez de
    sempre recomeçar do 'Iniciar verificação' — importante porque o usuário pode
    ter avançado manualmente durante uma pausa de intervenção anterior. Elementos
    stale (DOM re-renderizado entre a checagem e a ação) são absorvidos com retry
    silencioso — a máquina de estados já re-consulta o DOM do zero a cada volta.
    """
    try:
        driver.current_window_handle
    except Exception as e:
        raise RuntimeError(
            f"A janela do navegador foi fechada ({type(e).__name__}) — reabra o perfil "
            "no AdsPower (ou reabra a aba manualmente) e clique Continuar para tentar de novo."
        ) from e

    for stale_attempt in range(3):
        try:
            _run_verification_state_machine(driver, business_id, cnpj)
            return
        except StaleElementReferenceException:
            if stale_attempt == 2:
                raise
            time.sleep(0.5)


def _is_stuck_on_business_home(driver) -> bool:
    """A tela inicial 'Boa tarde, <nome>' (business_home) não tem nada do wizard
    de verificação nem da tela de Contas do WhatsApp — só os cards de
    'Desempenho da conta de anúncios' e 'Perfis'. Detectada pela combinação
    desses dois textos, que juntos não aparecem em nenhuma outra tela do fluxo."""
    return _is_visible_text(driver, "Desempenho da conta de anúncios") and _is_visible_text(driver, "Perfis")


def _go_to_whatsapp_accounts(driver, business_id: str) -> None:
    """Navega diretamente pela URL para Contas do WhatsApp — mais confiável do
    que clicar em 'Ir para Configurações da empresa' na tela home, que pode cair
    numa aba diferente (ex: Pages) dependendo de qual dos dois botões idênticos
    foi clicado."""
    driver.get(whatsapp_url(business_id))
    load_deadline = time.time() + 12
    while time.time() < load_deadline:
        if driver.find_elements(By.CSS_SELECTOR, "a[href*='/settings/security/']") or \
           _is_visible_text(driver, "Verificação da empresa"):
            break
        time.sleep(0.4)


def _run_verification_state_machine(driver, business_id: str, cnpj: str) -> None:
    def _log(msg):
        pass  # instrumentação de debug removida após diagnóstico concluído

    ensure_business_scope(driver, business_id)
    if _is_stuck_on_business_home(driver):
        _go_to_whatsapp_accounts(driver, business_id)

    # Limite por TEMPO (não por número de iterações) — o wizard é
    # copiloto: se o usuário interagir manualmente na tela a qualquer
    # momento (resolver um captcha, avançar uma tela que a automação não
    # reconheceu, fechar um popup), o loop absorve isso no próximo ciclo e
    # continua sozinho, sem exigir clique de "Continuar" no dashboard. Um
    # limite baseado em passos (usado antes) podia esgotar rápido demais
    # em telas com erro técnico transitório do Facebook, mesmo o usuário
    # estando pronto para ajudar — 6 minutos dão espaço de sobra tanto
    # para retries automáticos quanto para intervenção manual.
    deadline = time.time() + 360
    _step_i = 0
    while time.time() < deadline:
        _step_i += 1
        # a home ('Boa tarde, ...') pode reaparecer no meio do loop também (ex:
        # depois de ensure_business_scope ser chamado de novo por outro motivo)
        if _is_stuck_on_business_home(driver):
            _go_to_whatsapp_accounts(driver, business_id)
            continue

        # tela de erro técnico TEMPORÁRIO do próprio Facebook ("Ocorreu um
        # erro / Há um problema técnico com esse recurso...") — descoberta ao
        # vivo: às vezes o clique em "Iniciar verificação" abre esse modal de
        # erro em vez do modal real "Verificar <nome>", sem nenhum botão
        # "Começar" dentro dele. Sem essa checagem, o código ficava preso
        # tentando reconhecer "Começar" pra sempre nessa tela (que nunca
        # aparece ali). Fecha o modal de erro e deixa o loop tentar de novo —
        # na prática costuma funcionar na segunda tentativa.
        if _is_visible_text(driver, "Ocorreu um erro"):
            _log(f"step {_step_i}: Facebook error dialog ('Ocorreu um erro') — closing and retrying")
            close_btn = driver.find_elements(By.CSS_SELECTOR, "[role=dialog] [aria-label='Fechar'], [role=dialog] [aria-label='Close']")
            if close_btn:
                _cdp_click(driver, close_btn[0])
            else:
                driver.execute_script("document.activeElement.blur();")
                from selenium.webdriver.common.keys import Keys
                driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            time.sleep(1.5)
            continue

        # modal "Verificar <nome>" -> Começar — checado com prioridade máxima,
        # antes de qualquer outra coisa. A descrição desse modal contém as
        # palavras "endereço", "telefone" e "site" na mesma frase, o que colide
        # com os marcadores usados para reconhecer outras telas mais abaixo.
        if _is_visible_text(driver, "Começar"):
            _log(f"step {_step_i}: recognized 'Começar' modal, clicking")
            clicked = _click_text_once(driver, "Começar", after=1.5, settle_first=True)
            _log(f"step {_step_i}: click Começar done (found_element={clicked})")
            continue

        # tela final: telefone/site (procura indício de que chegamos lá e paramos).
        # Usa o CAMPO de telefone (input), não o texto solto "Telefone"/"Site",
        # para não colidir com a descrição do modal "Verificar <nome>".
        phone_field_present = bool(driver.find_elements(
            By.XPATH, "//input[contains(@aria-label,'elefone') or contains(@placeholder,'elefone')]"
        ))
        if phone_field_present and not _is_visible_text(driver, "Adicionar dados da empresa"):
            return  # chegou na etapa manual (telefone/site) — para aqui

        # tela "Adicionar dados da empresa" — CNPJ
        if _is_visible_text(driver, "Adicionar dados da empresa"):
            wait = WebDriverWait(driver, 10)
            dialog = wait.until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'Adicionar dados da empresa')]"))
            )
            dialog_container = dialog.find_elements(
                By.XPATH,
                "./ancestor::div[@role='dialog'][1] | ./ancestor::div[.//button or .//div[@role='button']][1]",
            )
            search_scope = dialog_container[0] if dialog_container else driver
            text_inputs = [
                i for i in search_scope.find_elements(By.TAG_NAME, "input")
                if i.is_displayed() and i.get_attribute("type") in (None, "text", "search")
            ]
            empty_inputs = [i for i in text_inputs if not (i.get_attribute("value") or "").strip()]
            if empty_inputs:
                driver.execute_script("arguments[0].focus(); arguments[0].value='';", empty_inputs[0])
                driver.execute_cdp_cmd("Input.insertText", {"text": cnpj})
                time.sleep(0.5)
            _click_text_once(driver, "Avançar", after=1.2)
            continue

        # "Sua empresa tem registro oficial?"
        if _is_visible_text(driver, "Tem registro"):
            _click_radio_option(driver, "Tem registro")
            time.sleep(0.6)
            _click_text_once(driver, "Avançar", after=1.2)
            continue

        # tipo de empresa
        if _is_visible_text(driver, "Empresa individual"):
            _click_radio_option(driver, "Empresa individual")
            time.sleep(0.6)
            _click_text_once(driver, "Avançar", after=1.2)
            continue

        # país (já vem Brasil selecionado) — tela genérica "Avançar" sem os
        # marcadores acima; só avança se reconhecer o contexto do wizard
        if _is_visible_text(driver, "Selecionar um país") or _is_visible_text(driver, "País"):
            _click_text_once(driver, "Avançar", after=1.2)
            continue

        # tela de endereço (já preenchida) — mesma lógica: reconhece pelo rótulo
        if _is_visible_text(driver, "Endereço") and _is_visible_text(driver, "Avançar"):
            _click_text_once(driver, "Avançar", after=1.2)
            continue

        # fallback genérico: qualquer tela do wizard com um botão "Avançar"
        # visível é assumida como uma etapa já preenchida (pelo próprio wizard
        # ou pelo usuário manualmente) — clica pra seguir em frente, sem exigir
        # mais nenhum marcador de texto específico da tela.
        if _is_visible_text(driver, "Avançar"):
            _click_text_once(driver, "Avançar", after=1.2)
            continue

        # se o link 'Iniciar verificação' está na tela (Contas do WhatsApp),
        # clica nele. Checado ANTES do botão de mesmo texto da Central de
        # Segurança logo abaixo — os dois têm o MESMO TEXTO ("Iniciar
        # verificação"). Distinguir pelo elemento (link com href de
        # /settings/security/), não pelo texto, resolve a ambiguidade — MAS só
        # quando ainda NÃO estamos na Central de Segurança: um link com esse
        # href pode continuar presente no DOM (menu, breadcrumb) mesmo depois
        # de já termos chegado lá.
        pending_verify_link = (
            not _url_has_security_settings(driver)
            and bool(driver.find_elements(By.CSS_SELECTOR, "a[href*='/settings/security/']"))
        )
        if pending_verify_link:
            _log(f"step {_step_i}: pending_verify_link, opening")
            navigated = open_verification_link(driver, business_id)
            _log(f"step {_step_i}: open_verification_link done (navigated={navigated})")
            if not navigated:
                driver.get(f"https://business.facebook.com/settings/security/?business_id={business_id}")
                time.sleep(2)
            continue

        # tela da Central de Segurança (destino do link clicado acima): tem um
        # BOTÃO azul "Iniciar verificação" (role=button, não <a href>) que abre
        # o modal 'Verificar <nome>'. Só chega aqui se a checagem do link acima
        # não bateu, então não há ambiguidade entre os dois.
        if _is_visible_text(driver, "Iniciar verificação"):
            _log(f"step {_step_i}: recognized 'Iniciar verificação' button, clicking")
            clicked = _click_text_once(driver, "Iniciar verificação", after=2.0, settle_first=True)
            _log(f"step {_step_i}: click Iniciar verificação done (found_element={clicked})")
            continue

        # não estamos em nenhuma tela reconhecida do wizard nem na tela de
        # Contas do WhatsApp com o link disponível — navega pra lá de novo
        if not _is_visible_text(driver, "Verificar"):
            _log(f"step {_step_i}: nothing recognized, no 'Verificar' text either -> go_to_whatsapp")
            _go_to_whatsapp_accounts(driver, business_id)
            continue

        # estado não reconhecido — antes de desistir, dá mais uma chance: a
        # página pode ainda estar renderizando (transição/fade-in) e os
        # marcadores de texto ainda não bateram por puro timing. Espera ativa
        # curta e tenta reconhecer de novo antes de pausar pro usuário.
        recheck_deadline = time.time() + 5
        recognized_now = False
        while time.time() < recheck_deadline:
            if (
                _is_stuck_on_business_home(driver) or _is_visible_text(driver, "Começar")
                or _is_visible_text(driver, "Adicionar dados da empresa")
                or _is_visible_text(driver, "Tem registro") or _is_visible_text(driver, "Empresa individual")
                or _is_visible_text(driver, "Selecionar um país") or _is_visible_text(driver, "País")
                or _is_visible_text(driver, "Avançar") or _is_visible_text(driver, "Verificar")
            ):
                recognized_now = True
                break
            time.sleep(0.5)

        if recognized_now:
            continue

        raise RuntimeError("Tela do wizard de verificação de negócio não reconhecida.")

    raise RuntimeError(
        "Wizard de verificação de negócio não chegou na tela de telefone/site em 6 "
        "minutos — verifique manualmente."
    )
