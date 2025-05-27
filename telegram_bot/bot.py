import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
from telegram_bot.handlers import start, create_order


load_dotenv()
TOKEN = os.getenv("7562714456:AAHLG6zZxjUduK8Koh0-N_Z0fOtKRNGcq8Y")

logging.basicConfig(level=logging.INFO)

bot = Bot(token="7562714456:AAHLG6zZxjUduK8Koh0-N_Z0fOtKRNGcq8Y")
dp = Dispatcher()

dp.include_routers(
    start.router,
    create_order.router,
    
)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
