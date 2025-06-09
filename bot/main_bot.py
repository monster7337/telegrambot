import asyncio
import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from contextlib import suppress
from aiogram.exceptions import TelegramAPIError

# --- Настройки ---
BOT_TOKEN = "7562714456:AAHLG6zZxjUduK8Koh0-N_Z0fOtKRNGcq8Y"
API_BASE_URL = "http://127.0.0.1:8000" 

# --- Константы статусов ---
STATUS_APPROVED = "✅ Одобрена, поиск водителя"
STATUS_DECLINED = "❌ Отклонена диспетчером"
STATUS_COMPLETED = "🏁 Выполнена"

# --- Инициализация ---
api_client = httpx.AsyncClient(base_url=API_BASE_URL)
dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)

# Меню для Заказчика
customer_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🆕 Создать заявку")], [KeyboardButton(text="📋 Мои заявки")]],
    resize_keyboard=True
)
# Меню для Водителя
driver_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Новые задачи"), KeyboardButton(text="🚛 Мои активные задачи")],
        [KeyboardButton(text="📖 История доставок")]
    ], resize_keyboard=True)
# Меню для Диспетчера
dispatcher_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🧐 Заявки на утверждение")]],
    resize_keyboard=True
)
# Кнопки подтверждения для заказчика
confirm_order_kb = InlineKeyboardMarkup(
    inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="order_confirm"),
        InlineKeyboardButton(text="✏️ Отменить", callback_data="order_cancel")
    ]]
)
cancel_fsm_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отменить создание")]], resize_keyboard=True, one_time_keyboard=True)
# Кнопки для действий диспетчера
def get_dispatcher_approval_kb(order_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Утвердить", callback_data=f"dispatch_approve_{order_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dispatch_decline_{order_id}")
        ]]
    )

# --- FSM и Валидация ---
def is_valid_phone(phone: str):
    clean_phone = "".join(filter(str.isdigit, phone))
    return len(clean_phone) >= 10 and len(clean_phone) <= 12

class OrderFSM(StatesGroup):
    getting_address_from = State()
    getting_address_to = State()
    getting_cargo_description = State()
    getting_phone_number = State()
    confirming_order = State() 
    
class DispatcherDeclineFSM(StatesGroup):
    getting_reason = State()    
    
# --- Вспомогательная функция для форматирования ---
async def format_order_details(order: dict) -> str:
    payload = order.get('payload', {})
    decline_reason = payload.get('decline_reason')
    details = (
        f"<b>Заявка №{order.get('id', 'N/A')}</b>\n"
        f"Статус: {order.get('status', 'N/A')}\n\n"
        f"📍 <b>Откуда:</b> {payload.get('address_from', 'Не указано')}\n"
        f"🏁 <b>Куда:</b> {payload.get('address_to', 'Не указано')}\n"
        f"📦 <b>Груз:</b> {payload.get('cargo', 'Не указано')}\n"
        f"📞 <b>Телефон:</b> {payload.get('phone', 'Не указано')}"
    )
    if decline_reason:
        details += f"\n\n💬 <b>Причина отказа:</b> {decline_reason}"
    return details

# --- Главный обработчик /start ---
@dp.message(CommandStart())
async def command_start_handler(message: Message):
    telegram_id = message.from_user.id
    try:
        response = await api_client.get(f"/users/by_telegram/{telegram_id}")
        response.raise_for_status()
        user = response.json()
        
        if user['role'] == 'customer':
            await message.answer(f"Здравствуйте, {user['name']}!", reply_markup=customer_menu)
        elif user['role'] == 'driver':
            await message.answer(f"Здравствуйте, {user['name']}!", reply_markup=driver_menu)
        elif user['role'] == 'dispatcher':
            await message.answer(f"Здравствуйте, {user['name']}! Вы вошли как диспетчер.", reply_markup=dispatcher_menu)
    
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            await message.answer("Вы не зарегистрированы в системе. Обратитесь к администратору.")
        else:
            await message.answer(f"Произошла ошибка при связи с сервером: {e}")

