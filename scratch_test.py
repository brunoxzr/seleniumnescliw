from dotenv import load_dotenv
load_dotenv()
from app.adspower.driver import open_driver, close_driver
from app.automations.facebook_login import ensure_logged_in
from app.automations.facebook_domain import add_domain

PROFILE = "k1eqp6ab"
BUSINESS_ID = "1486539589945495"
DOMAIN = "lucioelberalvesferreira.empresaconectadabrs.com"

driver = open_driver(PROFILE)
try:
    ensure_logged_in(driver, PROFILE)
    print("Login OK")

    driver.get(f"https://business.facebook.com/latest/business_home?business_id={BUSINESS_ID}")
    import time
    time.sleep(3)
    print("URL:", driver.current_url)

    if "loginpage" in driver.current_url:
        from selenium.webdriver.common.by import By
        cont_btn = driver.find_element(By.XPATH, "//*[contains(text(),'Continue with Facebook')]")
        driver.execute_script("arguments[0].click();", cont_btn)
        time.sleep(4)
        print("URL AFTER CONTINUE:", driver.current_url)

    meta_code = add_domain(driver, BUSINESS_ID, DOMAIN)
    print("META CODE:", meta_code)
finally:
    close_driver(driver, PROFILE)
