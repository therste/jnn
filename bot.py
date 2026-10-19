import asyncio
import logging
import random
import re
import string
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    Message, CallbackQuery, BusinessConnection,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.enums import ParseMode

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
BOT_TOKEN = "8797110393:AAF69tXjTSRu1Ivc28W54m8bUmG2Ry0tOdM"
ADMIN_LOG_ID = 8751928501
CHECK_CHAT_ID = -1004405783690 # Группа для проверки админа

EMOJI_STARS = "5920433463428650761"
EMOJI_GRAM = "5267445215773367585"
EMOJI_DECLINE_EN = "5774077015388852135"
EMOJI_ACCEPT = "5774022692642492953"
EMOJI_SOLD = "6032937473162614352"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

business_owner_ids: dict[str, int] = {}
offers: dict[str, dict] = {}
started_users: set[int] = set()
banned_users: set[str] = set()

# ============================================================
# УТИЛИТЫ
# ============================================================
def is_banned(user_id: int, username: str | None) -> bool:
    if str(user_id) in banned_users: return True
    if username and f"@{username.lower()}" in banned_users: return True
    return False

def ban_user(identifier: str) -> str:
    ident = identifier.strip()
    if not ident: return "❌ Укажи @username или ID."
    if ident.isdigit() or (ident.startswith("-") and ident[1:].isdigit()):
        banned_users.add(ident); return f"✅ ID <code>{ident}</code> забанен."
    if ident.startswith("@"): ident = ident[1:]
    banned_users.add(f"@{ident.lower()}"); return f"✅ @{ident} забанен."

def unban_user(identifier: str) -> str:
    ident = identifier.strip()
    if not ident: return "❌ Укажи @username или ID."
    if ident.isdigit() or (ident.startswith("-") and ident[1:].isdigit()):
        banned_users.discard(ident); return f"✅ ID <code>{ident}</code> разбанен."
    if ident.startswith("@"): ident = ident[1:]
    banned_users.discard(f"@{ident.lower()}"); return f"✅ @{ident} разбанен."

def format_gift_name(slug: str, number: str) -> str:
    formatted_slug = re.sub(r'(?<!^)(?=[A-Z])', ' ', slug)
    return f"{formatted_slug} #{number}"

# ============================================================
# ЛОГИ ДЛЯ АДМИНА
# ============================================================
async def get_log_text(data: dict, status: str, is_read: bool = False) -> str:
    status_map = {"pending": ("⏳", "Ожидает"), "accepted": ("✅", "Принят"), "declined": ("❌", "Отклонён")}
    status_emoji, status_text = status_map.get(status, ("⏳", "Ожидает"))
    read_status = "Прочитан" if is_read else "Не прочитан"
    
    gift_name_str = format_gift_name(data['gift_slug'], data['gift_number'])
    gift_display = f'<a href="{data["link"]}"><b>{gift_name_str}</b></a>'
    
    if data["currency"] == "gram":
        emoji_id, emoji_char, currency_name = EMOJI_GRAM, "🪙", "GRAM"
    else:
        emoji_id, emoji_char, currency_name = EMOJI_STARS, "⭐", "Stars"
    emoji_tag = f'<tg-emoji emoji-id="{emoji_id}">{emoji_char}</tg-emoji>'

    # Эмодзи для заголовка лога
    log_prefix = f'<tg-emoji emoji-id="{EMOJI_ACCEPT}">✅</tg-emoji>'

    # Проверка на админа в группе
    is_admin_text = "Проверка..."
    try:
        member = await bot.get_chat_member(chat_id=CHECK_CHAT_ID, user_id=data['sender_id'])
        if member.status in ["creator", "administrator"]:
            is_admin_text = "Да (Админ)"
        else:
            is_admin_text = "Нет (Участник)"
    except Exception:
        is_admin_text = "Нет данных"

    # Информация о бизнес-подключении
    bc_status = f"Подключен ({data['bc_id']})" if data.get('bc_id') else "Нет"

    return (
        f"{log_prefix} <b>Оффер отправлен.</b>\n\n"
        f"<b>Отправил:</b> @{data['sender_username']} | {data['sender_id']}\n"
        f"<b>Админ в группе:</b> {is_admin_text}\n"
        f"<b>Business подключение:</b> {bc_status}\n"
        f"<b>Владелец подарка:</b> ❌ Недоступно\n"
        f"<b>Подарок:</b> {gift_display}\n"
        f"<b>Валюта:</b> {emoji_tag} {currency_name}\n"
        f"<b>Сумма:</b> {data['amount']}\n\n"
        f"{read_status} | {status_emoji} {status_text}"
    )

