"""Gera código 2FA/TOTP via 2fa.cn a partir do secret armazenado no AdsPower.

O site é instável com Selenium: o textarea às vezes não recebe o texto e o botão
Submit às vezes não dispara a geração. Por isso o preenchimento usa CDP
Input.insertText (mais confiável que send_keys nesse app) com retry até o valor
ser confirmado, e o clique é repetido até o campo de saída ser preenchido.
"""
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://2fa.cn/"


def get_totp_code(driver, secret: str, max_attempts: int = 8) -> str:
    """Abre o 2fa.cn em uma aba nova (preservando a aba atual) e retorna o código gerado."""
    original_window = driver.current_window_handle
    driver.switch_to.new_window("tab")
    try:
        driver.get(BASE_URL)
        field = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.ID, "listToken")))
        time.sleep(1)

        for _ in range(max_attempts):
            field.click()
            driver.execute_script("arguments[0].value='';", field)
            driver.execute_cdp_cmd("Input.insertText", {"text": secret})
            time.sleep(0.5)
            if field.get_attribute("value") == secret:
                break
        else:
            raise RuntimeError("Não foi possível preencher o campo de secret no 2fa.cn")

        submit = driver.find_element(By.ID, "submit")
        output = driver.find_element(By.ID, "output")

        for _ in range(max_attempts):
            driver.execute_script("arguments[0].click();", submit)
            time.sleep(2)
            raw = output.get_attribute("value")
            if raw:
                return raw.split("|")[-1].strip()

        raise RuntimeError("2fa.cn não gerou o código após múltiplas tentativas")
    finally:
        driver.close()
        driver.switch_to.window(original_window)
