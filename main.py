import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types

API_TOKEN = "7985537464:AAFqegtfyt1cxHBZ1EViBWdL-pHfhHQIVaA"

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

async def start(msg: types.Message):
    await msg.answer("Бот запущен на Amvera!")

dp.register_message_handler(start, commands=["start"])

async def main():
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
