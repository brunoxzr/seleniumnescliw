"""Configura a API key na extensão CapSolver instalada no perfil do AdsPower
(extensions/capsolver/, carregada localmente — sem 'key' fixa no manifest.json,
então o ID da extensão é derivado do caminho no disco e descoberto em tempo de
execução via CDP, não é hardcoded).

A extensão guarda a key em chrome.storage.local sob a chave "config" como
{"apiKey": "...", ...} (confirmado lendo background.js) — escrever direto ali
evita ter que abrir e interagir com a UI de configurações da extensão.
"""
import os

CONFIG_KEY = "config"


def _find_extension_id(driver) -> str | None:
    """Descobre o ID da extensão CapSolver ativa no perfil, via CDP
    Target.getTargets — a extensão roda um service_worker cujo alvo tem uma URL
    do tipo chrome-extension://<id>/background.js."""
    targets = driver.execute_cdp_cmd("Target.getTargets", {})
    for target in targets.get("targetInfos", []):
        url = target.get("url", "")
        if url.startswith("chrome-extension://") and "background.js" in url:
            return url.split("chrome-extension://")[1].split("/")[0]
    return None


def set_api_key(driver, api_key: str | None = None) -> bool:
    """Escreve a API key do CapSolver no chrome.storage.local da extensão.
    Retorna False se a extensão não estiver instalada/ativa nesse perfil
    (não é erro — só significa que precisa configurar a pasta no AdsPower)."""
    api_key = api_key or os.environ.get("CAPSOLVER_API_KEY", "")
    if not api_key:
        return False

    ext_id = _find_extension_id(driver)
    if ext_id is None:
        return False

    original_window = driver.current_window_handle
    driver.switch_to.new_window("tab")
    try:
        driver.get(f"chrome-extension://{ext_id}/www/index.html#/popup")
        driver.execute_async_script(
            """
            const apiKey = arguments[0];
            const configKey = arguments[1];
            const done = arguments[2];
            chrome.storage.local.get(configKey, (result) => {
                const config = result[configKey] || {};
                config.apiKey = apiKey;
                chrome.storage.local.set({[configKey]: config}, () => done(true));
            });
            """,
            api_key,
            CONFIG_KEY,
        )
    finally:
        driver.close()
        driver.switch_to.window(original_window)
    return True
