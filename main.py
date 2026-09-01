import asyncio
import aiohttp
import os
from aiogram import Bot, Dispatcher, types

API_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

TOKENS = {}

DEX_PAIRS_URL = "https://api.dexscreener.com/latest/dex/pairs/robinhood/"
DEX_TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens/"
DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q="


# -----------------------------
#   FETCH METHODS (3 levels)
# -----------------------------

async def fetch_pairs_api(pair_address):
    async with aiohttp.ClientSession() as session:
        async with session.get(DEX_PAIRS_URL + pair_address) as resp:
            data = await resp.json()

    if "pairs" in data and data["pairs"]:
        return data["pairs"][0]
    return None


async def fetch_tokens_api(contract):
    async with aiohttp.ClientSession() as session:
        async with session.get(DEX_TOKENS_URL + contract) as resp:
            data = await resp.json()

    if "pairs" in data and data["pairs"]:
        return data["pairs"]
    return None


async def fetch_search_api(contract):
    async with aiohttp.ClientSession() as session:
        async with session.get(DEX_SEARCH_URL + contract) as resp:
            data = await resp.json()

    if "pairs" in data and data["pairs"]:
        return data["pairs"]
    return None


# -----------------------------
#   BEST PAIR SELECTOR
# -----------------------------

def select_best_pair(pairs):
    pairs.sort(
        key=lambda p: (
            p.get("liquidity", 0),
            p.get("volume", 0),
            p.get("holders", 0)
        ),
        reverse=True
    )
    return pairs[0]


# -----------------------------
#   COMBINED TOKEN RESOLVER
# -----------------------------

async def resolve_token(raw):
    # If link → extract pairAddress
    if "dexscreener.com" in raw:
        pair_address = raw.split("/")[-1]

        # Try PAIRS API
        pair = await fetch_pairs_api(pair_address)
        if pair:
            return pair

        # If PAIRS fails → try TOKENS API using baseToken.address
        # But we don't know contract yet → skip
        return None

    # If raw is contract → try TOKENS API
    contract = raw

    pairs = await fetch_tokens_api(contract)
    if pairs:
        return select_best_pair(pairs)

    # If TOKENS fails → try SEARCH API
    pairs = await fetch_search_api(contract)
    if pairs:
        return select_best_pair(pairs)

    return None


# -----------------------------
#   MONITORING LOOP
# -----------------------------

async def monitor(chat_id):
    while True:
        for key, token in TOKENS.items():
            pair = await resolve_token(key)
            if not pair:
                continue

            mc = pair.get("marketCap", 0)

            if token["ath_mc"] is None:
                token["ath_mc"] = mc
                continue

            if mc > token["ath_mc"]:
                token["ath_mc"] = mc
                token["triggered"] = set()

            drop = (token["ath_mc"] - mc) / token["ath_mc"]

            emoji_map = {
                0.60: "🟥",
                0.65: "🟧",
                0.70: "🟨",
                0.80: "🟪"
            }

            for threshold in token["alerts"]:

                if drop < threshold and threshold in token["triggered"]:
                    token["triggered"].remove(threshold)

                if drop >= threshold and threshold not in token["triggered"]:
                    token["triggered"].add(threshold)

                    emoji = emoji_map.get(threshold, "⚠️")

                    keyboard = types.InlineKeyboardMarkup()
                    keyboard.add(
                        types.InlineKeyboardButton("Re-arm", callback_data=f"rearm_{key}"),
                        types.InlineKeyboardButton("Delete", callback_data=f"delete_{key}")
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
#   CALLBACKS
# -----------------------------

@dp.callback_query_handler(lambda c: c.data.startswith("rearm_"))
async def rearm_alert(call: types.CallbackQuery):
    key = call.data.split("_")[1]
    if key in TOKENS:
        TOKENS[key]["triggered"] = set()
        await call.message.answer(f"Alerts re-armed for {TOKENS[key]['name']}.")
    await call.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("delete_"))
async def delete_token(call: types.CallbackQuery):
    key = call.data.split("_")[1]
    if key in TOKENS:
        name = TOKENS[key]["name"]
        del TOKENS[key]
        await call.message.answer(f"Token {name} deleted.")
    await call.answer()


# -----------------------------
#   /add COMMAND
# -----------------------------

@dp.message_handler(commands=["add"])
async def add_token(msg: types.Message):
    parts = msg.text.split()

    if len(parts) < 2:
        await msg.answer("Usage:\n/add <dexscreener_link_or_contract>")
        return

    raw = parts[1]

    pair = await resolve_token(raw)
    if not pair:
        await msg.answer("Token not found via PAIRS, TOKENS, or SEARCH API.")
        return

    base = pair.get("baseToken", {})
    name = base.get("name", "Unknown")
    contract = base.get("address", raw)

    TOKENS[raw] = {
        "name": name,
        "contract": contract,
        "ath_mc": None,
        "alerts": [0.60, 0.65, 0.70, 0.80],
        "triggered": set()
    }

    await msg.answer(
        f"Added token:\n"
        f"Name: {name}\n"
        f"Contract: {contract}\n"
        f"Tracking MarketCap drops."
    )


# -----------------------------
#   OTHER COMMANDS
# -----------------------------

@dp.message_handler(commands=["remove"])
async def remove_token(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage:\n/remove <contract_or_link>")
        return

    key = parts[1]

    if key in TOKENS:
        name = TOKENS[key]["name"]
        del TOKENS[key]
        await msg.answer(f"Token {name} removed.")
    else:
        await msg.answer("Token not found.")


@dp.message_handler(commands=["reset"])
async def reset_alerts(msg: types.Message):
    for token in TOKENS.values():
        token["triggered"] = set()
    await msg.answer("All alerts reset.")


@dp.message_handler(commands=["list"])
async def list_tokens(msg: types.Message):
    if not TOKENS:
        await msg.answer("No tokens being tracked.")
        return

    text = "Tracked tokens:\n\n"
    for key, token in TOKENS.items():
        text += f"• {token['name']} (ATH MC: {token['ath_mc']})\n"

    await msg.answer(text)


@dp.message_handler(commands=["commands"])
async def commands(msg: types.Message):
    await msg.answer(
        "/start – start monitoring\n"
        "/add <link_or_contract> – add token\n"
        "/remove <key> – remove token\n"
        "/list – list tracked tokens\n"
        "/reset – reset alerts\n"
        "/commands – show all commands\n"
    )


@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.answer("Bot started. Add tokens using /add <link_or_contract>.")
    asyncio.create_task(monitor(msg.chat.id))


async def main():
    await dp.start_polling()


if __name__ == "__main__":
    asyncio.run(main())








