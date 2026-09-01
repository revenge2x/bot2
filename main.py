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

async def get_price(address):
    url = f"https://public-api.birdeye.so/public/price?address={address}"
    headers = {"x-api-key": BIRDEYE_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return data["data"]["value"]

async def monitor(chat_id):
    while True:
        for address, token in TOKENS.items():
            price = await get_price(address)

            # Если ATH ещё не установлен — ставим текущую цену как ATH
            if token["ath"] is None:
                token["ath"] = price
                continue

            # Если цена выросла выше ATH — обновляем ATH
            if price > token["ath"]:
                token["ath"] = price
                token["triggered"] = set()  # сбрасываем алерты

            # Падение от ATH
            drop = (token["ath"] - price) / token["ath"]

            for threshold in token["alerts"]:
                if drop >= threshold and threshold not in token["triggered"]:
                    token["triggered"].add(threshold)

                    await bot.send_message(
                        chat_id,
                        f"⚠️ Токен {address} упал на {int(threshold*100)}% от ATH\n"
                        f"ATH: {token['ath']}\n"
                        f"Текущая цена: {price}"
                    )

        await asyncio.sleep(10)

@dp.message_handler(commands=["add"])
async def add_token(msg: types.Message):
    try:
        address = msg.text.split()[1]

        TOKENS[address] = {
            "ath": None,  # ATH будет установлено автоматически
            "alerts": [0.60, 0.65, 0.70, 0.80],
            "triggered": set()
        }

        await msg.answer(f"Токен добавлен!\nНачинаю отслеживать падение от ATH: {address}")
    except:
        await msg.answer("Использование:\n/add <contract_address>")

@dp.message_handler(commands=["list"])
async def list_tokens(msg: types.Message):
    if not TOKENS:
        await msg.answer("Нет отслеживаемых токенов.")
        return

    text = "Отслеживаемые токены:\n\n"
    for address, token in TOKENS.items():
        text += f"• {address} (ATH: {token['ath']})\n"

    await msg.answer(text)

@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    await msg.answer("Бот запущен! Кидай контракты через /add <address>")
    asyncio.create_task(monitor(msg.chat.id))

async def main():
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())


