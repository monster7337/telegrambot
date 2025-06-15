
import asyncio
import datetime
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
import json

# --- Настройки ----------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN env var")

API_URL = os.getenv("API_URL", "http://localhost:8000")
api_client = httpx.AsyncClient(base_url=API_URL)

# --- Статусы в соответствии с новой схемой ------------------------------
STATUS_CREATED = "created"
STATUS_PLANNING = "planning"
STATUS_DRIVER_ASSIGNED = "driver_assigned"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_ARCHIVED = "archived"

# --- Инициализация ------------------------------------------------------
dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)

# --- Клавиатуры ---------------------------------------------------------

customer_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🆕 Создать заявку")], [KeyboardButton(text="📋 Мои заявки")]],
    resize_keyboard=True
)
driver_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Новые задачи"), KeyboardButton(text="🚛 Мои активные задачи")],
        [KeyboardButton(text="📖 История доставок")]
    ],
    resize_keyboard=True
)
dispatcher_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🧐 Заявки на утверждение")]],
    resize_keyboard=True
)

confirm_order_kb = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="✅ Подтвердить", callback_data="order_confirm"),
                      InlineKeyboardButton(text="✏️ Отменить", callback_data="order_cancel")]]
)
cancel_fsm_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отменить создание")]],
                                    resize_keyboard=True, one_time_keyboard=True)

def get_dispatcher_approval_kb(order_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Утвердить", callback_data=f"dispatch_approve_{order_id}"),
                          InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dispatch_decline_{order_id}")]]
    )

# --- FSM ---------------------------------------------------------------

def is_valid_phone(phone: str):
    clean = "".join(ch for ch in phone if ch.isdigit())
    return 10 <= len(clean) <= 12


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

# --- Вспомогательные функции -------------------------------------------

def parse_address_block(block):
    if isinstance(block, str):
        try:
            # Попробуем распарсить строку как JSON-строку
            block = json.loads(block)
        except Exception:
            # Если не JSON, то пробуем парсить как просто строку через запятую
            parts = block.split(",")
            return {
                "name": parts[0].strip() if len(parts) > 0 else "-",
                "phone": parts[1].strip() if len(parts) > 1 else "-",
                "address": ", ".join(parts[2:]).strip() if len(parts) > 2 else "-"
            }
    if isinstance(block, dict):
        return {
            "name": block.get("name", "-"),
            "phone": block.get("phone", "-"),
            "address": block.get("address", "-")
        }
    return {"name": "-", "phone": "-", "address": "-"}

async def format_order_details(order: dict) -> str:
    if isinstance(order, str):
        try:
            order = json.loads(order)
        except Exception:
            return "❌ Невозможно прочитать заявку."

    item = order.get("item", {})
    get_from = parse_address_block(item.get("get_from", {}))
    deliver_to = parse_address_block(item.get("deliver_to", {}))

    return (
        f"<b>Заявка №{order['id']}</b>\n"
        f"Статус: {order['status']}\n\n"
        f"📦 <b>Груз:</b> {item.get('name', '-')}, {item.get('weight', '-')} кг, "
        f"{item.get('count', '-')} шт, {item.get('size', '-')}\n\n"
        f"📑 <b>Документы:</b> {item.get('documents', 'нет') or 'нет'}\n\n"
        f"📍 <b>Забрать у:</b> {get_from['name']}, 📞 {get_from['phone']}, 🏠 {get_from['address']}\n\n"
        f"🏁 <b>Доставить:</b> {deliver_to['name']}, 📞 {deliver_to['phone']}, 🏠 {deliver_to['address']}\n\n"
        f"💰 <b>Оплата:</b> {'Да' if item.get('need_payment') else 'Нет'}\n"
        f"🕒 <b>Доставить до:</b> {item.get('lead_time', '-')}"
    )
# --- Обработчики --------------------------------------------------------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    telegram_id = message.from_user.id
    try:
        resp = await api_client.get(f"/users/by_telegram/{telegram_id}")
        resp.raise_for_status()
        user = resp.json()
        role = user["role"]
        if role == "customer":
            await message.answer(f"Здравствуйте, {user['name']}!", reply_markup=customer_menu)
        elif role == "driver":
            await message.answer(f"Здравствуйте, {user['name']}!", reply_markup=driver_menu)
        elif role == "dispatcher":
            await message.answer(f"Здравствуйте, {user['name']}! Вы вошли как диспетчер.", reply_markup=dispatcher_menu)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            await message.answer("Вы не зарегистрированы.")
        else:
            await message.answer(f"Ошибка: {e}")

# ----------------- Создание заявки (customer) --------------------------

@dp.message(F.text == "🆕 Создать заявку")
async def start_order(message: Message, state: FSMContext):
    await state.set_state(OrderFSM.getting_cargo_name)
    await message.answer("📦 Укажите наименование груза:", reply_markup=cancel_fsm_kb)

