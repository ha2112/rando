from selenium import webdriver
import time
options = webdriver.SafariOptions()

driver = webdriver.Safari()
driver.get("https://www.youtube.com")
time.sleep(3)  # Give time for potential auto-login/cookie-load
driver.get("https://accounts.google.com")
time.sleep(10)  # Give time for potential auto-login/cookie-load
driver.get("https://www.youtube.com/watch?v=hvJEmqvzkoo") ## private video
print(driver.title)
time.sleep(10)
driver.quit()