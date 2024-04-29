import logging
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

import hassapi as hass
import traceback
import adbase as ad

JS_WAIT = 1
WEB_WAIT = 5

class GoPortParkingController(hass.Hass):
    def initialize(self):
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-extensions')
        chrome_service = Service(executable_path='/usr/bin/chromedriver')
        self.driver = webdriver.Chrome(service=chrome_service, options=chrome_options)
        self.log(f"initialized parking driver")
        self.buttons = []
        for plate in self.args['plates']:
            entity_id = f"button.quick_buy_daily_{plate}"
            self.buttons.append(entity_id)
            entity = self.get_entity(entity_id)
            entity.set_state(state='unknown', attributes={'friendly_name': f"Quick buy daily goportparking pass for {plate}", 'plate': plate, 'detail': 'Ready to buy'})
        def filter_quick_buy_button(x):
            entity = x.get('entity_id')
            if isinstance(entity, list) and len(entity) == 1:
                entity = entity[0]
            if isinstance(entity, str):
                return entity.startswith('button.quick_buy_daily_')
            return False
        self.listen_event(self.book_daily, "call_service", domain="button", service="press", service_data=filter_quick_buy_button)
        self.run_daily(self.reset_state, '3:00:00')

    def terminate(self):
        try:
            self.driver.close()
        except Exception:
            self.log(f"failed to terminate properly: {traceback.format_exc()}")

    def reset_state(self, kwargs):
        for plate in self.args['plates']:
            entity_id = f"button.quick_buy_daily_{plate}"
            entity = self.get_entity(entity_id)
            entity.set_state(state='unknown', attributes={'detail': 'Ready to buy'})

    def book_daily(self, event_name, data, kwargs):
        entity = data['service_data']['entity_id']
        if isinstance(entity, list) and len(entity) == 1:
            entity = entity[0]
        if not isinstance(entity, str) or not entity.startswith('button.quick_buy_daily_'):
            self.log(f"entity wasn't expected: {entity}")
            return
        button_attrs = self.get_state(entity, attribute="all")
        self.log(f"attrs of {entity} = {button_attrs}")
        plate = button_attrs['attributes']['plate']
        self.get_entity(entity).set_state(state='opening_portal', attributes={'detail': 'Opening portal'})
        self.log(f"buying daily: navigating to login")
        self.driver.get("https://goportparking.org/rppportal/login.xhtml")
        time.sleep(WEB_WAIT)
        self.log(f"buying daily: logging in")
        username = self.driver.find_element(By.ID, "username")
        username.clear()
        username.send_keys(self.args['username'])
        password = self.driver.find_element(By.ID, "password")
        password.clear()
        password.send_keys(self.args['password'])
        self.driver.find_element(By.ID, "login").click()
        self.get_entity(entity).set_state(state='logging_in', attributes={'detail': 'Logging in...'})
        time.sleep(WEB_WAIT)
        if self.driver.current_url != 'https://goportparking.org/rppportal/index.xhtml':
            self.log(f"Login seems to have failed")
            self.get_entity(entity).set_state(state='login_failed', attributes={'detail': 'Login failed'})
            return
        self.log(f"buying daily: activating quick-buy (on url {self.driver.current_url})")
        self.get_entity(entity).set_state(state='purchasing_daily', attributes={'detail': 'Purchasing daily pass...'})
        try:
            quick_buy = self.driver.find_element(By.PARTIAL_LINK_TEXT, plate)
        except NoSuchElementException:
            drop_down = self.driver.find_element(By.CLASS_NAME, 'caret')
            drop_down.click()
            time.sleep(JS_WAIT)
            quick_buy = self.driver.find_element(By.PARTIAL_LINK_TEXT, plate)
        quick_buy.click()
        time.sleep(WEB_WAIT)
        self.log(f"buying daily: confirming quick-buy")
        self.get_entity(entity).set_state(state='confirming_purchase', attributes={'detail': 'Confirming purchase...'})
        quick_buy_confirm = self.driver.find_element(By.XPATH, "//span[@id='quickBuyConfirmPanel']//input[@Value='Yes']")
        quick_buy_confirm.click()
        print(f"bought the daily pass (confirm={quick_buy_confirm})")

        time.sleep(WEB_WAIT)
        try:
            xpath = f'//div[contains(@class, "panel") and .//h3[contains(text(), "Your RPPs")] and .//li[contains(@class, "active") and ./a[contains(text(), "Current RPPs")]] and .//td[./span[contains(text(), "Plate")] and ./span[contains(text(), "{plate}") and contains(@class, "text-success")]]]'
            self.log(f"Find element by xpath {xpath}")
            self.driver.find_element(By.XPATH, xpath)
            self.log(f"Found active parking pass on RPP portal for {plate}")
            self.get_entity(entity).set_state(state='successfully_purchased', attributes={'detail': 'Purchase successfully completed.'})
        except NoSuchElementException as nse:
          self.log(f"Unexpected state from RPP portal for {plate}: {nse}")
          self.get_entity(entity).set_state(state='error', attributes={'detail': 'No active parking passes, check the website.'})