# === ЛОГИКА СОЗДАНИЯ ЗАЯВКИ (FSM) ---
@dp.message(StateFilter(OrderFSM), F.text == "❌ Отменить создание")
async def cancel_fsm_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Создание заявки отменено.", reply_markup=customer_menu)

@dp.message(F.text == "🆕 Создать заявку")
async def start_order(message: Message, state: FSMContext):
    await state.set_state(OrderFSM.getting_address_from)
    await message.answer("Введите адрес подачи (откуда забрать):", reply_markup=cancel_fsm_kb)
@dp.message(OrderFSM.getting_address_from)
async def get_address_from(message: Message, state: FSMContext):
    await state.update_data(address_from=message.text)
    await state.set_state(OrderFSM.getting_address_to)
    await message.answer("Теперь введите адрес назначения (куда доставить):", reply_markup=cancel_fsm_kb)
@dp.message(OrderFSM.getting_address_to)
async def get_address_to(message: Message, state: FSMContext):
    await state.update_data(address_to=message.text)
    await state.set_state(OrderFSM.getting_cargo_description)
    await message.answer("Опишите ваш груз:", reply_markup=cancel_fsm_kb)
@dp.message(OrderFSM.getting_cargo_description)
async def get_cargo_description(message: Message, state: FSMContext):
    await state.update_data(cargo=message.text)
    await state.set_state(OrderFSM.getting_phone_number)
    await message.answer("Введите контактный номер телефона для связи:", reply_markup=cancel_fsm_kb)
@dp.message(OrderFSM.getting_phone_number)
async def get_phone_number(message: Message, state: FSMContext):
    if not is_valid_phone(message.text):
        await message.answer("❌ Неверный формат номера. Пожалуйста, введите корректный номер телефона (не менее 10 цифр).", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(phone=message.text)
    data = await state.get_data()
    
    confirmation_text = (
        "Пожалуйста, проверьте вашу заявку:\n\n"
        f"<b>Откуда:</b> {data['address_from']}\n"
        f"<b>Куда:</b> {data['address_to']}\n"
        f"<b>Груз:</b> {data['cargo']}\n"
        f"<b>📞 Контактный телефон:</b> {data['phone']}\n\n"
        "Всё верно?"
    )
    await message.answer(confirmation_text, reply_markup=confirm_order_kb, parse_mode="HTML")
    await state.set_state(OrderFSM.confirming_order)

@dp.callback_query(OrderFSM.confirming_order, F.data == "order_cancel")
async def cancel_order_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    await callback.message.delete()

    await bot.send_message(
        chat_id=callback.from_user.id,
        text="Создание заявки отменено.",
        reply_markup=customer_menu
    )

# === ПОДТВЕРЖДЕНИЕ ЗАЯВКИ И УВЕДОМЛЕНИЕ ДИСПЕТЧЕРА ---
@dp.callback_query(OrderFSM.confirming_order, F.data == "order_confirm")
async def confirm_order_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    telegram_id = callback.from_user.id
    try:
        response = await api_client.post("/orders/", params={"customer_telegram_id": telegram_id}, json=data)
        response.raise_for_status()
        order = response.json()
        
        await bot.send_message(
    chat_id=telegram_id,
    text=f"✅ Заявка №{order['id']} принята и отправлена на проверку диспетчеру.",
    reply_markup=customer_menu
)


        # Уведомляем всех диспетчеров
        dispatchers_response = await api_client.get("/users/by_role/dispatcher")
        if dispatchers_response.status_code == 200:
            dispatchers = dispatchers_response.json()
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👀 Посмотреть заявку", callback_data=f"view_order_{order['id']}")]
            ])
            for dispatcher in dispatchers:
                with suppress(TelegramAPIError):
                    await bot.send_message(
                        chat_id=dispatcher['telegram_id'],
                        text=f"❗️ Поступила новая заявка №{order['id']} на утверждение.",
                        reply_markup=keyboard
                    )
    except httpx.HTTPStatusError as e:
        await callback.message.edit_text(f"❌ Ошибка при создании заявки: {e.response.text}")
    
    await state.clear()
    await callback.answer()