@dp.message(StateFilter(OrderFSM), F.text == "❌ Отменить создание")
async def cancel_creation(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Создание заявки отменено.", reply_markup=customer_menu)

@dp.message(OrderFSM.getting_cargo_name)
async def set_name(message: Message, state: FSMContext):
    await state.update_data(cargo_name=message.text)
    await state.set_state(OrderFSM.getting_cargo_weight)
    await message.answer("⚖️ Вес (кг):", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_cargo_weight)
async def set_weight(message: Message, state: FSMContext):
    try:
        weight = int(message.text)
        if weight <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите положительное число.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(cargo_weight=weight)
    await state.set_state(OrderFSM.getting_cargo_count)
    await message.answer("Количество мест:", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_cargo_count)
async def set_count(message: Message, state: FSMContext):
    try:
        count = int(message.text)
        if count <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите положительное число.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(cargo_count=count)
    await state.set_state(OrderFSM.getting_cargo_size)
    await message.answer("Габариты (например 120x80x100):", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_cargo_size)
async def set_size(message: Message, state: FSMContext):
    await state.update_data(cargo_size=message.text)
    await state.set_state(OrderFSM.getting_documents_info)
    await message.answer("Описание документов (если нет — напишите «нет»):", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_documents_info)
async def set_docs(message: Message, state: FSMContext):
    docs = message.text.strip()
    await state.update_data(documents_info=docs)
    await state.set_state(OrderFSM.getting_cargo_contact)
    await message.answer("Контакт отдачи груза (Имя, телефон):", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_cargo_contact)
async def set_cargo_contact(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 2:
        await message.answer("Формат: Имя, телефон", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(cargo_contact={"name": parts[0], "phone": parts[1]})
    await state.set_state(OrderFSM.getting_address_from)
    await message.answer("Адрес отдачи груза:", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_address_from)
async def set_addr_from(message: Message, state: FSMContext):
    await state.update_data(address_from=message.text)
    await state.set_state(OrderFSM.getting_address_to)
    await message.answer("Адрес доставки:", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_address_to)
async def set_addr_to(message: Message, state: FSMContext):
    await state.update_data(address_to=message.text)
    await state.set_state(OrderFSM.getting_recipient_info)
    await message.answer("Получатель (Имя, телефон):", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_recipient_info)
async def set_recipient(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 2:
        await message.answer("Формат: Имя, телефон", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(recipient={"name": parts[0], "phone": parts[1]})
    await state.set_state(OrderFSM.getting_payment_required)
    await message.answer("Требуется оплата? (Да/Нет):", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_payment_required)
async def set_payment(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    if text not in ["да", "нет"]:
        await message.answer("Введите Да или Нет.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(need_payment=(text == "да"))
    await state.set_state(OrderFSM.getting_lead_time)
    await message.answer("Срок доставки (ГГГГ-ММ-ДД ЧЧ:ММ):", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_lead_time)
async def set_lead_time(message: Message, state: FSMContext):
    try:
        dt = datetime.datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("Неверный формат.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(lead_time=dt.isoformat())
    await state.set_state(OrderFSM.getting_extra_info)
    await message.answer("Доп. информация (или «-»):", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_extra_info)
async def set_extra(message: Message, state: FSMContext):
    extra = "" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(extra_info=extra)

    data = await state.get_data()
    summary = (
        f"<b>Проверьте заявку:</b>\n\n"
        f"Груз: {data['cargo_name']} / {data['cargo_weight']} кг / "
        f"{data['cargo_count']} шт / {data['cargo_size']}\n"
        f"Документы: {data['documents_info']}\n"
        f"Забрать: {data['cargo_contact']['name']}, {data['address_from']}\n"
        f"Доставить: {data['address_to']} (получатель {data['recipient']['name']})\n"
        f"Оплата: {'Да' if data['need_payment'] else 'Нет'}\n"
        f"До: {data['lead_time']}\n"
    )
    if extra:
        summary += f"ℹ️ {extra}\n"
    summary += "\nВсе верно?"

    await state.set_state(OrderFSM.confirming_order)
    await message.answer(summary, parse_mode="HTML", reply_markup=confirm_order_kb)

@dp.callback_query(OrderFSM.confirming_order, F.data == "order_cancel")
async def cancel_order_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await call.message.delete()
    await bot.send_message(call.from_user.id, "Создание заявки отменено.", reply_markup=customer_menu)

@dp.callback_query(OrderFSM.confirming_order, F.data == "order_confirm")
async def confirm_order_cb(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    telegram_id = call.from_user.id

    item_payload = {
        "name": data["cargo_name"],
        "weight": data["cargo_weight"],
        "count": data["cargo_count"],
        "size": data["cargo_size"],
        "documents": data["documents_info"],
        "get_from": {
            "name": data["cargo_contact"]["name"],
            "phone": data["cargo_contact"]["phone"],
            "address": data["address_from"],
        },
        "deliver_to": {
            "name": data["recipient"]["name"],
            "phone": data["recipient"]["phone"],
            "address": data["address_to"],
        },
        "need_payment": data["need_payment"],
        "lead_time": data["lead_time"],
        "comments": data["extra_info"],
    }

    try:
        resp = await api_client.post(
            "/orders/",
            params={"customer_telegram_id": telegram_id},
            json=item_payload,
        )
        resp.raise_for_status()
        order = resp.json()
        await call.message.delete()
        await bot.send_message(telegram_id, f"✅ Заявка №{order['id']} создана.", reply_markup=customer_menu)

        # Уведомить всех диспетчеров
        users = (await api_client.get("/users/")).json()
        for u in users:
            if u["role"] == "dispatcher":
                await bot.send_message(u["telegram_id"], f"📬 Новая заявка №{order['id']} ожидает вашего решения.")
    except Exception as e:
        await bot.send_message(telegram_id, f"❌ Ошибка: {e}")

    await state.clear()
    await call.answer()

# ------------------- Просмотр заявок (customer) -------------------------

@dp.message(F.text == "📋 Мои заявки")
async def view_customer_orders(message: Message):
    telegram_id = message.from_user.id
    try:
        orders = (await api_client.get(f"/orders/customer/{telegram_id}")).json()
        if not orders:
            await message.answer("У вас пока нет заявок.")
            return
        for order in orders:
            await message.answer(await format_order_details(order), parse_mode="HTML")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

# ------------------- Dispatcher flows -----------------------------------

@dp.message(F.text == "🧐 Заявки на утверждение")
async def dispatcher_pending(message: Message):
    try:
        response = await api_client.get("/orders/", params={"status": STATUS_CREATED})
        orders = response.json()
    except Exception:
        orders = []
    if not orders:
        await message.answer("Нет заявок на утверждение.")
        return
    for order in orders:
        if not isinstance(order, dict):
            continue
        msg = await format_order_details(order)
        await message.answer(msg, reply_markup=get_dispatcher_approval_kb(order["id"]))

@dp.callback_query(F.data.startswith("dispatch_approve_"))
async def dispatcher_approve(call: CallbackQuery):
    order_id = int(call.data.split("_")[-1])
    try:
        order = (
            await api_client.post(f"/orders/{order_id}/status",
                                  params={"status": STATUS_PLANNING})
        ).json()
        await call.message.edit_text(f"✅ Заявка №{order_id} утверждена.")
        with suppress(TelegramAPIError):
            await bot.send_message(order["item"]["get_from"]["phone"],
                                   f"Ваша заявка №{order_id} утверждена.")
    except Exception as e:
        await call.message.edit_text(f"Ошибка: {e}")
    await call.answer()

@dp.callback_query(F.data.startswith("dispatch_decline_"))
async def dispatcher_decline_init(call: CallbackQuery, state: FSMContext):
    await state.set_state(DispatcherDeclineFSM.getting_reason)
    await state.update_data(order_id=int(call.data.split("_")[-1]), original_message=call.message)
    await call.message.answer("Введите причину отказа:")
    await call.answer()

@dp.message(DispatcherDeclineFSM.getting_reason)
async def dispatcher_decline_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    reason = message.text.strip()
    order_id = data["order_id"]
    original = data["original_message"]
    try:
        await api_client.post(f"/orders/{order_id}/status",
                              params={"status": STATUS_ARCHIVED})
        await original.edit_text(f"❌ Заявка №{order_id} отклонена.\nПричина: {reason}")
    except Exception as e:
        await original.edit_text(f"Ошибка: {e}")
    await state.clear()

# ------------------- Driver flows (новые / активные / история) ----------

@dp.message(F.text == "📝 Новые задачи")
async def driver_new_tasks(message: Message):
    try:
        response = await api_client.get("/orders/", params={"status": STATUS_PLANNING})
        orders = response.json()
    except Exception:
        orders = []
    if not orders:
        await message.answer("Свободных задач нет.")
        return
    for order in orders:
        if not isinstance(order, dict):
            continue
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Взять", callback_data=f"take_{order['id']}")]
        ])
        await message.answer(await format_order_details(order), parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data.startswith("take_"))
async def driver_take(call: CallbackQuery):
    order_id = int(call.data.split("_")[1])
    driver_tid = call.from_user.id
    try:
        order = (
            await api_client.post(f"/orders/{order_id}/assign/{driver_tid}")
        ).json()
        await call.message.edit_text(f"✅ Вы приняли заявку №{order_id}.")
    except Exception as e:
        await call.message.edit_text(f"Ошибка: {e}")
    await call.answer()

# ------------------- Run polling ----------------------------------------

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
