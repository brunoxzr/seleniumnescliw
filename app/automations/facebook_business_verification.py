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
"""
import time

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .facebook_scope import ensure_business_scope
from .facebook_whatsapp import whatsapp_url


def _find_visible_by_text(driver, texts: list[str]):
    for xpath_text in texts:
        # contains(text(), ...) só olha nós de texto DIRETOS do elemento — se o
        # texto estiver quebrado em spans filhos, não bate nada. contains(., ...)
        # (todo o texto, incluindo descendentes) cobre esse caso também.
        candidates = [
            el for el in driver.find_elements(
                By.XPATH, f"//*[contains(text(),'{xpath_text}') or contains(., '{xpath_text}')]"
            )
            if el.is_displayed()
        ]
        if candidates:
            # prioriza o elemento mais interno (menos texto extra ao redor),
            # que costuma ser o nó de texto real do botão
            candidates.sort(key=lambda e: len(e.text or ""))
            return candidates[0]
    return None


def _click_js(driver, element) -> None:
    # .click() sintético simples não dispara handlers React ligados a
    # mousedown/mouseup (em vez de onclick nativo) em vários componentes dessa
    # tela — dispara a sequência completa de eventos de mouse, que o React
    # reconhece como interação real de usuário.
    driver.execute_script(
        """
        const el = arguments[0];
        el.scrollIntoView({block: 'center'});
        const rect = el.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
            el.dispatchEvent(new MouseEvent(type, {
                bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy
            }));
        }
        """,
        element,
    )


def _click_button_with_text(driver, texts: list[str], timeout: float = 8) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        el = _find_visible_by_text(driver, texts)
        if el is not None:
            # o texto pode estar num nó filho; sobe até o botão/role=button/link mais próximo
            btn = el.find_elements(
                By.XPATH,
                "./ancestor-or-self::div[@role='button'] | ./ancestor-or-self::button "
                "| ./ancestor-or-self::a[@role='link' or @href]",
            )
            target = btn[0] if btn else el
            try:
                _click_js(driver, target)
                return
            except Exception as e:
                last_error = e
        time.sleep(0.3)
    raise RuntimeError(f"Botão com texto {texts} não encontrado ou não clicável na tela ({last_error}).")


def _click_radio_option(driver, label_texts: list[str]) -> bool:
    option = _find_visible_by_text(driver, label_texts)
    if option is None:
        return False
    radio_row = option.find_elements(By.XPATH, "./ancestor::div[.//input[@type='radio'] or @role='radio'][1]")
    if radio_row:
        radio = radio_row[0].find_elements(By.CSS_SELECTOR, "input[type='radio']")
        _click_js(driver, radio[0] if radio else radio_row[0])
    else:
        _click_js(driver, option)
    return True


def _is_visible_text(driver, text: str) -> bool:
    return bool([el for el in driver.find_elements(By.XPATH, f"//*[contains(., '{text}')]") if el.is_displayed()])


def _url_has_security_settings(driver) -> bool:
    try:
        return "/settings/security" in driver.current_url
    except Exception:
        return False


def open_verification_link(driver, business_id: str) -> bool:
    """Clica no link 'Iniciar verificação' da tela de Contas do WhatsApp
    (<a href="/settings/security/?business_id=X" target="_blank">). Tenta
    múltiplas estratégias de clique em sequência, confirmando sucesso pela URL
    de fato mudar para /settings/security — não apenas "o clique não lançou
    exceção" (que não garante que o handler de navegação disparou).

    Retorna True se confirmou a navegação; False caso nenhuma estratégia tenha
    funcionado (o chamador decide o que fazer — ex: navegar direto por URL).
    """
    if _url_has_security_settings(driver):
        return True

    verify_link = None
    deadline = time.time() + 15
    while time.time() < deadline:
        links = [
            el for el in driver.find_elements(By.CSS_SELECTOR, "a[href*='/settings/security/']")
            if el.is_displayed()
        ]
        if links:
            verify_link = links[0]
            break
        time.sleep(0.3)

    if verify_link is None:
        return False

    windows_before = set(driver.window_handles)
    href = verify_link.get_attribute("href") or ""

    strategies = [
        lambda el: driver.execute_script(
            "arguments[0].removeAttribute('target'); arguments[0].click();", el
        ),
        lambda el: _click_js(driver, el),  # sequência completa de eventos de mouse
        lambda el: driver.get(href.replace("http://", "https://") if href else driver.current_url),
    ]

    for strategy in strategies:
        try:
            strategy(verify_link)
        except Exception:
            pass
        time.sleep(1.5)

        # se abriu aba nova, troca o foco antes de checar a URL
        new_windows = set(driver.window_handles) - windows_before
        if new_windows:
            driver.close()
            driver.switch_to.window(new_windows.pop())

        if _url_has_security_settings(driver):
            return True

        # elemento pode ter ficado stale entre tentativas — re-busca
        fresh_links = [
            el for el in driver.find_elements(By.CSS_SELECTOR, "a[href*='/settings/security/']")
            if el.is_displayed()
        ]
        if fresh_links:
            verify_link = fresh_links[0]

    return False


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
    ensure_business_scope(driver, business_id)
    if _is_stuck_on_business_home(driver):
        _go_to_whatsapp_accounts(driver, business_id)

    max_steps = 20
    for _ in range(max_steps):
        # a home ('Boa tarde, ...') pode reaparecer no meio do loop também (ex:
        # depois de ensure_business_scope ser chamado de novo por outro motivo)
        if _is_stuck_on_business_home(driver):
            _go_to_whatsapp_accounts(driver, business_id)
            continue
        # modal "Verificar <nome>" -> Começar — checado com prioridade máxima,
        # antes de qualquer outra coisa. A descrição desse modal contém as
        # palavras "endereço", "telefone" e "site" na mesma frase, o que colide
        # com os marcadores usados para reconhecer outras telas mais abaixo.
        if _is_visible_text(driver, "Começar"):
            _click_button_with_text(driver, ["Começar"], timeout=15)
            time.sleep(1.5)
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
            _click_button_with_text(driver, ["Avançar"])
            time.sleep(1.2)
            continue

        # "Sua empresa tem registro oficial?"
        if _is_visible_text(driver, "Tem registro"):
            _click_radio_option(driver, ["Tem registro"])
            time.sleep(0.6)
            _click_button_with_text(driver, ["Avançar"])
            time.sleep(1.2)
            continue

        # tipo de empresa
        if _is_visible_text(driver, "Empresa individual"):
            _click_radio_option(driver, ["Empresa individual"])
            time.sleep(0.6)
            _click_button_with_text(driver, ["Avançar"])
            time.sleep(1.2)
            continue

        # país (já vem Brasil selecionado) — tela genérica "Avançar" sem os
        # marcadores acima; só avança se reconhecer o contexto do wizard
        if _is_visible_text(driver, "Selecionar um país") or _is_visible_text(driver, "País"):
            _click_button_with_text(driver, ["Avançar"])
            time.sleep(1.2)
            continue

        # tela de endereço (já preenchida) — mesma lógica: reconhece pelo rótulo
        if _is_visible_text(driver, "Endereço") and _is_visible_text(driver, "Avançar"):
            _click_button_with_text(driver, ["Avançar"])
            time.sleep(1.2)
            continue

        # fallback genérico: qualquer tela do wizard com um botão "Avançar"
        # visível é assumida como uma etapa já preenchida (pelo próprio wizard
        # ou pelo usuário manualmente) — clica pra seguir em frente, sem exigir
        # mais nenhum marcador de texto específico da tela.
        if _is_visible_text(driver, "Avançar"):
            _click_button_with_text(driver, ["Avançar"])
            time.sleep(1.2)
            continue

        # tela da Central de Segurança (destino do link clicado na tela
        # anterior): tem um BOTÃO azul "Iniciar verificação" (diferente do link
        # <a> da tela de Contas do WhatsApp) que abre o modal 'Verificar <nome>'.
        # Sem essa checagem, o código nunca reconhecia essa tela e ficava indo
        # e voltando pra tela de WhatsApp em loop infinito.
        if _find_visible_by_text(driver, ["Iniciar verificação"]) is not None:
            _click_button_with_text(driver, ["Iniciar verificação"], timeout=5)
            time.sleep(2)
            continue

        # se o link 'Iniciar verificação' está na tela (Contas do WhatsApp),
        # clica nele. Não usar "Verificar" como marcador genérico aqui: a
        # própria tela de Contas do WhatsApp contém as palavras "Verificação"/
        # "verificado" na descrição, então esse texto sozinho não distingue
        # "ainda não abri o link" de "já cliquei e nada mudou".
        pending_verify_link = bool(driver.find_elements(By.CSS_SELECTOR, "a[href*='/settings/security/']"))
        if pending_verify_link:
            navigated = open_verification_link(driver, business_id)
            if not navigated:
                # nenhuma estratégia de clique funcionou — navega direto pela
                # URL como último recurso, garantidamente confiável
                driver.get(f"https://business.facebook.com/settings/security/?business_id={business_id}")
                time.sleep(2)
            continue

        # não estamos em nenhuma tela reconhecida do wizard nem na tela de
        # Contas do WhatsApp com o link disponível — navega pra lá de novo
        if not _is_visible_text(driver, "Verificar"):
            _go_to_whatsapp_accounts(driver, business_id)
            if _is_stuck_on_business_home(driver):
                continue
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
        "Wizard de verificação de negócio não chegou na tela de telefone/site após "
        f"{max_steps} passos — verifique manualmente."
    )