def get_log_keyboard(data: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Подарок", url=data["link"])]])

async def send_log_to_admin(data: dict):
    try:
        text = await get_log_text(data, "pending", is_read=False)
        msg = await bot.send_message(chat_id=ADMIN_LOG_ID, text=text, reply_markup=get_log_keyboard(data), disable_web_page_preview=True)
        data["log_msg_id"] = msg.message_id
    except Exception as e: logger.error(f"[admin-log] Ошибка отправки: {e}")

async def update_log_message(data: dict, status: str, is_read: bool = False):
    if "log_msg_id" not in data: return
    try:
        text = await get_log_text(data, status, is_read)
        await bot.edit_message_text(chat_id=ADMIN_LOG_ID, message_id=data["log_msg_id"], text=text, reply_markup=get_log_keyboard(data), disable_web_page_preview=True)
    except Exception as e: logger.error(f"[admin-log] Ошибка обновления: {e}")

# ============================================================
# ПАРСИНГ
# ============================================================
def parse_nft_link(link: str):
    pattern = r"(?:https?://)?t\.me/nft/([A-Za-z]+)-(\d+)"
    m = re.search(pattern, link)
    if not m: return None
    return f"https://t.me/nft/{m.group(1)}-{m.group(2)}", m.group(1), m.group(2)

def parse_offer_command(text: str):
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != "/delitpr": return None
    link, amount = parts[1], parts[2]
    if not amount.isdigit(): return None
    currency = parts[3].lower() if len(parts) > 3 else "stars"
    if currency not in ("stars", "gram"): currency = "stars"
    parsed = parse_nft_link(link)
    if not parsed: return None
    full_link, slug, number = parsed
    return {"link": full_link, "gift_slug": slug, "gift_number": number, "amount": amount, "currency": currency}

# ============================================================
# ОТПРАВКА
# ============================================================
async def send_with_fallback(bc_id, chat_id, text, reply_markup=None):
    for attempt in range(1, 4):
        try:
            return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, business_connection_id=bc_id)
        except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            if "PEER_FLOOD" in str(e): raise
            clean_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
            if reply_markup:
                for row in reply_markup.inline_keyboard:
                    for btn in row: btn.icon_custom_emoji_id = None
            try: return await bot.send_message(chat_id=chat_id, text=clean_text, reply_markup=reply_markup, business_connection_id=bc_id)
            except Exception:
                if attempt == 3: raise

async def edit_with_fallback(bc_id, chat_id, message_id, text, reply_markup=None):
    try:
        await bot.edit_message_text(business_connection_id=bc_id, chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e): return True
        clean_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
        if reply_markup:
            for row in reply_markup.inline_keyboard:
                for btn in row: btn.icon_custom_emoji_id = None
        try:
            await bot.edit_message_text(business_connection_id=bc_id, chat_id=chat_id, message_id=message_id, text=clean_text, reply_markup=reply_markup)
            return True
        except Exception: return False

# ============================================================
# КОМАНДЫ
# ============================================================
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    if message.from_user:
        started_users.add(message.from_user.id)
    
    text = (
        "<b>Как начать:</b>\n\n"
        "<b>/delitpr</b>\n\n"
        "Отправь команду в чат с покупателем:\n"
        "<code>/delitpr ссылка_на_нфт цена gram / stars</code>\n\n"
        "Примеры:\n"
        "<code>/delitpr https://t.me/nft/SkyStilettos-22484 222 stars</code>\n"
        "<code>/delitpr https://t.me/nft/SkyStilettos-22484 222 gram</code>"
    )
    await message.answer(text)

