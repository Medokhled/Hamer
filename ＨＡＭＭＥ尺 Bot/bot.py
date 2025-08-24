import asyncio
import json
import time
import os
import importlib.util
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import MessageNotModified, MessageToDeleteNotFound, InvalidQueryID
import requests
from bs4 import BeautifulSoup
import pycountry
import tenacity

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token='8059528086:AAFIZLlNJzo_nUplHlXzjyShla-DsT0RNYw')
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
admins = ['5328767896']

# Load Braintree gateway files
gateway_files = [f for f in os.listdir() if f.startswith(('braintree_charge', 'braintree_auth')) and f.endswith('.py')]
gateways = {'braintree_charge': [], 'braintree_auth': []}

for gate_file in gateway_files:
    try:
        spec = importlib.util.spec_from_file_location("PaymentGateway", gate_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        gateway_type = 'braintree_charge' if gate_file.startswith('braintree_charge') else 'braintree_auth'
        if module.gateway.fetch_braintree_token():
            gateways[gateway_type].append({'gateway': module.gateway, 'file': gate_file, 'status': '✅'})
        else:
            gateways[gateway_type].append({'gateway': module.gateway, 'file': gate_file, 'status': '❌'})
    except Exception:
        gateways[gateway_type].append({'gateway': None, 'file': gate_file, 'status': '⚠️'})
        logger.error(f"Skipping {gate_file} due to error")

# Data files
response_rules_file = "response_rules.json"
settings_file = "settings.json"

# Load JSON data
def load_json(file_path, default):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

# Load settings
def load_settings():
    settings = load_json(settings_file, {"max_cards": 1000, "combo_time": 20})
    global MAX_CARDS, COMBO_TIME
    MAX_CARDS = settings["max_cards"]
    COMBO_TIME = settings["combo_time"]

load_settings()

response_rules = load_json(response_rules_file, {'braintree_charge': {}, 'braintree_auth': {}})
processing_users = {}

# Messages
messages = {
    "welcome": """<b>Welcome to Braintree Bot ⚡</b>
🆔 Your ID: <code>{user_id}</code> | @{username}
👤 Developer: <a href="https://t.me/developer_hammer">ＨＡＭＭＥ尺</a>
📤 Upload a .txt file to check cards!""",
    "processing": "Checking cards... ⚙️ | Gateway: {gate_type}",
    "stopped": "Check stopped 🛑",
    "done": "Check completed ✅",
    "processing_active": "A check is already active, wait or use stop! ⚙️",
    "max_cards": "Maximum {max_cards} cards per file 🚫",
    "txt_only": "Please upload a .txt file only 📋",
    "not_authorized": "This command is for developers only! ⚠️ | Contact [<a href=\"https://t.me/developer_hammer\">ＨＡＭＭＥ尺</a>]",
    "res_success": "Response '{response}' for {gate_type} set as {status} ✅",
    "res_error": "Use format: /res gate_type Response Status (e.g., /res braintree_charge Invalid_Transaction Declined) 🚫",
    "error": "Error occurred: {error} 🚫",
    "gate_not_found": "Gateway {gate_type} not available or under development... 🚫",
    "gate_current": "{gate_type}➜{gate_index}",
    "no_gateways": "No gateways available to check the card 🚫",
}

# User counters
def get_user_counters(user_id):
    if user_id not in processing_users:
        processing_users[user_id] = {
            'charged': 0, 'approved': 0, 'ccn': 0, 'risk': 0, 'declined': 0, 'total': 0,
            'current_status': "Waiting... ⏳", 'stop_event': asyncio.Event(), 'is_processing': False,
            'task': None, 'current_gate': 0, 'gate_type': None,
            'lock': asyncio.Lock(), 'update_task': None, 'last_message_content': None
        }
    return processing_users[user_id]

# Reset user state
def reset_user_state(counters):
    counters['is_processing'] = False
    counters['task'] = None
    counters['stop_event'].clear()
    counters['last_message_content'] = None
    if counters['update_task']:
        counters['update_task'].cancel()
        counters['update_task'] = None

# Get BIN info
def get_bin_info(bin_number):
    headers = {
        'Referer': 'https://bincheck.io',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36',
    }
    try:
        response = requests.get(f'https://bincheck.io/details/{bin_number}', headers=headers)
        if response.status_code != 200:
            return {'scheme': 'Unknown ⚠️', 'type': 'Unknown ⚠️', 'bank': 'Unknown ⚠️', 'country': 'Unknown ⚠️', 'level': 'Unknown ⚠️', 'iso_a2': ''}
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = soup.find_all('table', class_='w-full table-auto')
        if len(tables) < 2:
            return {'scheme': 'Unknown ⚠️', 'type': 'Unknown ⚠️', 'bank': 'Unknown ⚠️', 'country': 'Unknown ⚠️', 'level': 'Unknown ⚠️', 'iso_a2': ''}
        bin_info = {}
        for row in tables[0].find_all('tr'):
            cells = row.find_all('td')
            if len(cells) == 2:
                key, value = cells[0].text.strip(), cells[1].text.strip()
                if key == 'Card Brand': bin_info['scheme'] = value.upper()
                elif key == 'Card Type': bin_info['type'] = value.upper()
                elif key == 'Card Level': bin_info['level'] = value.upper() if value else 'Unknown ⚠️'
                elif key == 'Issuer Name / Bank': bin_info['bank'] = value.upper()
        for row in tables[1].find_all('tr'):
            cells = row.find_all('td')
            if len(cells) == 2:
                key, value = cells[0].text.strip(), cells[1].text.strip()
                if key == 'ISO Country Name': bin_info['country'] = value.upper()
                elif key == 'ISO Country Code A2': bin_info['iso_a2'] = value.upper()
        country = pycountry.countries.get(alpha_2=bin_info.get('iso_a2', ''))
        bin_info['flag'] = getattr(country, 'flag', '❓') if country else '❓'
        return bin_info
    except Exception:
        return {'scheme': 'Unknown ⚠️', 'type': 'Unknown ⚠️', 'bank': 'Unknown ⚠️', 'country': 'Unknown ⚠️', 'level': 'Unknown ⚠️', 'iso_a2': ''}

# Validate card format
def validate_card_format(card):
    try:
        parts = card.split('|')
        if len(parts) != 4:
            return False
        number, month, year, cvv = parts
        if not (number.isdigit() and 13 <= len(number) <= 19):
            return False
        if not (month.isdigit() and 1 <= int(month) <= 12 and 1 <= len(month) <= 2):
            return False
        if not (year.isdigit() and len(year) in [2, 4]):
            return False
        if not (cvv.isdigit() and 3 <= len(cvv) <= 4):
            return False
        return True
    except Exception:
        return False

# Update status message
async def update_status_message(chat_id, message_id, counters, user_id, gate_type):
    while not counters['stop_event'].is_set() and counters['is_processing']:
        try:
            markup = InlineKeyboardMarkup(row_width=1)
            gate_index = (counters['current_gate'] % len(gateways[gate_type])) + 1
            buttons = [
                InlineKeyboardButton(f"Status: {counters['current_status']} 📡", callback_data='x'),
                InlineKeyboardButton(f"{counters.get('current_cc', 'No card yet')} 💳", callback_data='x'),
                InlineKeyboardButton(f"Approved: {counters['approved']} ✅", callback_data='x'),
                InlineKeyboardButton(f"Charged: {counters['charged']} 💰", callback_data='x'),
                InlineKeyboardButton(f"CCN: {counters['ccn']} 🔒", callback_data='x'),
                InlineKeyboardButton(f"Risk: {counters['risk']} ⚠️", callback_data='x'),
                InlineKeyboardButton(f"Declined: {counters['declined']} ❌", callback_data='x'),
                InlineKeyboardButton(f"Total: {counters['total']} 👻", callback_data='x'),
                InlineKeyboardButton(f"{gate_type.replace('_', ' ').title()}➜{gate_index}", callback_data='x'),
                InlineKeyboardButton("STOP 🛑", callback_data=f'stop_{user_id}')
            ]
            markup.add(*buttons)
            formatted_msg = messages["processing"].format(gate_type=gate_type.replace('_', ' ').title())
            current_content = (formatted_msg, json.dumps([[b.text, b.callback_data] for row in markup.inline_keyboard for b in row]))
            if counters['last_message_content'] != current_content:
                await bot.edit_message_text(formatted_msg, chat_id, message_id, reply_markup=markup, parse_mode="HTML")
                counters['last_message_content'] = current_content
        except MessageNotModified:
            pass
        except Exception as e:
            logger.error(f"Error updating status message: {e}")
        await asyncio.sleep(1)

# Process cards
async def process_cards(message: types.Message, lines, user_id, counters, message_id, gate_type):
    stop_event = counters['stop_event']
    update_task = asyncio.create_task(update_status_message(message.chat.id, message_id, counters, user_id, gate_type))
    counters['update_task'] = update_task

    for i, line in enumerate(lines):
        if stop_event.is_set():
            break
        cc = line.strip()
        async with counters['lock']:
            counters['current_cc'] = cc
        try:
            if not validate_card_format(cc):
                async with counters['lock']:
                    counters['declined'] += 1
                    counters['current_status'] = "Invalid card format 🚫"
                continue
            if stop_event.is_set():
                break
            parts = cc.split('|')
            bin_number = parts[0][:6]
            bin_info = get_bin_info(bin_number)
            info_text = f"{bin_info.get('scheme', 'Unknown')} - {bin_info.get('type', 'Unknown')} - {bin_info.get('level', 'Unknown')}".strip()
            start_time = time.time()
            gate_index = counters['current_gate'] % len(gateways[gate_type])
            max_attempts = len(gateways[gate_type])
            attempts = 0
            response_text = None
            token = None
            while attempts < max_attempts:
                if stop_event.is_set():
                    break
                working_gateway = gateways[gate_type][gate_index]
                if working_gateway['status'] != '✅':
                    async with counters['lock']:
                        counters['current_gate'] += 1
                    gate_index = counters['current_gate'] % len(gateways[gate_type])
                    attempts += 1
                    continue
                try:
                    @tenacity.retry(wait=tenacity.wait_exponential(multiplier=1, min=4, max=10), stop=tenacity.stop_after_attempt(3))
                    def check_card_with_retry():
                        if stop_event.is_set():
                            raise asyncio.CancelledError("Stopped by user")
                        return working_gateway['gateway'].check_card(cc, stop_event)

                    token = check_card_with_retry()
                    if not token:
                        async with counters['lock']:
                            counters['current_gate'] += 1
                        gate_index = counters['current_gate'] % len(gateways[gate_type])
                        attempts += 1
                        continue
                    if stop_event.is_set():
                        break
                    response_text = working_gateway['gateway'].submit_payment(token, stop_event)
                    break
                except Exception as e:
                    async with counters['lock']:
                        counters['current_gate'] += 1
                    gate_index = counters['current_gate'] % len(gateways[gate_type])
                    attempts += 1
                    continue
            if stop_event.is_set():
                break
            if attempts >= max_attempts or not response_text:
                async with counters['lock']:
                    counters['current_status'] = messages["no_gateways"]
                    counters['declined'] += 1
                continue
            end_time = time.time()
            check_time = round(end_time - start_time, 2)
            send_message = False
            status_text = None
            gate_rules = response_rules.get(gate_type, {})
            for resp, status in gate_rules.items():
                if resp == response_text:
                    status_text = status
                    if status == "Charged":
                        async with counters['lock']:
                            counters['charged'] += 1
                        send_message = True
                    elif status == "Approved":
                        async with counters['lock']:
                            counters['approved'] += 1
                        send_message = True
                    elif status == "CCN":
                        async with counters['lock']:
                            counters['ccn'] += 1
                        send_message = True
                    elif status == "Risk":
                        async with counters['lock']:
                            counters['risk'] += 1
                    elif status == "Declined":
                        async with counters['lock']:
                            counters['declined'] += 1
                    break
            if status_text is None:
                if any(x in response_text for x in ['Payment method successfully added.', 'street address.', 'Gateway Rejected: avs', 'payment method added:', 'Thank you for your purchase!', 'added']):
                    async with counters['lock']:
                        counters['charged'] += 1
                    status_text = "Charged"
                    send_message = True
                elif 'Insufficient Funds' in response_text or 'Invalid Amount' in response_text:
                    async with counters['lock']:
                        counters['approved'] += 1
                    status_text = "Approved"
                    send_message = True
                elif 'Card Issuer Declined CVV' in response_text:
                    async with counters['lock']:
                        counters['ccn'] += 1
                    status_text = "CCN"
                    send_message = True
                elif 'risk_threshold' in response_text or 'risk' in response_text.lower():
                    async with counters['lock']:
                        counters['risk'] += 1
                    status_text = "Risk"
                else:
                    async with counters['lock']:
                        counters['declined'] += 1
                    status_text = "Declined"
            display_status = "Charged ✅" if status_text == "Charged" else "Approved ✅" if status_text == "Approved" else "CCN 🔒" if status_text == "CCN" else "Risk ⚠️" if status_text == "Risk" else "Declined ❌"
            async with counters['lock']:
                counters['current_status'] = response_text[:100]
            if send_message:
                msg = f"""<b>ＨＡＭＭＥ尺 {gate_type.replace('_', ' ').title()} 🌩️</b>
━━━━━━━━━━━━━
💳 Card: <code>{cc}</code>
⚡ Status: {display_status}
📩 Response: {response_text}
ℹ️ Info: {info_text}
🏦 Bank: {bin_info.get('bank', 'Unknown')} - {bin_info.get('flag', '❓')}
🌍 Country: {bin_info.get('country', 'Unknown')} [ {bin_info.get('flag', '❓')} ]
⏱️ Time: {check_time}s
👤 By: <a href="https://t.me/developer_hammer">ＨＡＭＭＥ尺</a>
━━━━━━━━━━━━━"""
                await bot.send_message(message.chat.id, msg, parse_mode="HTML")
            async with counters['lock']:
                counters['current_gate'] += 1
            sleep_duration = COMBO_TIME
            sleep_interval = 0.1
            elapsed = 0
            while elapsed < sleep_duration:
                if stop_event.is_set():
                    break
                await asyncio.sleep(sleep_interval)
                elapsed += sleep_interval
            if stop_event.is_set():
                break
        except Exception as e:
            async with counters['lock']:
                counters['current_status'] = f"Error: {str(e)[:50]} 🚫"
            async with counters['lock']:
                counters['current_gate'] += 1
            continue
    async with counters['lock']:
        try:
            gate_display = gate_type.replace('_', ' ').title()
            stats = [
                f"✅ Approved: {counters['approved']}",
                f"💰 Charged: {counters['charged']}",
                f"🔒 CCN: {counters['ccn']}",
                f"⚠️ Risk: {counters['risk']}",
                f"❌ Declined: {counters['declined']}",
                f"👻 Total: {counters['total']}",
                f"📶 {gate_display}",
                messages['done'] if not stop_event.is_set() else messages['stopped']
            ]
            final_message = "Check Results 📊\n━━━━━━━━━━━━━\n" + "\n".join(stats)
            await bot.edit_message_text(final_message, message.chat.id, message_id, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error finalizing check: {e}")
        finally:
            reset_user_state(counters)

# Welcome message handler
@dp.message_handler(content_types=['text'])
async def welcome_message(message: types.Message):
    user_id = str(message.from_user.id)
    try:
        username = message.from_user.username or "Not available"
        formatted_msg = messages["welcome"].format(user_id=user_id, username=username)
        await message.reply(formatted_msg, parse_mode="HTML")
    except Exception as e:
        await message.reply(messages["error"].format(error=str(e)), parse_mode="HTML")

# File upload handler
@dp.message_handler(content_types=["document"])
async def handle_file(message: types.Message):
    user_id = str(message.from_user.id)
    counters = get_user_counters(user_id)
    try:
        async with counters['lock']:
            if counters['is_processing']:
                await message.reply(messages["processing_active"], parse_mode="HTML")
                return
        if not message.document.file_name.endswith('.txt'):
            await message.reply(messages["txt_only"], parse_mode="HTML")
            return
        file_info = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        with open(f"combo_{user_id}.txt", "wb") as f:
            f.write(downloaded_file.read())
        with open(f"combo_{user_id}.txt", "r") as file:
            lines = file.readlines()
            if len(lines) > MAX_CARDS:
                await message.reply(messages["max_cards"].format(max_cards=MAX_CARDS), parse_mode="HTML")
                return
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton(f"Braintree Charge {'✅' if gateways['braintree_charge'] else '🚫'}", callback_data="file_braintree_charge"),
            InlineKeyboardButton(f"Braintree Auth {'✅' if gateways['braintree_auth'] else '🚫'}", callback_data="file_braintree_auth"),
        )
        await message.reply("Select gateway to check file:", reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        await message.reply(messages["error"].format(error=str(e)), parse_mode="HTML")

# File gateway selection handler
@dp.callback_query_handler(lambda c: c.data.startswith('file_'))
async def handle_file_gateway(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    counters = get_user_counters(user_id)
    gate_type = callback.data.replace('file_', '')
    try:
        if not gateways[gate_type]:
            await callback.message.reply(messages["gate_not_found"].format(gate_type=gate_type.replace('_', ' ').title()), parse_mode="HTML")
            await callback.answer()
            return
        async with counters['lock']:
            if counters['is_processing']:
                await callback.message.reply(messages["processing_active"], parse_mode="HTML")
                await callback.answer()
                return
            counters['is_processing'] = True
            counters['total'] = 0
            counters['charged'] = counters['approved'] = counters['ccn'] = counters['risk'] = counters['declined'] = 0
            counters['stop_event'].clear()
            counters['current_gate'] = 0
            counters['gate_type'] = gate_type
            counters['last_message_content'] = None
        with open(f"combo_{user_id}.txt", "r") as file:
            lines = file.readlines()
            counters['total'] = len(lines)
        formatted_msg = messages["processing"].format(gate_type=gate_type.replace('_', ' ').title())
        message_id = (await callback.message.reply(formatted_msg, parse_mode="HTML")).message_id
        initial_markup = InlineKeyboardMarkup(row_width=1)
        buttons = [
            InlineKeyboardButton(f"Status: Waiting... ⏳ 📡", callback_data='x'),
            InlineKeyboardButton(f"No card yet 💳", callback_data='x'),
            InlineKeyboardButton(f"Approved: 0 ✅", callback_data='x'),
            InlineKeyboardButton(f"Charged: 0 💰", callback_data='x'),
            InlineKeyboardButton(f"CCN: 0 🔒", callback_data='x'),
            InlineKeyboardButton(f"Risk: 0 ⚠️", callback_data='x'),
            InlineKeyboardButton(f"Declined: 0 ❌", callback_data='x'),
            InlineKeyboardButton(f"Total: {counters['total']} 👻", callback_data='x'),
            InlineKeyboardButton(f"{gate_type.replace('_', ' ').title()}➜1", callback_data='x'),
            InlineKeyboardButton(f"STOP 🛑", callback_data=f'stop_{user_id}')
        ]
        initial_markup.add(*buttons)
        await bot.edit_message_text(formatted_msg, callback.message.chat.id, message_id, reply_markup=initial_markup, parse_mode="HTML")
        task = asyncio.create_task(process_cards(callback.message, lines, user_id, counters, message_id, gate_type))
        counters['task'] = task
        try:
            await callback.message.delete()
        except (MessageToDeleteNotFound, MessageNotModified):
            logger.debug("Message already deleted or not modifiable")
        try:
            await callback.answer()
        except InvalidQueryID:
            logger.debug("Callback query too old, ignoring.")
    except Exception as e:
        try:
            await callback.message.reply(messages["error"].format(error=str(e)), parse_mode="HTML")
        except:
            logger.error(f"Failed to send error message: {e}")
        async with counters['lock']:
            reset_user_state(counters)
        try:
            await callback.answer("An error occurred, try again.", show_alert=True)
        except InvalidQueryID:
            logger.debug("Callback query too old, ignoring.")
        except:
            logger.error(f"Failed to answer callback: {e}")

# Stop button handler
@dp.callback_query_handler(lambda c: c.data.startswith('stop_'))
async def callback_handler(callback: types.CallbackQuery):
    try:
        parts = callback.data.split('_')
        if len(parts) != 2:
            await callback.answer("Invalid stop command.", show_alert=True)
            return
        _, target_user_id = parts
        target_user_id = str(target_user_id)
        counters = get_user_counters(target_user_id)
        async with counters['lock']:
            if counters['is_processing']:
                counters['stop_event'].set()
                if counters['task']:
                    try:
                        counters['task'].cancel()
                        await counters['task']
                    except asyncio.CancelledError:
                        pass
                gate_type = counters['gate_type']
                gate_display = gate_type.replace('_', ' ').title()
                stats = [
                    f"✅ Approved: {counters['approved']}",
                    f"💰 Charged: {counters['charged']}",
                    f"🔒 CCN: {counters['ccn']}",
                    f"⚠️ Risk: {counters['risk']}",
                    f"❌ Declined: {counters['declined']}",
                    f"👻 Total: {counters['total']}",
                    f"📶 {gate_display}",
                    messages['stopped']
                ]
                final_message = "Check Results 📊\n━━━━━━━━━━━━━\n" + "\n".join(stats)
                try:
                    await bot.edit_message_text(final_message, callback.message.chat.id, callback.message.message_id, parse_mode="HTML")
                except (MessageNotModified, MessageToEditNotFound):
                    await bot.send_message(callback.message.chat.id, final_message, parse_mode="HTML")
                reset_user_state(counters)
                try:
                    await callback.answer("Stopped 🛑")
                except InvalidQueryID:
                    logger.debug("Callback query too old, ignoring.")
            else:
                try:
                    await callback.answer("No active check running 🌙", show_alert=True)
                except InvalidQueryID:
                    logger.debug("Callback query too old, ignoring.")
    except Exception as e:
        try:
            await callback.answer(messages["error"].format(error=str(e)), show_alert=True)
        except InvalidQueryID:
            logger.debug("Callback query too old, ignoring.")
        except:
            logger.error(f"Failed to answer callback: {e}")

# /res command handler
@dp.message_handler(commands=["res"])
async def set_response_rule(message: types.Message):
    user_id = str(message.from_user.id)
    try:
        if user_id not in admins:
            await message.reply(messages["not_authorized"], parse_mode="HTML")
            return
        command_text = message.text[len("/res "):].strip()
        if not command_text:
            await message.reply(messages["res_error"], parse_mode="HTML")
            return
        parts = command_text.split()
        if len(parts) < 3:
            await message.reply(messages["res_error"], parse_mode="HTML")
            return
        gate_type = parts[0]
        status = parts[-1]
        response = " ".join(parts[1:-1]).strip('"')
        if gate_type not in gateways:
            await message.reply(messages["res_error"], parse_mode="HTML")
            return
        valid_statuses = ["Charged", "Approved", "CCN", "Risk", "Declined"]
        if status not in valid_statuses:
            status = valid_statuses[0]
        response_rules[gate_type][response] = status
        save_json(response_rules_file, response_rules)
        display_status = "Charged ✅" if status == "Charged" else "Approved ✅" if status == "Approved" else "CCN 🔒" if status == "CCN" else "Risk ⚠️" if status == "Risk" else "Declined ❌"
        formatted_msg = messages["res_success"].format(response=response, gate_type=gate_type, status=display_status)
        await message.reply(formatted_msg, parse_mode="HTML")
    except Exception as e:
        await message.reply(messages["error"].format(error=str(e)), parse_mode="HTML")

# Run the bot
async def main():
    print("Braintree Bot is now running! ⚡")
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())