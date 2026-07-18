"""Conecta o Selenium ao Chrome já aberto pelo AdsPower via debuggerAddress.

Mantém um cache de driver por perfil em memória: abrir/fechar o Chrome inteiro
a cada etapa é lento, então reaproveita a mesma janela (já logada) entre
execuções sucessivas do mesmo perfil, em vez de encerrar o navegador toda vez.
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from .client import start_profile, stop_profile

_driver_cache: dict[str, webdriver.Chrome] = {}


def _is_driver_alive(driver: webdriver.Chrome) -> bool:
    try:
        driver.current_window_handle
        return True
    except Exception:
        return False


def _connect(profile_id: str) -> webdriver.Chrome:
    profile_data = start_profile(profile_id)
    debug_port = profile_data["debug_port"]
    chromedriver_path = profile_data["webdriver"]

    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")

    service = Service(executable_path=chromedriver_path)
    return webdriver.Chrome(service=service, options=options)


def open_driver(profile_id: str) -> "ResilientDriver":
    cached = _driver_cache.get(profile_id)
    if cached is None or not _is_driver_alive(cached):
        if cached is not None:
            _driver_cache.pop(profile_id, None)
        cached = _connect(profile_id)
        _driver_cache[profile_id] = cached
    return ResilientDriver(profile_id, cached)


def close_driver(driver: webdriver.Chrome, profile_id: str) -> None:
    """Não fecha mais o Chrome a cada chamada — mantém a janela aberta para
    reaproveitamento entre execuções. Use force_close_driver para encerrar de
    fato (ex: ao trocar de perfil ou finalizar o dia de trabalho)."""
    pass


def force_close_driver(profile_id: str) -> None:
    """Encerra de fato o Chrome do perfil e limpa o cache — chame quando quiser
    liberar o perfil (ex: trocar para outro, ou parar de trabalhar por hoje)."""
    driver = _driver_cache.pop(profile_id, None)
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass
    stop_profile(profile_id)


class ResilientDriver:
    """Encapsula um webdriver.Chrome e reconecta automaticamente quando a sessão
    CDP cai por inatividade prolongada (ex: pausa manual de 10+ minutos), mesmo
    com a janela do Chrome continuando aberta de verdade. Delega todos os
    atributos/métodos para o driver real via __getattr__, então funciona como
    um Selenium WebDriver normal em qualquer código que o receba.

    A conexão WebSocket do ChromeDriver com o Chrome pode expirar mesmo que a
    janela nunca tenha sido fechada — nesse caso o Selenium lança
    NoSuchWindowException/InvalidSessionIdException em QUALQUER comando, não só
    nos que de fato dependem da janela. Reconectar (sem fechar o Chrome) resolve.
    """

    _RECONNECT_EXCEPTIONS = ("NoSuchWindowException", "InvalidSessionIdException")

    def __init__(self, profile_id: str, driver: webdriver.Chrome):
        object.__setattr__(self, "_profile_id", profile_id)
        object.__setattr__(self, "_driver", driver)

    def _reconnect(self) -> None:
        new_driver = _connect(self._profile_id)
        object.__setattr__(self, "_driver", new_driver)
        _driver_cache[self._profile_id] = new_driver

    def __getattr__(self, name):
        attr = getattr(self._driver, name)
        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except Exception as e:
                if type(e).__name__ not in self._RECONNECT_EXCEPTIONS:
                    raise
                self._reconnect()
                # tenta a MESMA chamada de novo na nova conexão — cobre o caso
                # comum de "a sessão caiu bem na hora desse comando"
                reconnected_attr = getattr(self._driver, name)
                return reconnected_attr(*args, **kwargs)

        return wrapper

    def __setattr__(self, name, value):
        setattr(self._driver, name, value)
