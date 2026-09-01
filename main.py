import asyncio
import aiohttp
import os
from aiogram import Bot, Dispatcher, types

API_TOKEN = os.getenv("BOT_TOKEN")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Хранилище токенов
TOKENS = {}

# Получение marketcap токена
async def get_marketcap(address):
    url = f"https://public-api.birdeye.so/public/token?address={address}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return data["data"]["marketCap"]

# Получение имени токена
async def get_token_name(address):
    url = f"https://public-api.birdeye.so/public/token?address={address}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return data["data"]["name"]

# Основной мониторинг
async def monitor(chat_id):
    while True:
        for address, token in TOKENS.items():
            mc = await get_marketcap(address)

            # Если ATH ещё не установлен — ставим текущий marketcap как ATH
            if token["ath_mc"] is None:
                token["ath_mc"] = mc
                continue

            # Если marketcap вырос — обновляем ATH
            if mc > token["ath_mc"]:
                token["ath_mc"] = mc
                token["triggered"] = set()  # сбрасываем алерты

            # Падение от ATH marketcap
            drop = (token["ath_mc"] - mc) / token["ath_mc"]

            for threshold in token["alerts"]:
                if drop >= threshold and threshold not in token["triggered"]:
                    token["triggered"].add(threshold)

                    await bot.send_message(
                        chat_id,
                        f"⚠️ {token['name']} упал на {int(threshold*100)}% от ATH MarketCap\n"
                        f"ATH MarketCap: {token['ath_mc']}\n"
                        f"Текущий MarketCap: {mc}"
                    )

        await asyncio.sleep(10)

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

        await msg.answer(f"Токен {name} добавлен!\nОтслеживаю падение по MarketCap.")
    except:
        await msg.answer("Использование:\n/add <contract_address>")

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
            await msg.answer(f"Токен {name} удалён.")
        else:
            await msg.answer("Такого токена нет в списке.")
    except:
        await msg.answer("Использование:\n/remove <contract_address>")

# -----------------------------
#        /reset
# -----------------------------
@dp.message_handler(commands=["reset"])
async def reset_alerts(msg: types.Message):
    for token in TOKENS.values():
        token["triggered"] = set()
    await msg.answer("Все алерты сброшены!")

# -----------------------------
#        /list
# -----------------------------
@dp.message_handler(commands=["list"])
async def list_tokens(msg: types.Message):
    if not TOKENS:
        await msg.answer("Нет отслеживаемых токенов.")
        return

    text = "Отслеживаемые токены:\n\n"
    for address, token in TOKENS.items():
        text += f"• {token['name']} (ATH MarketCap: {token['ath_mc']})\n"

    await msg.answer(text)

# -----------------------------
#        /commands
# -----------------------------
@dp.message_handler(commands=["commands"])
async def commands(msg: types.Message):
    text = (
        "/start – запустить бота\n"
        "/add <address> – добавить токен\n"
        "/remove <address> – удалить токен\n"
        "/list – список токенов\n"
        "/reset – сбросить алерты\n"
        "/commands – список всех команд\n"
    )
    await msg.answer(text)

# -----------------------------
#        /start
# -----------------------------
@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.answer("Бот запущен! Кидай контракты через /add <address>")
    asyncio.create_task(monitor(msg.chat.id))

async def main():
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())



