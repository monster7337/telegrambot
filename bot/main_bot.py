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
import os
# --- Настройки ---
BOT_TOKEN = "7562714456:AAHLG6zZxjUduK8Koh0-N_Z0fOtKRNGcq8Y"
API_URL = os.getenv("API_URL", "http://localhost:8000")
api_client = httpx.AsyncClient(base_url=API_URL)

# --- Константы статусов ---
STATUS_APPROVED = "✅ Одобрена, поиск водителя"
STATUS_DECLINED = "❌ Отклонена диспетчером"
STATUS_COMPLETED = "🏁 Выполнена"

# --- Инициализация ---
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
    getting_cargo_name = State()
    getting_cargo_weight = State()
    getting_cargo_count = State()
    getting_cargo_size = State()
    getting_documents_info = State()
    getting_docs_contact = State()
    getting_cargo_contact = State()
    getting_address_from = State()
    getting_address_to = State()
    getting_recipient_info = State()
    getting_payment_required = State()
    getting_lead_time = State()
    getting_extra_info = State()
    confirming_order = State()
    waiting_driver_message = State()
    waiting_new_time = State()
    waiting_delay_reason = State()

    
class DispatcherDeclineFSM(StatesGroup):
    getting_reason = State()    
    
# --- Вспомогательная функция для форматирования ---
import json


async def format_order_details(order: dict) -> str:
    # Страховка на случай, если order — строка (вместо словаря)
    if isinstance(order, str):
        try:
            order = json.loads(order)
        except Exception:
            return "❌ Невозможно прочитать заявку."


    payload = order.get("payload", {})
    
    # Страховка, если payload оказался строкой
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    cargo = payload.get("cargo", {})
    get_from = payload.get("get_from", {})
    deliver_to = payload.get("deliver_to", {})
    pickup_contact = payload.get("pickup_contact", {})
    status = order.get("status", "N/A")

    details = (
        f"<b>Заявка №{order.get('id', 'N/A')}</b>\n"
        f"Статус: {status}\n\n"
        f"📦 <b>Что везем:</b>\n"
        f"- Наименование: {cargo.get('name', '—')}\n"
        f"- Вес: {cargo.get('weight', '—')} кг\n"
        f"- Кол-во: {cargo.get('count', '—')} шт\n"
        f"- Размеры: {cargo.get('size', '—')}\n\n"
        f"📑 <b>Документы:</b> {payload.get('documents', '—')}\n\n"
        f"📍 <b>Забрать документы у:</b>\n"
        f"- {get_from.get('name', '—')}\n"
        f"- {get_from.get('address', '—')}\n"
        f"- 📞 {get_from.get('phone', '—')}\n\n"
        f"🚚 <b>Забрать груз у:</b>\n"
        f"- {pickup_contact.get('name', '—')}, 📞 {pickup_contact.get('phone', '—')}\n"
        f"- Адрес: {payload.get('address_from', '—')}\n\n"
        f"🏁 <b>Доставить по адресу:</b>\n"
        f"- {deliver_to.get('address', '—')}\n"
        f"- Получатель: {deliver_to.get('name', '—')}, 📞 {deliver_to.get('phone', '—')}\n\n"
        f"💰 <b>Оплата:</b> {'Да' if payload.get('need_payment') else 'Нет'}\n"
        f"🕒 <b>Выполнить до:</b> {payload.get('lead_time', '—')}\n"
    )

    extra = payload.get("extra_info") or payload.get("extra") or payload.get("comments")
    if extra:
        details += f"\nℹ️ <b>Доп. информация:</b> {extra}"

    if payload.get("decline_reason"):
        details += f"\n\n💬 <b>Причина отказа:</b> {payload['decline_reason']}"

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
    await state.set_state(OrderFSM.getting_cargo_name)
    await message.answer("📦 Укажите наименование груза:", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_cargo_name)
async def get_cargo_name(message: Message, state: FSMContext):
    await state.update_data(cargo_name=message.text)
    await state.set_state(OrderFSM.getting_cargo_weight)
    await message.answer("⚖️ Укажите вес груза в килограммах:", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_cargo_weight)
async def get_cargo_weight(message: Message, state: FSMContext):
    try:
        weight = int(message.text)
        if weight <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое положительное число для веса.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(cargo_weight=weight)
    await state.set_state(OrderFSM.getting_cargo_count)
    await message.answer("📦 Укажите количество единиц груза:", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_cargo_count)