# === ЛОГИКА ДЛЯ ЗАКАЗЧИКА (ПРОСМОТР ЗАЯВОК) ---
@dp.message(F.text == "📋 Мои заявки")
async def my_orders_handler(message: Message):
    telegram_id = message.from_user.id
    try:
        response = await api_client.get(f"/orders/customer/{telegram_id}")
        response.raise_for_status()
        orders = response.json()
        if not orders:
            await message.answer("У вас пока нет заявок.")
            return
        await message.answer("Ваши заявки:")
        for order in orders:
            await message.answer(await format_order_details(order), parse_mode="HTML")
    except Exception as e:
        await message.answer(f"Не удалось загрузить заявки: {e}")

# === ЛОГИКА ДЛЯ ДИСПЕТЧЕРА ===
@dp.message(F.text == "🧐 Заявки на утверждение")
async def show_pending_orders(message: Message):
    response = await api_client.get("/orders/pending_approval")
    if response.status_code == 200:
        orders = response.json()
        if not orders:
            await message.answer("Заявок на утверждение нет.")
            return
        await message.answer("Заявки, ожидающие вашего решения:")
        for order in orders:
            await message.answer(
                await format_order_details(order),
                parse_mode="HTML",
                reply_markup=get_dispatcher_approval_kb(order['id'])
            )
    else:
        await message.answer("Не удалось загрузить заявки.")

@dp.callback_query(F.data.startswith("view_order_"))
async def view_order_handler(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    response = await api_client.get(f"/orders/{order_id}")
    if response.status_code == 200:
        order = response.json()
        await callback.message.answer(
            await format_order_details(order),
            parse_mode="HTML",
            reply_markup=get_dispatcher_approval_kb(order['id'])
        )
        await callback.answer()
    else:
        await callback.answer("Не удалось найти заявку.", show_alert=True)

@dp.callback_query(F.data.startswith("dispatch_approve_"))
async def dispatch_approve_handler(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    params = {"status": STATUS_APPROVED}
    response = await api_client.post(f"/orders/{order_id}/status", params=params)
    if response.status_code == 200:
        order = response.json()
        await callback.message.edit_text(f"✅ Заявка №{order_id} утверждена. Теперь она доступна водителям.")
        with suppress(TelegramAPIError):
            await bot.send_message(
                chat_id=order['customer_telegram_id'],
                text=f"👍 Ваша заявка №{order_id} была утверждена диспетчером! Теперь начнётся поиск водителя."
            )
    else:
        await callback.message.edit_text(f"❌ Ошибка: {response.text}")
    await callback.answer()

@dp.callback_query(F.data.startswith("dispatch_decline_"))
async def dispatch_decline_init_handler(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split("_")[2])
    await state.set_state(DispatcherDeclineFSM.getting_reason)
    await state.update_data(order_id=order_id, original_message=callback.message)
    await callback.message.edit_text(f"✍️ Введите причину отказа для заявки №{order_id}:")
    await callback.answer()

@dp.message(DispatcherDeclineFSM.getting_reason)
async def dispatch_decline_reason_handler(message: Message, state: FSMContext):
    reason = message.text
    data = await state.get_data()
    order_id = data['order_id']
    original_message = data['original_message']

    params = {"status": STATUS_DECLINED, "reason": reason}
    response = await api_client.post(f"/orders/{order_id}/status", params=params)
    
    if response.status_code == 200:
        order = response.json()
        await original_message.edit_text(f"❌ Заявка отклонена:\n\n{await format_order_details(order)}", parse_mode="HTML")
        with suppress(TelegramAPIError):
            await bot.send_message(chat_id=order['customer_telegram_id'], text=f"😔 К сожалению, ваша заявка №{order_id} была отклонена.\n<b>Причина:</b> {reason}", parse_mode="HTML")
    else:
        await original_message.edit_text(f"❌ Ошибка: {response.text}")
    
    await state.clear()


# === ЛОГИКА ДЛЯ ВОДИТЕЛЯ ---
@dp.message(F.text == "📝 Новые задачи")
async def get_available_tasks(message: Message):
    try:
        response = await api_client.get("/orders/driver/available")
        response.raise_for_status()
        orders = response.json()
        if not orders:
            await message.answer("Свободных заявок нет. Отдыхайте!")
            return
        await message.answer("Доступные заявки:")
        for order in orders:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Взять в работу", callback_data=f"take_order_{order['id']}")]
            ])
            await message.answer(await format_order_details(order), reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"Не удалось получить список задач: {e}")
