import asyncio
import aiohttp
import os
from aiogram import Bot, Dispatcher, types

API_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

TOKENS = {}  # key: (chainId, pairAddress)


DEX_BASE = "https://api.dexscreener.com/latest/dex/pairs/"


async def fetch_pair(chain_id: str, pair_address: str):
    url = f"{DEX_BASE}{chain_id}/{pair_address}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()

    if "pairs" in data and data["pairs"]:
        return data["pairs"][0]
    return None


async def monitor(chat_id: int):
    while True:
        for key, token in TOKENS.items():
            chain_id, pair_address = key

            pair = await fetch_pair(chain_id, pair_address)
            if not pair:
                continue

            mc = pair.get("marketCap", 0) or 0
            if mc <= 0:
                continue

            # init ATH
            if token["ath_mc"] is None:
                token["ath_mc"] = mc
                continue

            # update ATH
            if mc > token["ath_mc"]:
                token["ath_mc"] = mc
                token["triggered"].clear()

            drop = (token["ath_mc"] - mc) / token["ath_mc"]

            emoji_map = {
                0.60: "🟥",
                0.65: "🟧",
                0.70: "🟨",
                0.80: "🟪",
            }

            for threshold in token["alerts"]:
                # auto‑rearm
                if drop < threshold and threshold in token["triggered"]:
                    token["triggered"].remove(threshold)

                # trigger
                if drop >= threshold and threshold not in token["triggered"]:
                    token["triggered"].add(threshold)
                    emoji = emoji_map.get(threshold, "⚠️")

                    kb = types.InlineKeyboardMarkup()
                    kb.add(
                        types.InlineKeyboardButton(
                            "Re-arm", callback_data=f"rearm_{chain_id}_{pair_address}"
                        ),
                        types.InlineKeyboardButton(
                            "Delete", callback_data=f"delete_{chain_id}_{pair_address}"
                        ),
                    )

                    await bot.send_message(
                        chat_id,
                        f"{emoji} {token['name']} dropped {int(threshold*100)}% from ATH MC\n"
                        f"ATH: ${token['ath_mc']:,}\n"
                        f"Now: ${mc:,}\n"
                        f"Link: {pair.get('url', '')}",
                        reply_markup=kb,
                    )

        await asyncio.sleep(10)


@dp.callback_query_handler(lambda c: c.data.startswith("rearm_"))
async def rearm_alert(call: types.CallbackQuery):
    _, chain_id, pair_address = call.data.split("_", 2)
    key = (chain_id, pair_address)
    if key in TOKENS:
        TOKENS[key]["triggered"].clear()
        await call.message.answer(f"Alerts re-armed for {TOKENS[key]['name']}.")
    await call.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("delete_"))
async def delete_token(call: types.CallbackQuery):
    _, chain_id, pair_address = call.data.split("_", 2)
    key = (chain_id, pair_address)
    if key in TOKENS:
        name = TOKENS[key]["name"]
        del TOKENS[key]
        await call.message.answer(f"Token {name} deleted.")
    await call.answer()


@dp.message_handler(commands=["add"])
async def add_token(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage:\n/add <dexscreener_link>")
        return

    raw = parts[1]

    if "dexscreener.com" not in raw:
        await msg.answer("Send a DexScreener link, e.g.:\n/add https://dexscreener.com/robinhood/<pairAddress>")
        return

    try:
        # example: https://dexscreener.com/robinhood/0x...
        segments = raw.split("/")
        chain_id = segments[-2]
        pair_address = segments[-1]
    except Exception:
        await msg.answer("Invalid DexScreener link format.")
        return

    pair = await fetch_pair(chain_id, pair_address)
    if not pair:
        await msg.answer("Pair not found via DexScreener API.")
        return

    base = pair.get("baseToken", {})
    name = base.get("name", "Unknown")
    contract = base.get("address", "")

    key = (chain_id, pair_address)
    TOKENS[key] = {
        "name": name,
        "contract": contract,
        "ath_mc": None,
        "alerts": [0.60, 0.65, 0.70, 0.80],
        "triggered": set(),
    }

    await msg.answer(
        f"Added token:\n"
        f"Name: {name}\n"
        f"Contract: {contract}\n"
        f"Chain: {chain_id}\n"
        f"Pair: {pair_address}\n"
        f"Tracking MarketCap drops."
    )


@dp.message_handler(commands=["list"])
async def list_tokens(msg: types.Message):
    if not TOKENS:
        await msg.answer("No tokens being tracked.")
        return

    text = "Tracked tokens:\n\n"
    for (chain_id, pair_address), token in TOKENS.items():
        text += (
            f"• {token['name']} "
            f"(Chain: {chain_id}, Pair: {pair_address}, ATH MC: {token['ath_mc']})\n"
        )

    await msg.answer(text)


@dp.message_handler(commands=["remove"])
async def remove_token(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage:\n/remove <chainId> <pairAddress>")
        return

    if len(parts) < 3:
        await msg.answer("Usage:\n/remove <chainId> <pairAddress>")
        return

    chain_id = parts[1]
    pair_address = parts[2]
    key = (chain_id, pair_address)

    if key in TOKENS:
        name = TOKENS[key]["name"]
        del TOKENS[key]
        await msg.answer(f"Token {name} removed.")
    else:
        await msg.answer("Token not found.")


@dp.message_handler(commands=["reset"])
async def reset_alerts(msg: types.Message):
    for token in TOKENS.values():
        token["triggered"].clear()
    await msg.answer("All alerts reset.")


@dp.message_handler(commands=["commands"])
async def commands(msg: types.Message):
    await msg.answer(
        "/start – start monitoring\n"
        "/add <dexscreener_link> – add token\n"
        "/remove <chainId> <pairAddress> – remove token\n"
        "/list – list tracked tokens\n"
        "/reset – reset alerts\n"
        "/commands – show all commands\n"
    )


@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.answer("Bot started. Add tokens using /add <dexscreener_link>.")
    asyncio.create_task(monitor(msg.chat.id))


async def main():
    await dp.start_polling()


if __name__ == "__main__":
    asyncio.run(main())









