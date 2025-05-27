from aiogram import Router, types
from aiogram.filters import CommandStart

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я бот для оформления заявок. Напиши 'заказ', чтобы начать.")
