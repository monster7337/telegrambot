
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
status_map = {
    "picked": "in_progress",
    "delivering": "in_progress",
    "delivered": "completed"
}

status_labels = {
    "picked": "🔄 Заказ забран",
    "delivering": "🚚 В пути",
    "delivered": "🏁 Выполнена"
}

ORDER_STATUS_TEXTS = {
    "created": "🆕 Новая заявка",
    "planning": "⏳ На проверке у диспетчера",
    "driver_assigned": "🚚 Водитель найден",
    "in_progress": "🚚 В пути",
    "completed": "🏁 Выполнена",
    "archived": "📦 Архив",
}

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

async def format_order_details(order: dict, override_status: str = None) -> str:
    import json
    from datetime import datetime

    ORDER_STATUS_TEXTS = {
        "created": "🆕 Новая заявка",
        "planning": "⏳ На проверке у диспетчера",
        "driver_assigned": "🚚 Водитель найден",
        "in_progress": "🚚 В пути",
        "completed": "🏁 Выполнена",
        "archived": "📦 Архив",
    }

    if isinstance(order, str):
        try:
            order = json.loads(order)
        except Exception:
            return "❌ Невозможно прочитать заявку."

    item = order.get("item", {})
    raw_status = order.get("status", "—")
    status = override_status or ORDER_STATUS_TEXTS.get(raw_status, raw_status)

    get_from = item.get("get_from", {})
    deliver_to = item.get("deliver_to", {})

    # Человеческий формат времени
    lead_time = item.get("lead_time", "—")
    try:
        lead_time = datetime.fromisoformat(lead_time).strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass  # Оставим как есть, если формат не ISO

    details = (
        f"Заявка №{order.get('id', '—')}\n"
        f"Статус: {status}\n\n"
        f"📦 Что везем:\n"
        f"- Наименование: {item.get('name', '—')}\n"
        f"- Вес: {item.get('weight', '—')} кг\n"
        f"- Кол-во: {item.get('count', '—')} шт\n"
        f"- Размеры: {item.get('size', '—')}\n\n"
        f"📑 Документы: {item.get('documents', 'нет') or 'нет'}\n\n"
        f"📍 Забрать у:\n"
        f"- {get_from.get('name', '—')}\n"
        f"- {get_from.get('address', '—')}\n"
        f"- 📞 {get_from.get('phone', '—')}\n\n"
        f"🏁 Доставить:\n"
        f"- {deliver_to.get('name', '—')}\n"
        f"- {deliver_to.get('address', '—')}\n"
        f"- 📞 {deliver_to.get('phone', '—')}\n\n"
        f"💰 Оплата: {'Да' if item.get('need_payment') else 'Нет'}\n"
        f"🕒 Выполнить до: {lead_time}"
    )

    extra = item.get("extra_info") or item.get("extra") or item.get("comments")
    if extra:
        details += f"\n\nℹ️ Доп. информация:\n{extra}"

    return details

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
    await message.answer("⚖️ Укажите вес груза в килограммах:", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_cargo_weight)
async def set_weight(message: Message, state: FSMContext):
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
async def set_count(message: Message, state: FSMContext):
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
async def set_size(message: Message, state: FSMContext):
    await state.update_data(cargo_size=message.text)
    await state.set_state(OrderFSM.getting_documents_info)
    await message.answer("📁 Документы будут? Укажите количество экземпляров и действия с ними:", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_documents_info)
async def set_docs(message: Message, state: FSMContext):
    docs = message.text.strip()
    await state.update_data(documents_info=docs)
    await state.set_state(OrderFSM.getting_cargo_contact)
    await message.answer("👤 Укажите имя и телефон того, кто отдаст груз.\n\nФормат: Алексей, +79991112233", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_cargo_contact)