async def get_cargo_count(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if count <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое положительное число для количества.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(cargo_count=count)
    await state.set_state(OrderFSM.getting_cargo_size)
    await message.answer("📀 Укажите габариты груза (например: 120x80x100 см):", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_cargo_size)
async def get_cargo_size(message: Message, state: FSMContext):
    await state.update_data(cargo_size=message.text)
    await state.set_state(OrderFSM.getting_documents_info)
    await message.answer("📁 Документы будут? Укажите количество экземпляров и действия с ними:", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_documents_info)
async def get_documents_info(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    await state.update_data(documents_info=message.text)

    if text in ["нет", "не будет", "нету", "отсутствуют"]:
        await state.update_data(docs_contact=None)
        await state.set_state(OrderFSM.getting_cargo_contact)
        await message.answer("👤 Укажите имя и телефон того, кто отдаст груз.\n\nФормат: Алексей, +79991112233", reply_markup=cancel_fsm_kb)
    else:
        await state.set_state(OrderFSM.getting_docs_contact)
        await message.answer(
            "📍 Укажите имя, адрес и телефон того, у кого забрать документы.\n\n"
            "Формат: Иван Иванов, г. Москва, ул. Документовая, д.1, +79991234567",
            reply_markup=cancel_fsm_kb
        )


@dp.message(OrderFSM.getting_cargo_contact)
async def get_cargo_contact(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 2:
        await message.answer("❌ Введите имя и телефон через запятую.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(cargo_contact={
        "name": parts[0],
        "phone": parts[1]
    })
    await state.set_state(OrderFSM.getting_address_from)
    await message.answer("📍 Укажите адрес, откуда забирать груз:", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_address_from)
async def get_address_from(message: Message, state: FSMContext):
    await state.update_data(address_from=message.text)
    await state.set_state(OrderFSM.getting_address_to)
    await message.answer("🏁 Укажите адрес, куда доставить груз:", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_address_to)
async def get_address_to(message: Message, state: FSMContext):
    await state.update_data(address_to=message.text)
    await state.set_state(OrderFSM.getting_recipient_info)
    await message.answer("🎯 Укажите имя и телефон получателя.\n\nФормат: ООО ПриемГруз, +79997654321", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_recipient_info)
async def get_recipient_info(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 2:
        await message.answer("❌ Введите имя и телефон через запятую.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(recipient={
        "name": parts[0],
        "phone": parts[1]
    })
    await state.set_state(OrderFSM.getting_payment_required)
    await message.answer("💰 Требуется ли оплата? Введите 'Да' или 'Нет':", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_payment_required)
async def get_payment_required(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    if text not in ["да", "нет"]:
        await message.answer("❌ Пожалуйста, введите 'Да' или 'Нет'.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(need_payment=(text == "да"))
    await state.set_state(OrderFSM.getting_lead_time)
    await message.answer("🕒 Укажите желаемое время выполнения задачи.\nФормат: ГГГГ-ММ-ДД ЧЧ:ММ (например, 2025-06-16 14:30)", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_lead_time)
async def get_lead_time(message: Message, state: FSMContext):
    import datetime
    try:
        dt = datetime.datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
        await state.update_data(lead_time=dt.isoformat())
        await state.set_state(OrderFSM.getting_extra_info)
        await message.answer("ℹ️ Дополнительная информация (если есть). Если нет — напишите «-»", reply_markup=cancel_fsm_kb)
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД ЧЧ:ММ", reply_markup=cancel_fsm_kb)


@dp.message(OrderFSM.getting_extra_info)
async def get_extra_info(message: Message, state: FSMContext):
    extra_info = message.text.strip()
    if extra_info == "-":
        extra_info = ""
    await state.update_data(extra_info=extra_info)

    data = await state.get_data()

    summary = (
        f"<b>📦 Заявка на доставку:</b>\n\n"
        f"<b>Груз:</b> {data['cargo_name']}, {data['cargo_weight']} кг, {data['cargo_count']} шт, {data['cargo_size']}\n"
        f"<b>Документы:</b> {data['documents_info']}\n"
    )

    if data.get("docs_contact"):
        docs = data["docs_contact"]
        summary += (
            f"<b>Забрать документы у:</b> {docs['name']} — {docs['phone']}, {docs['address']}\n"
        )

    summary += (
        f"<b>Забрать груз у:</b> {data['cargo_contact']['name']} — {data['cargo_contact']['phone']}\n"
        f"<b>Адрес забора:</b> {data['address_from']}\n"
        f"<b>Адрес доставки:</b> {data['address_to']}\n"
        f"<b>Получатель:</b> {data['recipient']['name']} — {data['recipient']['phone']}\n"
        f"<b>Оплата:</b> {'Да' if data['need_payment'] else 'Нет'}\n"
        f"<b>Выполнить до:</b> {data['lead_time']}\n"
    )

    if data['extra_info']:
        summary += f"<b>ℹ️ Доп. информация:</b> {data['extra_info']}\n"

    summary += "\nВсё верно?"

    await state.set_state(OrderFSM.confirming_order)
    await message.answer(summary, reply_markup=confirm_order_kb, parse_mode="HTML")


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

    payload = {
        "cargo": {
            "name": data["cargo_name"],
            "weight": data["cargo_weight"],
            "count": data["cargo_count"],
            "size": data["cargo_size"]
        },
        "documents": data["documents_info"],
        "get_from": data.get("docs_contact") or data["cargo_contact"],
        "deliver_to": {
            "name": data["recipient"]["name"],
            "phone": data["recipient"]["phone"],
            "address": data["address_to"]
        },
        "pickup_contact": data["cargo_contact"],
        "address_from": data["address_from"],
        "need_payment": data["need_payment"],
        "lead_time": data["lead_time"],
        "extra_info": data["extra_info"]
    }

    try:
        # 1. Создаём заявку
        response = await api_client.post(
            "/orders/",
            params={"customer_telegram_id": telegram_id},
            json=payload
        )
        response.raise_for_status()
        order = response.json()

        await callback.message.delete()

        await bot.send_message(
            chat_id=telegram_id,
            text=f"✅ Заявка №{order['id']} создана и отправлена диспетчеру.",
            reply_markup=customer_menu
        )

        # 2. Получаем всех диспетчеров
        users_response = await api_client.get("/users/")
        users_response.raise_for_status()
        users = users_response.json()

        dispatcher_ids = [
            user["telegram_id"]
            for user in users
            if user.get("role") == "dispatcher"
        ]

        for dispatcher_id in dispatcher_ids:
            await bot.send_message(
                chat_id=dispatcher_id,
                text=(
                    f"📬 Поступила новая заявка от клиента {telegram_id}:\n"
                    f"ID заявки: {order['id']}\n"
                    f"Статус: {order['status']}"
                )
            )

    except httpx.HTTPStatusError as e:
        await bot.send_message(
            chat_id=telegram_id,
            text=f"❌ Ошибка при создании заявки: {e.response.text}"
        )

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
            if isinstance(order, str):
                try:
                    import json
                    order = json.loads(order)
                except Exception as e:
                    await message.answer(f"❌ Не удалось прочитать заявку: {e}")
                    continue

            try:
                text = await format_order_details(order)
            except Exception as e:
                text = f"❌ Ошибка при разборе заявки: {e}"

            await message.answer(text, parse_mode="HTML")

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
        InlineKeyboardButton(text="✅ Доставлено", callback_data=f"status_{order['id']}_delivered"),
        InlineKeyboardButton(text="📩 Связь с заказчиком", callback_data=f"contact_customer_{order['id']}")
    ]
])

            await message.answer(
                await format_order_details(order),
                parse_mode="HTML",
                reply_markup=buttons
            )
    except Exception as e:
        await message.answer(f"❌ Не удалось получить активные задачи: {e}")
@dp.callback_query(F.data.startswith("contact_customer_"))
async def start_contact_customer(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split("_")[2])
    await state.update_data(order_id=order_id)
    await state.set_state(OrderFSM.waiting_driver_message)
    await callback.message.answer("✉️ Напишите сообщение для заказчика.\nЕсли хотите перенести время — напишите: *Перенос времени*")
    await callback.answer()
@dp.message(OrderFSM.waiting_driver_message)
async def handle_driver_message(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    order_id = data["order_id"]

    if text.lower().startswith("перенос времени"):
        await state.set_state(OrderFSM.waiting_new_time)
        await message.answer("📅 Укажите новую дату и время в формате: ГГГГ-ММ-ДД ЧЧ:ММ")
    else:
        try:
            order = (await api_client.get(f"/orders/{order_id}")).json()
            customer_id = order["customer_telegram_id"]
            await bot.send_message(
                chat_id=customer_id,
                text=f"📨 Сообщение от водителя по заявке №{order_id}:\n\n{text}"
            )
            await message.answer("✅ Сообщение отправлено заказчику.")
        except Exception as e:
            await message.answer(f"❌ Не удалось отправить сообщение: {e}")
        await state.clear()
@dp.message(OrderFSM.waiting_new_time)
async def get_new_time(message: Message, state: FSMContext):
    import datetime
    try:
        new_time = datetime.datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
        await state.update_data(new_lead_time=new_time.isoformat())
        await state.set_state(OrderFSM.waiting_delay_reason)
        await message.answer("📝 Укажите причину переноса:")
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: 2025-06-20 15:30")
@dp.message(OrderFSM.waiting_delay_reason)
async def send_delay_info(message: Message, state: FSMContext):
    data = await state.get_data()
    reason = message.text.strip()
    order_id = data["order_id"]
    new_time = data["new_lead_time"]

    try:
        order = (await api_client.get(f"/orders/{order_id}")).json()
        customer_id = order["customer_telegram_id"]
        await bot.send_message(
            chat_id=customer_id,
            text=(
                f"📦 Ваша доставка №{order_id} перенесена.\n"
                f"🕒 Новое время: {new_time}\n"
                f"📄 Причина: {reason}"
            )
        )
        await message.answer("✅ Перенос времени отправлен заказчику.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке: {e}")
    await state.clear()

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