import asyncio
import aiohttp
import os
from aiogram import Bot, Dispatcher, types

API_TOKEN = os.getenv("BOT_TOKEN")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Token storage
TOKENS = {}

# Fetch marketcap
async def get_marketcap(address):
    url = f"https://public-api.birdeye.so/public/token?address={address}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return data["data"]["marketCap"]

# Fetch token name
async def get_token_name(address):
    url = f"https://public-api.birdeye.so/public/token?address={address}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return data["data"]["name"]

# Monitoring loop
async def monitor(chat_id):
    while True:
        for address, token in TOKENS.items():
            mc = await get_marketcap(address)

            # Set initial ATH
            if token["ath_mc"] is None:
                token["ath_mc"] = mc
                continue

            # Update ATH if marketcap increases
            if mc > token["ath_mc"]:
                token["ath_mc"] = mc
                token["triggered"] = set()

            # Drop from ATH
            drop = (token["ath_mc"] - mc) / token["ath_mc"]

            # Emoji indicators
            emoji_map = {
                0.60: "🟥",
                0.65: "🟧",
                0.70: "🟨",
                0.80: "🟪"
            }

            for threshold in token["alerts"]:

                # AUTO-REARM: if marketcap recovered above threshold, rearm alert
                if drop < threshold and threshold in token["triggered"]:
                    token["triggered"].remove(threshold)

                # Trigger alert when drop crosses threshold
                if drop >= threshold and threshold not in token["triggered"]:
                    token["triggered"].add(threshold)

                    emoji = emoji_map.get(threshold, "⚠️")

                    keyboard = types.InlineKeyboardMarkup()
                    keyboard.add(
                        types.InlineKeyboardButton("Re-arm", callback_data=f"rearm_{address}"),
                        types.InlineKeyboardButton("Delete", callback_data=f"delete_{address}")
                    )

                    await bot.send_message(
                        chat_id,
                        f"{emoji} {token['name']} dropped {int(threshold*100)}% from ATH MC\n"
                        f"ATH: ${token['ath_mc']:,}\n"
                        f"Now: ${mc:,}",
                        reply_markup=keyboard
                    )

        await asyncio.sleep(10)

# -----------------------------
#        CALLBACKS
# -----------------------------
@dp.callback_query_handler(lambda c: c.data.startswith("rearm_"))
async def rearm_alert(call: types.CallbackQuery):
    address = call.data.split("_")[1]
    if address in TOKENS:
        TOKENS[address]["triggered"] = set()
        await call.message.answer(f"Alerts re-armed for {TOKENS[address]['name']}.")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("delete_"))
async def delete_token(call: types.CallbackQuery):
    address = call.data.split("_")[1]
    if address in TOKENS:
        name = TOKENS[address]["name"]
        del TOKENS[address]
        await call.message.answer(f"Token {name} deleted.")
    await call.answer()

# -----------------------------
#        /add
# -----------------------------
@dp.message_handler(commands=["add"])
async def add_token(msg: types.Message):
    try:
        address = msg.text.split()[1]
        name = await get_token_name(address)

        TOKENS[address] = {
            "name": name,
            "ath_mc": None,
            "alerts": [0.60, 0.65, 0.70, 0.80],
            "triggered": set()
        }

        await msg.answer(f"Token {name} added.\nTracking MarketCap drops.")
    except:
        await msg.answer("Usage:\n/add <contract_address>")

# -----------------------------
#        /remove
# -----------------------------
@dp.message_handler(commands=["remove"])
async def remove_token(msg: types.Message):
    try:
        address = msg.text.split()[1]

        if address in TOKENS:
            name = TOKENS[address]["name"]
            del TOKENS[address]
            await msg.answer(f"Token {name} removed.")
        else:
            await msg.answer("Token not found.")
    except:
        await msg.answer("Usage:\n/remove <contract_address>")

# -----------------------------
#        /reset
# -----------------------------
@dp.message_handler(commands=["reset"])
async def reset_alerts(msg: types.Message):
    for token in TOKENS.values():
        token["triggered"] = set()
    await msg.answer("All alerts reset.")

# -----------------------------
#        /list
# -----------------------------
@dp.message_handler(commands=["list"])
async def list_tokens(msg: types.Message):
    if not TOKENS:
        await msg.answer("No tokens being tracked.")
        return

    text = "Tracked tokens:\n\n"
    for address, token in TOKENS.items():
        text += f"• {token['name']} (ATH MC: {token['ath_mc']})\n"

    await msg.answer(text)

# -----------------------------
#        /commands
# -----------------------------
@dp.message_handler(commands=["commands"])
async def commands(msg: types.Message):
    text = (
        "/start – start monitoring\n"
        "/add <address> – add token\n"
        "/remove <address> – remove token\n"
        "/list – list tracked tokens\n"
        "/reset – reset alerts\n"
        "/commands – show all commands\n"
    )
    await msg.answer(text)

# -----------------------------
#        /start
# -----------------------------
@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.answer("Bot started. Add tokens using /add <address>.")
    asyncio.create_task(monitor(msg.chat.id))

async def main():
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())