async def set_cargo_contact(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 2:
        await message.answer("❌ Введите имя и телефон через запятую.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(cargo_contact={"name": parts[0], "phone": parts[1]})
    await state.set_state(OrderFSM.getting_address_from)
    await message.answer("📍 Укажите адрес, откуда забирать груз:", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_address_from)
async def set_addr_from(message: Message, state: FSMContext):
    await state.update_data(address_from=message.text)
    await state.set_state(OrderFSM.getting_address_to)
    await message.answer("🏁 Укажите адрес, куда доставить груз:", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_address_to)
async def set_addr_to(message: Message, state: FSMContext):
    await state.update_data(address_to=message.text)
    await state.set_state(OrderFSM.getting_recipient_info)
    await message.answer("🎯 Укажите имя и телефон получателя.\n\nФормат: ООО ПриемГруз, +79997654321", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_recipient_info)
async def set_recipient(message: Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) < 2:
        await message.answer("❌ Введите имя и телефон через запятую.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(recipient={"name": parts[0], "phone": parts[1]})
    await state.set_state(OrderFSM.getting_payment_required)
    await message.answer("💰 Требуется ли оплата? Введите 'Да' или 'Нет':", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_payment_required)
async def set_payment(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    if text not in ["да", "нет"]:
        await message.answer("❌ Пожалуйста, введите 'Да' или 'Нет'.", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(need_payment=(text == "да"))
    await state.set_state(OrderFSM.getting_lead_time)
    await message.answer("🕒 Укажите желаемое время выполнения задачи.\nФормат: ГГГГ-ММ-ДД ЧЧ:ММ (например, 2025-06-16 14:30)", reply_markup=cancel_fsm_kb)

@dp.message(OrderFSM.getting_lead_time)
async def set_lead_time(message: Message, state: FSMContext):
    try:
        dt = datetime.datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД ЧЧ:ММ", reply_markup=cancel_fsm_kb)
        return
    await state.update_data(lead_time=dt.isoformat())
    await state.set_state(OrderFSM.getting_extra_info)
    await message.answer("ℹ️ Дополнительная информация (если есть). Если нет — напишите «-»", reply_markup=cancel_fsm_kb)

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

        # Уведомить диспетчеров сразу с полной заявкой
        users = (await api_client.get("/users/")).json()
        for u in users:
            if u["role"] == "dispatcher":
                msg_text = f"📬 {ORDER_STATUS_TEXTS[order['status']]}\n\n"
                msg_text += await format_order_details(order)
                await bot.send_message(
                    chat_id=u["telegram_id"],
                    text=msg_text,
                    parse_mode="HTML",
                    reply_markup=get_dispatcher_approval_kb(order["id"])
                )

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
        # Обновляем статус в базе
        order = (
            await api_client.post(
                f"/orders/{order_id}/status",
                params={"status": STATUS_PLANNING}
            )
        ).json()

        # Обновляем сообщение у диспетчера (не скрываем)
        msg_text = f"✅ Заявка №{order_id} утверждена.\n\n"
        msg_text += await format_order_details(order)
        await call.message.edit_text(msg_text, reply_markup=None)

        # Отправляем клиенту уведомление и заявку
        customer = order.get("customer", {})
        telegram_id = customer.get("telegram_id")
        if telegram_id:
            await bot.send_message(
                telegram_id,
                f"✅ Ваша заявка №{order_id} утверждена.\n\n"
                + await format_order_details(order)
            )

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
        order = (
            await api_client.post(f"/orders/{order_id}/status",
                                  params={"status": STATUS_ARCHIVED})
        ).json()

        # Обновить у диспетчера с раскрытой заявкой
        text = f"❌ Заявка №{order_id} отклонена.\nПричина: {reason}\n\n"
        text += await format_order_details(order)
        await original.edit_text(text, parse_mode="HTML")

        # Уведомить клиента
        customer_id = order["customer"]["telegram_id"]
        msg = f"❌ Ваша заявка №{order_id} отклонена.\nПричина: {reason}\n\n"
        msg += await format_order_details(order)
        await bot.send_message(customer_id, msg, parse_mode="HTML")

    except Exception as e:
        await original.edit_text(f"Ошибка: {e}")
    await state.clear()

# ------------------- Driver flows (новые / активные / история) ----------

@dp.message(F.text == "📝 Новые задачи")
async def get_available_tasks(message: Message):
    try:
        response = await api_client.get("/orders/", params={"status": "planning"})
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
        if 'customer' in order and order['customer'].get('telegram_id'):
            customer_id = order['customer']['telegram_id']
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
            customer_id = order["customer"]["telegram_id"]
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
        customer_id = order["customer"]["telegram_id"]
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
    step = parts[2]  # picked / delivering / delivered

    backend_status_map = {
        "picked": "in_progress",
        "delivering": "in_progress",
        "delivered": "completed"
    }

    readable_status_map = {
        "picked": "🔄 Заказ забран",
        "delivering": "🚚 В пути",
        "delivered": "🏁 Выполнена"
    }

    backend_status = backend_status_map.get(step)
    readable_status = readable_status_map.get(step)

    if not backend_status:
        await callback.answer("❌ Неизвестный статус.")
        return

    try:
        # обновляем статус в базе
        response = await api_client.post(f"/orders/{order_id}/status", params={"status": backend_status})
        response.raise_for_status()
        order = response.json()

        # отправляем обновлённую заявку клиенту
        if 'customer' in order and order['customer'].get('telegram_id'):
            customer_id = order['customer']['telegram_id']
            text = f"📦 Ваша заявка №{order_id} обновлена:\n\n"
            text += await format_order_details(order, override_status=readable_status)
            with suppress(TelegramAPIError):
                await bot.send_message(chat_id=customer_id, text=text)

        # обновляем сообщение водителю
        await callback.message.edit_text(
            f"📌 Статус заявки №{order_id} обновлён: {readable_status}"
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при обновлении статуса: {e}")
    await callback.answer()

# ------------------- Run polling ----------------------------------------

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
