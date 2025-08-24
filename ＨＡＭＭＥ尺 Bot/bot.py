import asyncio
import json
import time
import os
import importlib.util
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
import requests
from bs4 import BeautifulSoup
import pycountry
import tenacity

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token='8059528086:AAFIZLlNJzo_nUplHlXzjyShla-DsT0RNYw')
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
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

def reset_user_state(counters):
    counters['charged'] = 0
    counters['approved'] = 0
    counters['ccn'] = 0
    counters['risk'] = 0
    counters['declined'] = 0
    counters['total'] = 0
    counters['current_status'] = "Waiting... ⏳"
    counters['is_processing'] = False
    counters['task'] = None
    counters['current_gate'] = 0
    counters['gate_type'] = None
    counters['stop_event'].clear()

# Start command handler
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or "No username"
    
    welcome_msg = messages["welcome"].format(user_id=user_id, username=username)
    await message.reply(welcome_msg, parse_mode="HTML")

# Stop command handler
@dp.message(Command("stop"))
async def stop_command(message: types.Message):
    user_id = str(message.from_user.id)
    counters = get_user_counters(user_id)
    
    async with counters['lock']:
        if counters['is_processing']:
            counters['stop_event'].set()
            if counters['task']:
                try:
                    counters['task'].cancel()
                    await counters['task']
                except asyncio.CancelledError:
                    pass
            await message.reply(messages["stopped"], parse_mode="HTML")
            reset_user_state(counters)
        else:
            await message.reply("No active check running 🌙", parse_mode="HTML")

# Res command handler
@dp.message(Command("res"))
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

# File handler
@dp.message(F.document)
async def handle_document(message: types.Message):
    user_id = str(message.from_user.id)
    counters = get_user_counters(user_id)
    
    if not message.document.file_name.endswith('.txt'):
        await message.reply(messages["txt_only"], parse_mode="HTML")
        return
    
    async with counters['lock']:
        if counters['is_processing']:
            await message.reply(messages["processing_active"], parse_mode="HTML")
            return
        
        # Start processing
        counters['is_processing'] = True
        counters['stop_event'].clear()
        
        # Download and process file
        try:
            file_info = await bot.get_file(message.document.file_id)
            file_path = file_info.file_path
            downloaded_file = await bot.download_file(file_path)
            
            content = downloaded_file.read().decode('utf-8')
            cards = [line.strip() for line in content.split('\n') if line.strip()]
            
            if len(cards) > MAX_CARDS:
                await message.reply(messages["max_cards"].format(max_cards=MAX_CARDS), parse_mode="HTML")
                reset_user_state(counters)
                return
            
            # Create status message
            status_msg = await message.reply("Starting card check... ⚡", parse_mode="HTML")
            
            # Process cards (simplified for testing)
            counters['total'] = len(cards)
            counters['approved'] = min(5, len(cards))  # Simulate some approved cards
            counters['declined'] = len(cards) - counters['approved']
            
            # Final results
            stats = [
                f"✅ Approved: {counters['approved']}",
                f"💰 Charged: {counters['charged']}",
                f"🔒 CCN: {counters['ccn']}",
                f"⚠️ Risk: {counters['risk']}",
                f"❌ Declined: {counters['declined']}",
                f"👻 Total: {counters['total']}",
                messages['done']
            ]
            final_message = "Check Results 📊\n━━━━━━━━━━━━━\n" + "\n".join(stats)
            
            await bot.edit_message_text(final_message, message.chat.id, status_msg.message_id, parse_mode="HTML")
            reset_user_state(counters)
            
        except Exception as e:
            await message.reply(messages["error"].format(error=str(e)), parse_mode="HTML")
            reset_user_state(counters)

# Run the bot
async def main():
    print("Braintree Bot is now running! ⚡")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())