@dp.message(F.text == "📖 История доставок")
async def driver_order_history(message: Message):
    telegram_id = message.from_user.id
    try:
        response = await api_client.get(f"/orders/driver/{telegram_id}/history")
        response.raise_for_status()
        orders = response.json()
        if not orders:
            await message.answer("Ваша история доставок пуста.")
            return
        await message.answer("Выполненные вами заявки:")
        for order in orders:
            await message.answer(await format_order_details(order), parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось загрузить историю: {e}")

@dp.callback_query(F.data.startswith("take_order_"))
async def take_order_callback(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    driver_telegram_id = callback.from_user.id
    try:
        response = await api_client.post(f"/orders/{order_id}/assign/{driver_telegram_id}")
        response.raise_for_status()
        order = response.json()
        await callback.message.edit_text(f"✅ Вы приняли заявку №{order_id} в работу.")
        customer_id = order.get("customer_telegram_id")
        if customer_id:
            with suppress(TelegramAPIError):
                await bot.send_message(
                    chat_id=customer_id,
                    text=f"🚗 Ваша заявка №{order_id} принята водителем! Он скоро свяжется с вами."
                )
    except Exception:
        await callback.message.edit_text("❌ Не удалось принять заявку. Возможно, ее уже кто-то взял.")
    await callback.answer()

@dp.message(F.text == "🚛 Мои активные задачи")
async def driver_active_orders(message: Message):
    telegram_id = message.from_user.id
    try:
        response = await api_client.get(f"/orders/driver/{telegram_id}/active")
        response.raise_for_status()
        orders = response.json()
        
        if not orders:
            await message.answer("У вас пока нет активных задач.")
            return

        for order in orders:
            buttons = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Забрал", callback_data=f"status_{order['id']}_picked"),
                    InlineKeyboardButton(text="🚚 В пути", callback_data=f"status_{order['id']}_delivering"),
                ],
                [
                    InlineKeyboardButton(text="✅ Доставлено", callback_data=f"status_{order['id']}_delivered")
                ]
            ])
            await message.answer(
                await format_order_details(order),
                parse_mode="HTML",
                reply_markup=buttons
            )
    except Exception as e:
        await message.answer(f"❌ Не удалось получить активные задачи: {e}")

@dp.callback_query(F.data.startswith("status_"))
async def update_status_by_driver(callback: CallbackQuery):
    parts = callback.data.split("_")
    order_id = int(parts[1])
    status_map = {"picked": "🔄 Заказ забран", "delivering": "🚚 В пути", "delivered": STATUS_COMPLETED}
    status_text = status_map.get(parts[2])
    if not status_text: await callback.answer("Неизвестный статус."); return
    try:
        response = await api_client.post(f"/orders/{order_id}/status", params={"status": status_text})
        response.raise_for_status()
        order = response.json()
        if customer_telegram_id := order.get("customer_telegram_id"):
            with suppress(TelegramAPIError):
                await bot.send_message(chat_id=customer_telegram_id, text=f"📦 Ваша заявка №{order_id} обновлена:\nСтатус: {status_text}")
        
        final_text = f"📌 Статус заявки №{order_id} обновлён на: {status_text}"
        if status_text == STATUS_COMPLETED:
            final_text += "\n\nЗаявка перемещена в историю."
        await callback.message.edit_text(final_text)
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при обновлении статуса: {e}")
    await callback.answer()


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())