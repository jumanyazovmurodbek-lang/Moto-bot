"""
╔══════════════════════════════════════════════════════════╗
║           MOTO ELON BOT - Python (aiogram 3)             ║
║  Barcha funksiyalar shu yerda: elon, admin, to'lov       ║
╚══════════════════════════════════════════════════════════╝

O'rnatish:
 1. .env faylida BOT_TOKEN, ADMIN_ID, CHANNEL_ID ni to'ldiring
 2. pip install -r requirements.txt
 3. python bot.py
"""

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import contextmanager

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Contact,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InputMediaPhoto,
)

# ══════════════════════════════════════════════
#  SOZLAMALAR
# ══════════════════════════════════════════════
BOT_TOKEN  = os.getenv("BOT_TOKEN", "8676663978:AAGw9S8ZBn9siEy7D-mk_QxNztk_tFg3rzQ")
ADMIN_ID   = int(os.getenv("ADMIN_ID", "5760181294"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@Arzon_sifatli_motoskuterlar")
DB_FILE    = os.getenv("DB_FILE", "moto_bot.db")

TEKN_COUNT = 5       # Bepul tekn soni
TEKN_PRICE = 5000    # To'lovli tekn narxi (so'm)
CURRENCY   = "UZS"
DEFAULT_CARD = os.getenv("DEFAULT_CARD", "5614 6818 1276 7451")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  MA'LUMOTLAR BAZASI
# ══════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY,
                tg_id      INTEGER UNIQUE,
                username   TEXT,
                full_name  TEXT,
                phone      TEXT,
                tekn_used  INTEGER DEFAULT 0,
                tekn_paid  INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ads (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER,
                status         TEXT DEFAULT 'draft',
                photos         TEXT DEFAULT '[]',
                description    TEXT,
                price          TEXT,
                phone          TEXT,
                address        TEXT,
                channel_msg_id INTEGER,
                created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS payments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER,
                amount       INTEGER,
                receipt_file TEXT,
                status       TEXT DEFAULT 'pending',
                admin_msg_id INTEGER,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            INSERT OR IGNORE INTO settings(key,value) VALUES ('card_number','{DEFAULT_CARD}');
            INSERT OR IGNORE INTO settings(key,value) VALUES ('tekn_price','{TEKN_PRICE}');
        """)

def get_setting(key: str) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""

def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))

def get_user(tg_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return dict(row) if row else None

def save_user(from_data: dict) -> dict:
    name = f"{from_data.get('first_name','')} {from_data.get('last_name','')}".strip()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO users(tg_id, username, full_name)
            VALUES(?,?,?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name
        """, (from_data["id"], from_data.get("username", ""), name))
    return get_user(from_data["id"])

def free_tekns_left(user: dict) -> int:
    return max(0, TEKN_COUNT - user["tekn_used"])

def total_tekns(user: dict) -> int:
    return free_tekns_left(user) + user["tekn_paid"]

def use_one_tekn(user_id: int):
    user = get_user(user_id)
    with get_db() as conn:
        if free_tekns_left(user) > 0:
            conn.execute("UPDATE users SET tekn_used=tekn_used+1 WHERE tg_id=?", (user_id,))
        else:
            conn.execute("UPDATE users SET tekn_paid=tekn_paid-1 WHERE tg_id=?", (user_id,))

def get_draft_ad(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM ads WHERE user_id=? AND status='draft' ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None

def create_draft_ad(user_id: int) -> int:
    with get_db() as conn:
        conn.execute("DELETE FROM ads WHERE user_id=? AND status='draft'", (user_id,))
        cur = conn.execute("INSERT INTO ads(user_id, photos) VALUES(?,?)", (user_id, "[]"))
        return cur.lastrowid

def update_ad(ad_id: int, data: dict):
    with get_db() as conn:
        for col, val in data.items():
            conn.execute(f"UPDATE ads SET {col}=? WHERE id=?", (val, ad_id))

def get_ad(ad_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone()
        return dict(row) if row else None

# ══════════════════════════════════════════════
#  FSM STATES
# ══════════════════════════════════════════════
class AdStates(StatesGroup):
    wait_contact    = State()
    upload_photos   = State()
    enter_desc      = State()
    enter_price     = State()
    enter_phone     = State()
    enter_address   = State()
    upload_receipt  = State()
    admin_set_card  = State()
    admin_set_price = State()

# ══════════════════════════════════════════════
#  KLAVIATURALAR
# ══════════════════════════════════════════════
def inline_kb(rows: list) -> InlineKeyboardMarkup:
    keyboard = []
    for row in rows:
        keyboard.append([InlineKeyboardButton(**btn) for btn in row])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def main_menu_kb() -> InlineKeyboardMarkup:
    return inline_kb([
        [{"text": "📢 Moto sotyapman (Elon berish)", "callback_data": "start_sell"}],
        [{"text": "🔍 Moto qidiraman",               "callback_data": "start_buy"}],
        [{"text": "💳 Tekshiruvlar sotib olish",      "callback_data": "buy_tekns"}],
        [{"text": "📋 Mening elon larim",              "callback_data": "my_ads"}],
    ])

def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()

def cancel_kb() -> InlineKeyboardMarkup:
    return inline_kb([[{"text": "❌ Bekor qilish", "callback_data": "cancel_ad"}]])

# ══════════════════════════════════════════════
#  BOT VA ROUTER
# ══════════════════════════════════════════════
bot = Bot(token=BOT_TOKEN)
router = Router()

# ══════════════════════════════════════════════
#  YORDAMCHI FUNKSIYALAR
# ══════════════════════════════════════════════
async def send_main_menu(chat_id: int, user_id: int, name: str):
    user  = get_user(user_id)
    tekns = total_tekns(user) if user else 0
    text  = (
        f"🏍️ <b>Moto Elon Botga xush kelibsiz, {name}!</b>\n\n"
        f"📌 Bu bot orqali moto sotiladi yoki moto qidirishingiz mumkin.\n\n"
        f"🎟 Sizning tekshiruvlar: <b>{tekns} ta</b>\n"
        f"(Har foydalanuvchiga {TEKN_COUNT} ta bepul beriladi)"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=main_menu_kb())

async def send_ad_to_channel(ad: dict):
    photos  = json.loads(ad["photos"])
    caption = (
        "🏍️ <b>MOTO SOTILADI</b>\n\n"
        f"📝 {ad['description']}\n\n"
        f"💰 Narx: <b>{ad['price']}</b>\n"
        f"📞 Tel: <b>{ad['phone']}</b>\n"
        f"📍 Manzil: <b>{ad['address']}</b>\n\n"
        "⚠️ <b>Diqqat:</b> Savdo qilishdan oldin admin bilan bog'laning!\n"
        "#moto #sotiladi"
    )
    if len(photos) > 1:
        media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
        media[0].caption = caption
        media[0].parse_mode = "HTML"
        result = await bot.send_media_group(CHANNEL_ID, media)
        return result[0].message_id
    elif len(photos) == 1:
        msg = await bot.send_photo(CHANNEL_ID, photos[0], caption=caption, parse_mode="HTML")
        return msg.message_id
    else:
        msg = await bot.send_message(CHANNEL_ID, caption, parse_mode="HTML")
        return msg.message_id

async def send_ad_preview(chat_id: int, ad: dict):
    photos  = json.loads(ad["photos"])
    caption = (
        "🏍️ <b>MOTO SOTILADI</b>\n\n"
        f"📝 {ad['description']}\n\n"
        f"💰 Narx: <b>{ad['price']}</b>\n"
        f"📞 Tel: <b>{ad['phone']}</b>\n"
        f"📍 Manzil: <b>{ad['address']}</b>\n\n"
        "👤 Admin bilan bog'laning!"
    )
    if len(photos) > 1:
        media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
        media[0].caption = caption
        media[0].parse_mode = "HTML"
        await bot.send_media_group(chat_id, media)
    elif len(photos) == 1:
        await bot.send_photo(chat_id, photos[0], caption=caption, parse_mode="HTML")

    kb = inline_kb([
        [{"text": "✅ Ha, tasdiqlash", "callback_data": f"confirm_ad:{ad['id']}"}],
        [{"text": "✏️ Tahrirlamoq",    "callback_data": f"edit_ad:{ad['id']}"}],
        [{"text": "❌ Bekor qilish",    "callback_data": "cancel_ad"}],
    ])
    await bot.send_message(chat_id,
        "👆 <b>Eloningiz ko'rinishi shunday bo'ladi!</b>\n\nTasdiqlaysizmi?",
        parse_mode="HTML", reply_markup=kb
    )

# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = save_user({
        "id": message.from_user.id,
        "first_name": message.from_user.first_name or "",
        "last_name": message.from_user.last_name or "",
        "username": message.from_user.username or "",
    })
    await state.clear()
    if not user.get("phone"):
        await message.answer(
            "👋 Xush kelibsiz!\n\n📱 Davom etish uchun telefon raqamingizni yuboring:",
            reply_markup=contact_kb()
        )
        await state.set_state(AdStates.wait_contact)
    else:
        await send_main_menu(message.chat.id, message.from_user.id, user["full_name"])

# ══════════════════════════════════════════════
#  /admin
# ══════════════════════════════════════════════
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await show_admin_panel(message.chat.id)

async def show_admin_panel(chat_id: int):
    with get_db() as conn:
        pending  = conn.execute("SELECT COUNT(*) FROM ads WHERE status='pending'").fetchone()[0]
        pay_pend = conn.execute("SELECT COUNT(*) FROM payments WHERE status='pending'").fetchone()[0]
        total    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    card  = get_setting("card_number")
    price = get_setting("tekn_price") or str(TEKN_PRICE)
    text = (
        "⚙️ <b>ADMIN PANEL</b>\n\n"
        f"📊 Statistika:\n"
        f"  👥 Jami foydalanuvchilar: <b>{total}</b>\n"
        f"  🕐 Kutilayotgan elon: <b>{pending}</b>\n"
        f"  💳 Kutilayotgan to'lov: <b>{pay_pend}</b>\n\n"
        f"💳 Joriy karta: <code>{card}</code>\n"
        f"💰 Tekn narxi: <b>{price} {CURRENCY}</b>"
    )
    kb = inline_kb([
        [{"text": "💳 Kartani o'zgartirish", "callback_data": "admin_set_card"}],
        [{"text": "💰 Narxni o'zgartirish",  "callback_data": "admin_set_price"}],
    ])
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)

# ══════════════════════════════════════════════
#  KONTAKT QABUL QILISH
# ══════════════════════════════════════════════
@router.message(F.contact)
async def handle_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    with get_db() as conn:
        conn.execute("UPDATE users SET phone=? WHERE tg_id=?", (phone, message.from_user.id))

    current = await state.get_state()
    if current == AdStates.enter_phone.state:
        data  = await state.get_data()
        ad_id = data.get("ad_id")
        update_ad(ad_id, {"phone": phone})
        await message.answer("✅ Raqam saqlandi.", reply_markup=remove_kb())
        await state.set_state(AdStates.enter_address)
        await message.answer(
            "📍 <b>Manzilingizni kiriting</b>\n\nMisol: Toshkent, Chilonzor tumani",
            parse_mode="HTML", reply_markup=cancel_kb()
        )
    else:
        await state.clear()
        await message.answer("✅ Rahmat! Raqamingiz saqlandi.", reply_markup=remove_kb())
        user = get_user(message.from_user.id)
        await send_main_menu(message.chat.id, message.from_user.id, user["full_name"])

# ══════════════════════════════════════════════
#  RASM YUKLASH
# ══════════════════════════════════════════════
@router.message(AdStates.upload_photos, F.photo)
async def handle_photo_upload(message: Message, state: FSMContext):
    data  = await state.get_data()
    ad_id = data.get("ad_id")
    ad    = get_ad(ad_id)
    if not ad:
        return

    file_id = message.photo[-1].file_id
    photos  = json.loads(ad["photos"])
    photos.append(file_id)
    update_ad(ad_id, {"photos": json.dumps(photos)})
    count = len(photos)

    if count < 4:
        await message.answer(
            f"✅ {count} ta rasm qabul qilindi.\n📸 Yana {4 - count} ta rasm yuboring.",
            reply_markup=cancel_kb()
        )
    else:
        extra = "\n➕ Yana rasm qo'shishingiz mumkin yoki davom eting." if count > 4 else ""
        kb = inline_kb([
            [{"text": "✅ Davom etish", "callback_data": f"photos_done:{ad_id}"}],
            [{"text": "❌ Bekor qilish", "callback_data": "cancel_ad"}],
        ])
        await message.answer(
            f"✅ <b>{count} ta rasm qabul qilindi!</b>{extra}\n\nDavom etish uchun tugmani bosing.",
            parse_mode="HTML", reply_markup=kb
        )

# ══════════════════════════════════════════════
#  TAVSIF, NARX, TELEFON, MANZIL
# ══════════════════════════════════════════════
@router.message(AdStates.enter_desc)
async def handle_desc(message: Message, state: FSMContext):
    data  = await state.get_data()
    ad_id = data.get("ad_id")
    update_ad(ad_id, {"description": message.text})
    await state.set_state(AdStates.enter_price)
    await message.answer(
        "💰 <b>Narxini kiriting</b>\n\nMisol: 15 000 000 so'm yoki kelishiladi",
        parse_mode="HTML", reply_markup=cancel_kb()
    )

@router.message(AdStates.enter_price)
async def handle_price(message: Message, state: FSMContext):
    data  = await state.get_data()
    ad_id = data.get("ad_id")
    update_ad(ad_id, {"price": message.text})
    user = get_user(message.from_user.id)
    await state.set_state(AdStates.enter_phone)

    if user.get("phone"):
        kb = inline_kb([
            [{"text": f"📱 {user['phone']} ishlatish", "callback_data": f"use_saved_phone:{ad_id}"}],
            [{"text": "❌ Bekor qilish", "callback_data": "cancel_ad"}],
        ])
        await message.answer(
            "📞 <b>Telefon raqamingizni kiriting</b>\n\nYoki saqlangan raqamni ishlating:",
            parse_mode="HTML", reply_markup=kb
        )
    else:
        await message.answer(
            "📞 <b>Telefon raqamingizni kiriting</b>\n\nMisol: +998901234567",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📲 Raqamni yuborish", request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )

@router.message(AdStates.enter_phone)
async def handle_phone_text(message: Message, state: FSMContext):
    data  = await state.get_data()
    ad_id = data.get("ad_id")
    phone = message.text
    with get_db() as conn:
        conn.execute("UPDATE users SET phone=? WHERE tg_id=?", (phone, message.from_user.id))
    update_ad(ad_id, {"phone": phone})
    await state.set_state(AdStates.enter_address)
    await message.answer(
        "📍 <b>Manzilingizni kiriting</b>\n\nMisol: Toshkent, Chilonzor tumani",
        parse_mode="HTML", reply_markup=remove_kb()
    )

@router.message(AdStates.enter_address)
async def handle_address(message: Message, state: FSMContext):
    data  = await state.get_data()
    ad_id = data.get("ad_id")
    update_ad(ad_id, {"address": message.text})
    await state.clear()
    ad = get_ad(ad_id)
    await send_ad_preview(message.chat.id, ad)

# ══════════════════════════════════════════════
#  CHEK YUKLASH
# ══════════════════════════════════════════════
@router.message(AdStates.upload_receipt, F.photo)
async def handle_receipt(message: Message, state: FSMContext):
    user    = get_user(message.from_user.id)
    file_id = message.photo[-1].file_id
    price   = get_setting("tekn_price") or str(TEKN_PRICE)

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO payments(user_id,amount,receipt_file) VALUES(?,?,?)",
            (message.from_user.id, price, file_id)
        )
        pay_id = cur.lastrowid

    await state.clear()
    await message.answer(
        "✅ <b>Chekingiz qabul qilindi!</b>\n\n"
        f"⏳ Admin tekshirib, {TEKN_COUNT} ta tekn hisobingizga o'tkazadi.\n"
        "Odatda 1-2 soat ichida amalga oshiriladi.",
        parse_mode="HTML",
        reply_markup=inline_kb([[{"text": "🏠 Bosh menyu", "callback_data": "main_menu"}]])
    )

    # Adminga yuborish
    caption = (
        "💳 <b>YANGI TO'LOV CHEKI</b>\n\n"
        f"👤 {user['full_name']} (@{user.get('username') or 'noname'})\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"💰 Summa: {int(price):,} {CURRENCY}\n"
        f"📦 {TEKN_COUNT} ta tekn"
    )
    result = await bot.send_photo(ADMIN_ID, file_id, caption=caption, parse_mode="HTML")
    admin_msg_id = result.message_id

    with get_db() as conn:
        conn.execute("UPDATE payments SET admin_msg_id=? WHERE id=?", (admin_msg_id, pay_id))

    kb = inline_kb([[
        {"text": "✅ Tasdiqlash", "callback_data": f"admin_pay_approve:{pay_id}"},
        {"text": "❌ Rad etish",  "callback_data": f"admin_pay_reject:{pay_id}"},
    ]])
    await bot.send_message(ADMIN_ID, f"👆 To'lov #{pay_id} ni tasdiqlaysizmi?",
                           reply_markup=kb)

# ══════════════════════════════════════════════
#  ADMIN: KARTA VA NARX O'ZGARTIRISH
# ══════════════════════════════════════════════
@router.message(AdStates.admin_set_card)
async def handle_admin_card(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    set_setting("card_number", message.text)
    await state.clear()
    await message.answer(f"✅ Karta raqami yangilandi: <code>{message.text}</code>",
                         parse_mode="HTML")
    await show_admin_panel(message.chat.id)

@router.message(AdStates.admin_set_price)
async def handle_admin_price(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    price = "".join(filter(str.isdigit, message.text))
    set_setting("tekn_price", price)
    await state.clear()
    await message.answer(f"✅ Narx yangilandi: <b>{price} {CURRENCY}</b>", parse_mode="HTML")
    await show_admin_panel(message.chat.id)

# ══════════════════════════════════════════════
#  CALLBACK QUERY
# ══════════════════════════════════════════════
@router.callback_query()
async def handle_callback(callback: CallbackQuery, state: FSMContext):
    data     = callback.data
    user_id  = callback.from_user.id
    chat_id  = callback.message.chat.id
    msg_id   = callback.message.message_id
    is_admin = (user_id == ADMIN_ID)

    user = get_user(user_id)
    if not user:
        user = save_user({
            "id": user_id,
            "first_name": callback.from_user.first_name or "",
            "last_name": callback.from_user.last_name or "",
            "username": callback.from_user.username or "",
        })

    await callback.answer()

    # ── Bosh menyu
    if data == "main_menu":
        await state.clear()
        await send_main_menu(chat_id, user_id, user["full_name"])

    # ── Elon berish
    elif data == "start_sell":
        if total_tekns(user) <= 0:
            await callback.message.answer(
                "❌ <b>Sizda bepul tekshiruv qolmadi!</b>\n\n"
                f"Elon berish uchun tekn kerak.\n"
                f"Narxi: {int(get_setting('tekn_price') or TEKN_PRICE):,} {CURRENCY}\n\n"
                "To'lov qilish uchun quyidagi tugmani bosing:",
                parse_mode="HTML",
                reply_markup=inline_kb([[{"text": "💳 To'lov qilish", "callback_data": "buy_tekns"}]])
            )
            return
        ad_id = create_draft_ad(user_id)
        await state.set_state(AdStates.upload_photos)
        await state.update_data(ad_id=ad_id)
        await callback.message.answer(
            "📸 <b>Moto rasmlarini yuboring</b>\n\n"
            "Kamida <b>4 ta rasm</b> yuborish kerak.\n"
            "Rasmlarni birin-ketin yuboring.\n\n"
            "📌 Hozircha 0 ta rasm qabul qilindi.",
            parse_mode="HTML", reply_markup=cancel_kb()
        )

    # ── Moto qidirish
    elif data in ("start_buy", "start_buy_ad"):
        await callback.message.answer(
            "🔍 <b>Moto qidirish</b>\n\n"
            f"Moto qidirish uchun kanalimizga o'ting:\n{CHANNEL_ID}\n\n"
            "Agar maxsus moto qidirsangiz, elon bering:",
            parse_mode="HTML",
            reply_markup=inline_kb([
                [{"text": "📢 Moto qidiraman (Elon berish)", "callback_data": "start_buy_ad"}],
                [{"text": "🏠 Bosh menyu", "callback_data": "main_menu"}],
            ])
        )

    # ── Rasmlar tayyor
    elif data.startswith("photos_done:"):
        ad_id = int(data.split(":")[1])
        ad    = get_ad(ad_id)
        if ad and len(json.loads(ad["photos"])) >= 4:
            await state.set_state(AdStates.enter_desc)
            await state.update_data(ad_id=ad_id)
            await callback.message.answer(
                "📝 <b>Moto haqida qisqacha ma'lumot yozing</b>\n\n"
                "Misol: Yamaha R15, 2021 yil, qora rang, 15.000 km yurgan, hujjatlari to'liq",
                parse_mode="HTML", reply_markup=cancel_kb()
            )
        else:
            await callback.message.answer("⚠️ Kamida 4 ta rasm yuborish kerak!")

    # ── Saqlangan telefon
    elif data.startswith("use_saved_phone:"):
        ad_id = int(data.split(":")[1])
        update_ad(ad_id, {"phone": user["phone"]})
        await state.set_state(AdStates.enter_address)
        await state.update_data(ad_id=ad_id)
        await callback.message.answer(
            "📍 <b>Manzilingizni kiriting</b>\n\nMisol: Toshkent, Chilonzor tumani",
            parse_mode="HTML", reply_markup=remove_kb()
        )

    # ── Elonni tasdiqlash
    elif data.startswith("confirm_ad:"):
        ad_id = int(data.split(":")[1])
        ad    = get_ad(ad_id)
        if not ad:
            return
        use_one_tekn(user_id)
        update_ad(ad_id, {"status": "pending"})
        user_after = get_user(user_id)
        tekns_left = total_tekns(user_after)

        await callback.message.answer(
            "✅ <b>Eloningiz adminга yuborildi!</b>\n\n"
            "⏳ Admin ko'rib chiqib tasdiqlaydi.\n"
            "Tasdiqlanganidan keyin kanal/guruhga joylashtiriladi.",
            parse_mode="HTML",
            reply_markup=inline_kb([[{"text": "🏠 Bosh menyu", "callback_data": "main_menu"}]])
        )

        photos = json.loads(ad["photos"])
        admin_caption = (
            "🆕 <b>YANGI ELON (Tasdiqlash kerak)</b>\n\n"
            f"👤 {user['full_name']} (@{user.get('username') or 'noname'})\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            f"📝 {ad['description']}\n\n"
            f"💰 Narx: <b>{ad['price']}</b>\n"
            f"📞 Tel: <b>{ad['phone']}</b>\n"
            f"📍 Manzil: <b>{ad['address']}</b>\n\n"
            f"🎟 Foydalanuvchi tekn qoldi: {tekns_left}"
        )
        if len(photos) > 1:
            media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
            media[0].caption = admin_caption
            media[0].parse_mode = "HTML"
            await bot.send_media_group(ADMIN_ID, media)
        elif len(photos) == 1:
            await bot.send_photo(ADMIN_ID, photos[0], caption=admin_caption, parse_mode="HTML")
        else:
            await bot.send_message(ADMIN_ID, admin_caption, parse_mode="HTML")

        kb = inline_kb([[
            {"text": "✅ Tasdiqlash", "callback_data": f"admin_approve:{ad_id}"},
            {"text": "❌ Rad etish",  "callback_data": f"admin_reject:{ad_id}"},
        ]])
        await bot.send_message(ADMIN_ID, f"👆 Elon #{ad_id} ni tasdiqlaysizmi?", reply_markup=kb)

    # ── Elon tahrirlash
    elif data.startswith("edit_ad:"):
        ad_id = int(data.split(":")[1])
        kb = inline_kb([
            [{"text": "📸 Rasmlar",    "callback_data": f"edit_photos:{ad_id}"}],
            [{"text": "📝 Tavsif",      "callback_data": f"edit_desc:{ad_id}"}],
            [{"text": "💰 Narx",        "callback_data": f"edit_price:{ad_id}"}],
            [{"text": "📞 Telefon",     "callback_data": f"edit_phone:{ad_id}"}],
            [{"text": "📍 Manzil",      "callback_data": f"edit_address:{ad_id}"}],
            [{"text": "❌ Bekor qilish","callback_data": "cancel_ad"}],
        ])
        await callback.message.answer("✏️ Nimani tahrirlamoqchisiz?", reply_markup=kb)

    elif data.startswith("edit_photos:"):
        ad_id = int(data.split(":")[1])
        update_ad(ad_id, {"photos": "[]"})
        await state.set_state(AdStates.upload_photos)
        await state.update_data(ad_id=ad_id)
        await callback.message.answer("📸 Yangi rasmlarni yuboring (kamida 4 ta):",
                                      reply_markup=cancel_kb())

    elif data.startswith("edit_desc:"):
        ad_id = int(data.split(":")[1])
        await state.set_state(AdStates.enter_desc)
        await state.update_data(ad_id=ad_id)
        await callback.message.answer(
            "📝 <b>Yangi tavsif yozing:</b>", parse_mode="HTML", reply_markup=cancel_kb()
        )

    elif data.startswith("edit_price:"):
        ad_id = int(data.split(":")[1])
        await state.set_state(AdStates.enter_price)
        await state.update_data(ad_id=ad_id)
        await callback.message.answer(
            "💰 <b>Yangi narxni kiriting:</b>", parse_mode="HTML", reply_markup=cancel_kb()
        )

    elif data.startswith("edit_phone:"):
        ad_id = int(data.split(":")[1])
        await state.set_state(AdStates.enter_phone)
        await state.update_data(ad_id=ad_id)
        await callback.message.answer(
            "📞 <b>Yangi telefon raqamini kiriting:</b>", parse_mode="HTML",
            reply_markup=cancel_kb()
        )

    elif data.startswith("edit_address:"):
        ad_id = int(data.split(":")[1])
        await state.set_state(AdStates.enter_address)
        await state.update_data(ad_id=ad_id)
        await callback.message.answer(
            "📍 <b>Yangi manzilni kiriting:</b>", parse_mode="HTML", reply_markup=cancel_kb()
        )

    # ── Elon bekor
    elif data == "cancel_ad":
        with get_db() as conn:
            conn.execute("DELETE FROM ads WHERE user_id=? AND status='draft'", (user_id,))
        await state.clear()
        await callback.message.answer(
            "❌ Elon bekor qilindi.",
            reply_markup=inline_kb([[{"text": "🏠 Bosh menyu", "callback_data": "main_menu"}]])
        )

    # ── Tekn sotib olish
    elif data == "buy_tekns":
        card  = get_setting("card_number")
        price = get_setting("tekn_price") or str(TEKN_PRICE)
        await callback.message.answer(
            f"💳 <b>Tekn sotib olish</b>\n\n"
            f"📦 Paket: {TEKN_COUNT} ta tekshiruv\n"
            f"💰 Narx: <b>{int(price):,} {CURRENCY}</b>\n\n"
            f"🏦 To'lov karta:\n<code>{card}</code>\n\n"
            "📤 To'lovni amalga oshirgach, <b>chek rasmini</b> yuboring.",
            parse_mode="HTML",
            reply_markup=inline_kb([
                [{"text": "📸 Chek yuborish", "callback_data": "send_receipt"}],
                [{"text": "🔙 Orqaga",        "callback_data": "main_menu"}],
            ])
        )

    elif data == "send_receipt":
        await state.set_state(AdStates.upload_receipt)
        await callback.message.answer(
            "📸 <b>To'lov chekini yuboring</b>\n\nBank ilovasidagi screenshot yoki rasmni yuboring.",
            parse_mode="HTML",
            reply_markup=inline_kb([[{"text": "❌ Bekor qilish", "callback_data": "main_menu"}]])
        )

    # ── Mening elon larim
    elif data == "my_ads":
        with get_db() as conn:
            ads = conn.execute(
                "SELECT * FROM ads WHERE user_id=? AND status != 'draft' ORDER BY id DESC LIMIT 10",
                (user_id,)
            ).fetchall()
        if not ads:
            await callback.message.answer(
                "📋 <b>Sizda hali elon yo'q.</b>",
                parse_mode="HTML",
                reply_markup=inline_kb([
                    [{"text": "📢 Elon berish", "callback_data": "start_sell"}],
                    [{"text": "🏠 Bosh menyu",  "callback_data": "main_menu"}],
                ])
            )
            return
        emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌", "published": "📢"}
        text  = "📋 <b>Sizning elon laringiz:</b>\n\n"
        for ad in ads:
            ad = dict(ad)
            e  = emoji.get(ad["status"], "❓")
            text += f"{e} #{ad['id']} — {(ad['description'] or '')[:40]}...\n"
            text += f"   💰 {ad['price']} | 📅 {ad['created_at'][:10]}\n\n"
        await callback.message.answer(
            text, parse_mode="HTML",
            reply_markup=inline_kb([[{"text": "🏠 Bosh menyu", "callback_data": "main_menu"}]])
        )

    # ── ADMIN: Elon tasdiqlash
    elif is_admin and data.startswith("admin_approve:"):
        ad_id = int(data.split(":")[1])
        ad    = get_ad(ad_id)
        if not ad or ad["status"] != "pending":
            return
        msg_id_ch = await send_ad_to_channel(ad)
        update_ad(ad_id, {"status": "published", "channel_msg_id": msg_id_ch})
        await bot.send_message(
            int(ad["user_id"]),
            f"🎉 <b>Eloningiz tasdiqlandi!</b>\n\n✅ Eloningiz kanalga joylashtirildi.\nKo'rish: {CHANNEL_ID}",
            parse_mode="HTML",
            reply_markup=inline_kb([[{"text": "🏠 Bosh menyu", "callback_data": "main_menu"}]])
        )
        await bot.edit_message_text(
            f"✅ Elon #{ad_id} tasdiqlandi va kanalga joylashtirildi.",
            chat_id=chat_id, message_id=msg_id
        )

    elif is_admin and data.startswith("admin_reject:"):
        ad_id = int(data.split(":")[1])
        ad    = get_ad(ad_id)
        if not ad or ad["status"] != "pending":
            return
        update_ad(ad_id, {"status": "rejected"})
        with get_db() as conn:
            conn.execute("UPDATE users SET tekn_paid=tekn_paid+1 WHERE tg_id=?", (ad["user_id"],))
        await bot.send_message(
            int(ad["user_id"]),
            "❌ <b>Eloningiz rad etildi.</b>\n\nSabab: Elon qoidalarga mos kelmadi.\nTekningiz qaytarildi.",
            parse_mode="HTML",
            reply_markup=inline_kb([[{"text": "🏠 Bosh menyu", "callback_data": "main_menu"}]])
        )
        await bot.edit_message_text(
            f"❌ Elon #{ad_id} rad etildi. Tekn qaytarildi.",
            chat_id=chat_id, message_id=msg_id
        )

    # ── ADMIN: To'lov tasdiqlash
    elif is_admin and data.startswith("admin_pay_approve:"):
        pay_id = int(data.split(":")[1])
        with get_db() as conn:
            pay = conn.execute("SELECT * FROM payments WHERE id=?", (pay_id,)).fetchone()
            if not pay or pay["status"] != "pending":
                return
            conn.execute("UPDATE payments SET status='approved' WHERE id=?", (pay_id,))
            conn.execute(f"UPDATE users SET tekn_paid=tekn_paid+{TEKN_COUNT} WHERE tg_id=?",
                         (pay["user_id"],))
        await bot.send_message(
            int(pay["user_id"]),
            f"🎉 <b>To'lovingiz tasdiqlandi!</b>\n\n✅ {TEKN_COUNT} ta tekn hisobingizga qo'shildi!\nEndi elon bera olasiz.",
            parse_mode="HTML",
            reply_markup=inline_kb([[{"text": "📢 Elon berish", "callback_data": "start_sell"}]])
        )
        await bot.edit_message_text(
            f"✅ To'lov #{pay_id} tasdiqlandi. {TEKN_COUNT} ta tekn qo'shildi.",
            chat_id=chat_id, message_id=msg_id
        )

    elif is_admin and data.startswith("admin_pay_reject:"):
        pay_id = int(data.split(":")[1])
        with get_db() as conn:
            pay = conn.execute("SELECT * FROM payments WHERE id=?", (pay_id,)).fetchone()
            if not pay or pay["status"] != "pending":
                return
            conn.execute("UPDATE payments SET status='rejected' WHERE id=?", (pay_id,))
        await bot.send_message(
            int(pay["user_id"]),
            "❌ <b>To'lovingiz rad etildi.</b>\n\nChek noto'g'ri yoki summa mos kelmadi.\nQayta urinib ko'ring.",
            parse_mode="HTML",
            reply_markup=inline_kb([[{"text": "🏠 Bosh menyu", "callback_data": "main_menu"}]])
        )
        await bot.edit_message_text(
            f"❌ To'lov #{pay_id} rad etildi.",
            chat_id=chat_id, message_id=msg_id
        )

    # ── ADMIN panel
    elif is_admin and data == "admin_panel":
        await show_admin_panel(chat_id)

    elif is_admin and data == "admin_set_card":
        await state.set_state(AdStates.admin_set_card)
        await callback.message.answer(
            "💳 Yangi karta raqamini kiriting:\nMisol: 8600 0000 0000 0000"
        )

    elif is_admin and data == "admin_set_price":
        await state.set_state(AdStates.admin_set_price)
        await callback.message.answer(
            f"💰 {TEKN_COUNT} ta tekn uchun yangi narxni kiriting (so'mda):\nMisol: 5000"
        )

# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════
async def main():
    init_db()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Bot ishga tushdi...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
