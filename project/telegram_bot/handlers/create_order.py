from aiogram import Router, types, F
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from ..services.api import send_order

router = Router()

class OrderForm(StatesGroup):
    cargo_description = State()
    pickup_address = State()
    delivery_address = State()
    contact_phone = State()
    confirm = State()

@router.message(F.text.lower() == "заказ")
async def start_order(message: types.Message, state: FSMContext):
    await message.answer("Что вы хотите перевезти?")
    await state.set_state(OrderForm.cargo_description)

@router.message(OrderForm.cargo_description)
async def set_cargo(message: types.Message, state: FSMContext):
    await state.update_data(cargo_description=message.text)
    await message.answer("Укажите адрес забора груза:")
    await state.set_state(OrderForm.pickup_address)

@router.message(OrderForm.pickup_address)
async def set_pickup(message: types.Message, state: FSMContext):
    await state.update_data(pickup_address=message.text)
    await message.answer("Укажите адрес доставки:")
    await state.set_state(OrderForm.delivery_address)

@router.message(OrderForm.delivery_address)
async def set_delivery(message: types.Message, state: FSMContext):
    await state.update_data(delivery_address=message.text)
    await message.answer("Введите контактный телефон получателя:")
    await state.set_state(OrderForm.contact_phone)

@router.message(OrderForm.contact_phone)
async def set_phone(message: types.Message, state: FSMContext):
    await state.update_data(contact_phone=message.text)
    data = await state.get_data()

    summary = (
        f"Проверьте заявку:\n"
        f"Груз: {data['cargo_description']}\n"
        f"Забрать: {data['pickup_address']}\n"
        f"Доставить: {data['delivery_address']}\n"
        f"Контакт: {data['contact_phone']}\n"
        "Подтвердите отправку? (да/нет)"
    )

    await message.answer(summary)
    await state.set_state(OrderForm.confirm)

@router.message(OrderForm.confirm)
async def confirm_order(message: types.Message, state: FSMContext):
    if message.text.lower() == "да":
        data = await state.get_data()
        await send_order(
            telegram_id=message.from_user.id,
            payload=data
        )
        await message.answer("Заявка отправлена!")
    else:
        await message.answer("Заявка отменена.")

    await state.clear()