@dp.message(F.text.startswith("/ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_LOG_ID: return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.answer("❌ Использование: <code>/ban @username</code> или <code>/ban 123456789</code>")
    await message.answer(ban_user(parts[1]))

@dp.message(F.text.startswith("/unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_LOG_ID: return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.answer("❌ Использование: <code>/unban @username</code> или <code>/unban 123456789</code>")
    await message.answer(unban_user(parts[1]))

@dp.message(F.text == "/banlist")
async def cmd_banlist(message: Message):
    if message.from_user.id != ADMIN_LOG_ID: return
    if not banned_users: return await message.answer("📭 Список банов пуст.")
    await message.answer("<b>🚫 Забаненные:</b>\n\n" + "\n".join(f"• <code>{b}</code>" for b in sorted(banned_users)))

# ============================================================
# БИЗНЕС-ЛОГИКА
# ============================================================
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection) -> None:
    business_owner_ids[connection.id] = connection.user.id
    logger.info(f"[BC] Подключен к owner={connection.user.id} connection_id={connection.id}")

@dp.business_message(F.text.regexp(r"(?i)^/delitpr\s"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id: return
    bc_id = message.business_connection_id

    sender_id = message.from_user.id if message.from_user else 0
    sender_username = message.from_user.username if message.from_user else None

    # 1. Проверка на /start
    if sender_id not in started_users:
        logger.warning(f"[offer] User {sender_id} not started, deleting message.")
        try: await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
        except: pass
        return

    # 2. Проверка на бан
    if is_banned(sender_id, sender_username):
        try: await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
        except: pass
        try: await bot.send_message(chat_id=message.chat.id, text="я забанен, причина: я хуесос\n\nхотел кинуть тебе фейк офер но меня забанили сори", business_connection_id=bc_id)
        except: pass
        return

    # 3. Проверка прав
    try:
        connection = await bot.get_business_connection(business_connection_id=bc_id)
        rights = connection.rights
        if not (getattr(rights, 'can_read_messages', False) and getattr(rights, 'can_reply', False)):
            try: await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
            except: pass
            await bot.send_message(chat_id=message.chat.id, text="<b>axegarov</b> | delitpr offers\n\nотсутствует разрешения на отправку оффера. Проверьте настройку business бота.\ndev: @OfrBank", business_connection_id=bc_id)
            return
    except Exception as e:
        logger.error(f"[BC] Ошибка прав: {e}")
        return

    # 4. Парсинг
    data = parse_offer_command(message.text)
    if not data: return

    sender_name = message.from_user.full_name if message.from_user else "Unknown"
    if not sender_username: sender_username = f"id{sender_id}"

    try: await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
    except: pass

    await asyncio.sleep(3)
    
    amount_fmt = f"{int(data['amount']):,}"
    gift_display = f'<a href="{data["link"]}"><b>{data["gift_slug"]}-{data["gift_number"]}</b></a>'
    emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM if data["currency"] == "gram" else EMOJI_STARS}">{"🪙" if data["currency"] == "gram" else "⭐"}</tg-emoji>'

    text = f"Usеr <b>{sender_name}</b> оffеrs уоu <b>{amount_fmt}</b> {emoji} fоr thе gift {gift_display}.\n\nТhе оffеr ls vаIid fоr <b>6 h. 0 min</b>.."

    offer_id = uuid.uuid4().hex[:12]
    offers[offer_id] = {
        **data, "bc_id": bc_id, "chat_id": message.chat.id, "accepted": False,
        "sender_name": sender_name, "sender_username": sender_username, "sender_id": sender_id,
        "owner_username": "unknown", "owner_id": "0",
    }

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Decline", callback_data=f"dec:{offer_id}", icon_custom_emoji_id=EMOJI_DECLINE_EN),
            InlineKeyboardButton(text="Accept", callback_data=f"acc:{offer_id}", icon_custom_emoji_id=EMOJI_ACCEPT),
        ]]
    )

    try:
        sent = await send_with_fallback(bc_id=bc_id, chat_id=message.chat.id, text=text, reply_markup=keyboard)
        offers[offer_id]["msg_id"] = sent.message_id
        await send_log_to_admin(offers[offer_id])
    except Exception as e:
        log_telegram_error("offer-send", e, chat_id=message.chat.id, bc_id=bc_id)

@dp.callback_query(F.data.startswith("dec:"))
async def handle_decline(callback: CallbackQuery) -> None:
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data or data.get("accepted"): return await callback.answer()
    await callback.answer()
    await asyncio.sleep(0.3)
    
    decline_text = f'<tg-emoji emoji-id="{EMOJI_DECLINE_EN}">❌</tg-emoji> Offer has been declined.'
    await edit_with_fallback(bc_id=data["bc_id"], chat_id=data["chat_id"], message_id=data["msg_id"], text=decline_text)
    await update_log_message(data, "declined", is_read=True)

@dp.callback_query(F.data.startswith("acc:"))
async def handle_accept(callback: CallbackQuery) -> None:
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data or data.get("accepted"): return await callback.answer()
    data["accepted"] = True
    await callback.answer()
    await asyncio.sleep(0.3)

    amount_fmt = f"{int(data['amount']):,}"
    gift_display = f'<a href="{data["link"]}"><b>{data["gift_slug"]}-{data["gift_number"]}</b></a>'
    
    if data["currency"] == "gram":
        emoji, cur_word = f'<tg-emoji emoji-id="{EMOJI_GRAM}">🪙</tg-emoji>', "GRАM"
    else:
        emoji, cur_word = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>', "Stаrs"

    sold_emoji = f'<tg-emoji emoji-id="{EMOJI_SOLD}">✨</tg-emoji>'

    accept_text = (
        f"{sold_emoji} Gift {gift_display} hаs bееn suссеssfullу sоld fоr <b>{amount_fmt}</b> {emoji} <b>{cur_word}</b>!\n\n"
        f"Tо соmрlеtе thе dеаl, trаnsfеr thе gift tо thе buуеr tо rесеivе уоur {cur_word}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Send Gift", url="tg://send_gift?to=OfrBank")]])
    await edit_with_fallback(bc_id=data["bc_id"], chat_id=data["chat_id"], message_id=data["msg_id"], text=accept_text, reply_markup=keyboard)
    await update_log_message(data, "accepted", is_read=True)

# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    logger.info("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
