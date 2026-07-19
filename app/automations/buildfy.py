"""Automação do Buildfy (buildfyapp.vercel.app): login, listar sites, criar site, extrair dados."""
import os
import time
from dataclasses import dataclass

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

from .driver_utils import safe_url

def _normalize_base_url(url: str) -> str:
    """Remove barra final e sufixos de rota comuns (ex: '/login' colado sem
    querer ao copiar o link) — o resto do módulo monta as rotas por conta
    própria (/login, /meus-sites, /sites/{id}, ...) a partir da raiz."""
    url = url.rstrip("/")
    for suffix in ("/login", "/meus-sites", "/criar"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


# o link da plataforma pode mudar (ex: novo deploy na Vercel) — configurável
# via BUILDFY_BASE_URL no .env, com o link atual como padrão
BASE_URL = _normalize_base_url(os.environ.get("BUILDFY_BASE_URL") or "https://buildfyapp.vercel.app")

SET_VALUE_JS = """
const el = arguments[0];
const value = arguments[1];
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
nativeSetter.call(el, value);
el.dispatchEvent(new Event('input', { bubbles: true }));
el.dispatchEvent(new Event('change', { bubbles: true }));
"""


@dataclass
class SiteData:
    empresa: str
    cnpj: str
    url: str
    dominio: str  # sem https://
    logradouro: str = ""
    complemento: str = ""
    bairro: str = ""
    cidade: str = ""
    estado: str = ""
    cep: str = ""
    email_contato: str = ""


def _set_value(driver, element, value: str) -> None:
    driver.execute_script(SET_VALUE_JS, element, value)


def get_credentials_for_slot(slot: str | None) -> tuple[str, str]:
    """Resolve email/senha do Buildfy para um robô específico (BUILDFY_EMAIL_A,
    BUILDFY_EMAIL_B, ...), caindo para BUILDFY_EMAIL/BUILDFY_PASSWORD (conta
    padrão, sem sufixo) se a variável específica do slot não estiver definida —
    permite ter só uma conta configurada e ainda assim todos os robôs funcionarem."""
    if slot:
        email = os.environ.get(f"BUILDFY_EMAIL_{slot}")
        password = os.environ.get(f"BUILDFY_PASSWORD_{slot}")
        if email and password:
            return email, password

    email = os.environ.get("BUILDFY_EMAIL")
    password = os.environ.get("BUILDFY_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            f"Nenhuma credencial do Buildfy configurada para o robô '{slot}' "
            f"(defina BUILDFY_EMAIL_{slot}/BUILDFY_PASSWORD_{slot}, ou BUILDFY_EMAIL/"
            "BUILDFY_PASSWORD como conta padrão, no .env)."
        )
    return email, password


def ensure_logged_in(driver, slot: str | None = None) -> None:
    email, password = get_credentials_for_slot(slot)

    driver.get(f"{BASE_URL}/login")
    time.sleep(1.5)
    if "/login" not in safe_url(driver):
        return  # já logado, redirecionou

    wait = WebDriverWait(driver, 15)
    email_field = wait.until(EC.presence_of_element_located((By.ID, "identifier")))
    _set_value(driver, email_field, email)

    pwd_field = driver.find_element(By.ID, "password")
    _set_value(driver, pwd_field, password)

    submit_btn = driver.find_element(By.CSS_SELECTOR, "button[type=submit]")
    driver.execute_script("arguments[0].click();", submit_btn)
    time.sleep(1)

    if "/login" in safe_url(driver):
        raise RuntimeError("Falha no login do Buildfy: continuou na tela de login.")


def list_all_sites(driver, limit: int = 200) -> list[tuple[str, str, str]]:
    """Varre 'Meus Sites' e retorna todos (cnpj, site_id, empresa) encontrados,
    independente de já terem sido processados ou não."""
    driver.get(f"{BASE_URL}/meus-sites")
    time.sleep(1.5)

    results: list[tuple[str, str, str]] = []
    manage_links = driver.find_elements(By.CSS_SELECTOR, "a[title='Gerenciar site']")
    for link in manage_links:
        href = link.get_attribute("href") or ""
        site_id = href.rstrip("/").split("/")[-1]

        cnpj = None
        empresa = ""
        ancestor = link
        for _ in range(6):
            try:
                ancestor = ancestor.find_element(By.XPATH, "..")
            except Exception:
                break
            row_text = ancestor.text.replace("\n", " ")
            for token in row_text.split():
                digits = "".join(ch for ch in token if ch.isdigit())
                if len(digits) == 14:
                    cnpj = digits
                    break
            if cnpj:
                lines = ancestor.text.split("\n")
                empresa = lines[0].strip() if lines else ""
                break

        if not cnpj:
            continue
        results.append((cnpj, site_id, empresa))
        if len(results) >= limit:
            break
    return results


def list_pending_sites(driver, already_processed: set[str], limit: int = 50) -> list[tuple[str, str]]:
    """Varre 'Meus Sites' e retorna pares (cnpj, site_id) ainda não processados.

    site_id é o ID numérico interno usado na rota /sites/{id} (link 'Gerenciar site').
    """
    driver.get(f"{BASE_URL}/meus-sites")
    time.sleep(1.5)

    pending: list[tuple[str, str]] = []
    manage_links = driver.find_elements(By.CSS_SELECTOR, "a[title='Gerenciar site']")
    for link in manage_links:
        href = link.get_attribute("href") or ""
        site_id = href.rstrip("/").split("/")[-1]

        # sobe níveis de ancestral até achar um container cujo texto contenha um CNPJ (14 dígitos)
        cnpj = None
        ancestor = link
        for _ in range(6):
            try:
                ancestor = ancestor.find_element(By.XPATH, "..")
            except Exception:
                break
            row_text = ancestor.text.replace("\n", " ")
            for token in row_text.split():
                digits = "".join(ch for ch in token if ch.isdigit())
                if len(digits) == 14:
                    cnpj = digits
                    break
            if cnpj:
                break

        if not cnpj or cnpj in already_processed:
            continue
        pending.append((cnpj, site_id))
        if len(pending) >= limit:
            break
    return pending


def create_site_from_cnpj(driver, cnpj: str, email_contato: str = "") -> str:
    """Cria um site novo no Buildfy a partir de um CNPJ: template IA, domínio aleatório, publicar.

    Retorna o site_id numérico da página de detalhe pra onde a criação redireciona.
    """
    driver.get(f"{BASE_URL}/criar")
    wait = WebDriverWait(driver, 15)

    cnpj_field = wait.until(EC.presence_of_element_located((By.ID, "siteCnpj")))
    _set_value(driver, cnpj_field, cnpj)

    if email_contato:
        email_field = driver.find_element(By.ID, "siteEmail")
        _set_value(driver, email_field, email_contato)

    # domínio: deixa "Aleatório (menor ocupação)" (value="") — já é o default, nada a fazer

    template_select_el = driver.find_element(By.ID, "siteTemplate")
    Select(template_select_el).select_by_value("ia")

    time.sleep(1)
    publicar_btn = driver.find_element(By.CSS_SELECTOR, "button[type=submit]")
    wait.until(lambda d: publicar_btn.get_attribute("disabled") is None)
    driver.execute_script("arguments[0].click();", publicar_btn)

    # geração via IA é mais lenta — aguarda navegação para página de detalhe do site
    WebDriverWait(driver, 120).until(lambda d: "/sites/" in safe_url(d))
    path = safe_url(driver).split("?")[0].rstrip("/")
    return path.split("/")[-1]


def get_site_data(driver, site_id: str, cnpj: str = "") -> SiteData:
    """Abre a página de detalhes do site (/sites/{id}, aba Informações) e extrai os dados exibidos.

    Estrutura real (verificada em produção): cada campo é um
    div.page__InfoRow-... contendo span.page__InfoLabel-... (ex: "Empresa")
    e span.page__InfoValue-... (o valor), classes com hash de styled-components
    mas prefixo estável entre builds.
    """
    driver.get(f"{BASE_URL}/sites/{site_id}")
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[class*='page__InfoRow']")))

    fields: dict[str, str] = {}
    rows = driver.find_elements(By.CSS_SELECTOR, "[class*='page__InfoRow']")
    for row in rows:
        try:
            label_els = row.find_elements(By.CSS_SELECTOR, "[class*='page__InfoLabel']")
            value_els = row.find_elements(By.CSS_SELECTOR, "[class*='page__InfoValue']")
            if not label_els or not value_els:
                continue
            # o botão "Copiar" fica dentro da mesma row; pega só o texto direto do
            # próprio elemento de label/valor, sem herdar texto de filhos extras
            label = label_els[0].text.strip()
            value = value_els[0].text.strip()
            if label:
                fields[label.lower()] = value
        except Exception:
            continue

    # o link do site pode não ser .com (ex: .store, .net) — tenta achar qualquer
    # link externo visível na página antes de restringir por TLD específico
    url_links = driver.find_elements(By.XPATH, "//a[contains(@href,'http')]")
    url_els = [
        a for a in url_links
        if a.get_attribute("href") and BASE_URL not in a.get_attribute("href")
    ]
    if not url_els:
        raise RuntimeError(
            f"Não foi possível encontrar o link do site na página de detalhes (site_id={site_id}) "
            "— verifique manualmente se a página carregou corretamente."
        )
    url = url_els[0].get_attribute("href")
    dominio = url.replace("https://", "").replace("http://", "").rstrip("/")

    return SiteData(
        empresa=fields.get("empresa", ""),
        cnpj=fields.get("cnpj", "") or cnpj,
        url=url,
        dominio=dominio,
        logradouro=fields.get("logradouro", ""),
        complemento=fields.get("complemento", ""),
        bairro=fields.get("bairro", ""),
        cidade=fields.get("cidade", ""),
        estado=fields.get("estado", ""),
        cep=fields.get("cep", ""),
    )


def apply_meta_tag(driver, site_id: str, meta_content_value: str) -> None:
    """Cola o código da meta-tag do Facebook na aba Verificação (?tab=facebook) e aplica."""
    driver.get(f"{BASE_URL}/sites/{site_id}?tab=facebook")
    wait = WebDriverWait(driver, 15)
    field = wait.until(EC.presence_of_element_located((By.ID, "fbCode")))
    _set_value(driver, field, meta_content_value)

    apply_btn = driver.find_element(By.XPATH, "//button[contains(., 'Aplicar meta tag')]")
    driver.execute_script("arguments[0].click();", apply_btn)
    time.sleep(1)
