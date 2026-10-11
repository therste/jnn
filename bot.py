import asyncio
import logging
import random
import re
import string
import uuid
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from aiogram.types import (
    Message,
    CallbackQuery,
    BusinessConnection,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ParseMode

BOT_TOKEN = "8850969557:AAF7ISM1vYIyqji6MGNMOfvImFWXG3S5pic"

EMOJI_STARS = "5920433463428650761"
EMOJI_GRAM = "5267445215773367585"
EMOJI_DECLINE_EN = "5774077015388852135"
EMOJI_ACCEPT = "5774022692642492953"

OFFER_USERNAME = "Reggan"  # визуальные двойники
BUYER_USERNAME = "tonreggan"

SEND_DELAY = 1.0

_chat_cooldowns: dict[int, float] = defaultdict(float)
_user_cooldowns: dict[int, float] = defaultdict(float)
_send_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

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


@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection) -> None:
    business_owner_ids[connection.id] = connection.user.id
    logger.info(f"[BC] owner={connection.user.id} connection_id={connection.id}")


def parse_nft_link(link: str):
    pattern = r"(?:https?://)?t\.me/nft/([A-Za-z]+)-(\d+)"
    m = re.search(pattern, link)
    if not m:
        return None
    slug = m.group(1)
    number = m.group(2)
    full_link = f"https://t.me/nft/{slug}-{number}"
    return full_link, slug, number


def fmt_number(num: str) -> str:
    """12345678 -> 12,345,678"""
    return f"{int(num):,}"


def format_slug(slug: str) -> str:
    """ToyBear -> Toy Bear"""
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', slug)


def parse_offer_command(text: str):
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != ".redo":
        return None
    link = parts[1]
    amount = parts[2]
    if not amount.isdigit():
        return None

    currency = "stars"

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


async def send_with_fallback(bc_id, chat_id, text, reply_markup):
    async with _send_locks[chat_id]:
        for attempt in range(3):
            try:
                result = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    business_connection_id=bc_id,
                    disable_web_page_preview=False,
                )
                await asyncio.sleep(SEND_DELAY)
                return result
            except TelegramRetryAfter as e:
                wait = e.retry_after + 1
                logger.warning(f"[send] FloodWait {wait}s, ждём...")
                await asyncio.sleep(wait)
            except TelegramForbiddenError as e:
                logger.error(f"[send] Forbidden: {e}")
                raise
            except TelegramBadRequest as e:
                logger.warning(f"[send] BadRequest: {e}, retry without emoji")
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
                    await asyncio.sleep(SEND_DELAY)
                    return result
                except TelegramRetryAfter as e2:
                    await asyncio.sleep(e2.retry_after + 1)
                except Exception as e2:
                    logger.error(f"[send] fallback failed: {e2}")
                    raise
            except Exception as e:
                logger.error(f"[send] error: {e}")
                raise
    raise RuntimeError("send_with_fallback: все попытки провалились")


async def edit_with_fallback(bc_id, chat_id, message_id, text, reply_markup=None):
    for attempt in range(3):
        try:
            await bot.edit_message_text(
                business_connection_id=bc_id,
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=False,
            )
            logger.info("[edit] OK")
            await asyncio.sleep(0.5)
            return True
        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(f"[edit] FloodWait {wait}s")
            await asyncio.sleep(wait)
        except TelegramBadRequest as e:
            logger.warning(f"[edit] BadRequest: {e}, retry without emoji")
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
                    disable_web_page_preview=False,
                )
                logger.info("[edit] OK (fallback)")
                await asyncio.sleep(0.5)
                return True
            except TelegramRetryAfter as e2:
                await asyncio.sleep(e2.retry_after + 1)
            except Exception as e2:
                logger.error(f"[edit] fallback failed: {e2}")
                return False
        except Exception as e:
            logger.error(f"[edit] error: {e}")
            return False
    return False


@dp.business_message(F.text.startswith(".redo"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id:
        logger.warning("[offer] no business_connection_id")
        return

    bc_id = message.business_connection_id
    chat_id = message.chat.id

    data = parse_offer_command(message.text)
    if not data:
        logger.warning(f"[offer] parse failed for text: {message.text!r}")
        return

    logger.info(
        f"[offer] parsed: amount={data['amount']} "
        f"currency={data['currency']} gift={data['gift_slug']}-{data['gift_number']}"
    )

    try:
        await bot.delete_business_messages(
            business_connection_id=bc_id,
            message_ids=[message.message_id],
        )
        logger.info("[offer] команда .redo удалена")
    except Exception as e:
        logger.warning(f"[offer] delete failed: {e}")

    await asyncio.sleep(3)

    amount_fmt = fmt_number(data["amount"])
    gift_num_fmt = fmt_number(data["gift_number"])
    slug_formatted = format_slug(data["gift_slug"])
    gift_plain = f'{slug_formatted} #{gift_num_fmt}'
    gift_display = f'<a href="{data["link"]}"><b>{gift_plain}</b></a>'

    emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'

    offer_minutes = 359  # 5ч 59м
    h, m = divmod(offer_minutes, 60)

    text = (
        f"Пoльзoвaтeль <b>{OFFER_USERNAME}</b> пpeдлaгaeт Baм "
        f"<b>{amount_fmt}</b> {emoji} <b>Ѕtarѕ</b> "
        f"зa пoдapoк {gift_display}.\n\n"
        f"Пpeдлoжeниe дeйcтвyeт eщё <b>{h}ч {m}м</b>."
    )

    offer_id = uuid.uuid4().hex[:12]
    offers[offer_id] = {
        **data,
        "bc_id": bc_id,
        "chat_id": chat_id,
        "accepted": False,
    }

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"dec:{offer_id}",
                    icon_custom_emoji_id=EMOJI_DECLINE_EN,
                ),
                InlineKeyboardButton(
                    text="Принять",
                    callback_data=f"acc:{offer_id}",
                    icon_custom_emoji_id=EMOJI_ACCEPT,
                ),
            ]
        ]
    )

    try:
        sent = await send_with_fallback(
            bc_id=bc_id,
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
        )
        offers[offer_id]["msg_id"] = sent.message_id
        logger.info(
            f"[offer] {offer_id} отправлен в чат {chat_id} "
            f"(msg_id={sent.message_id})"
        )
    except Exception as e:
        logger.error(f"[offer] send failed: {e}")


@dp.callback_query(F.data.startswith("dec:"))
async def handle_decline(callback: CallbackQuery) -> None:
    logger.info(f"[decline] callback: {callback.data}")
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data:
        logger.warning(f"[decline] offer {offer_id} not found")
        try:
            await callback.answer("Заявка не найдена.", show_alert=True)
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

    decline_text = (
        f'<tg-emoji emoji-id="{EMOJI_DECLINE_EN}">❌</tg-emoji> '
        f"Предложение было отклонено."
    )

    await edit_with_fallback(
        bc_id=data["bc_id"],
        chat_id=data["chat_id"],
        message_id=data["msg_id"],
        text=decline_text,
    )
    logger.info(f"[decline] offer {offer_id} обновлён")

    try:
        await callback.answer()
    except Exception:
        pass


@dp.callback_query(F.data.startswith("acc:"))
async def handle_accept(callback: CallbackQuery) -> None:
    logger.info(f"[accept] callback: {callback.data}")
    _, offer_id = callback.data.split(":", 1)
    data = offers.get(offer_id)
    if not data:
        logger.warning(f"[accept] offer {offer_id} not found")
        try:
            await callback.answer("Заявка не найдена.", show_alert=True)
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

    # Форматируем данные из ссылки
    amount_display = data["amount"]  # 1000 (без запятой, как на скрине)
    amount_fmt = fmt_number(data["amount"])
    gift_num_fmt = fmt_number(data["gift_number"])
    slug_formatted = format_slug(data["gift_slug"])
    gift_plain = f'{slug_formatted} #{gift_num_fmt}'
    gift_display = f'<a href="{data["link"]}"><b>{gift_plain}</b></a>'

    # 1. КБ Ансвер с данными из ссылки
    popup_text = (
        f"Kristen Espinoza\n\n"
        f"Предложение о продаже {gift_plain} успешно принято.\n\n"
        f"Передайте подарок покупателю для зачисления {amount_display} Stars на ваш баланс."
    )
    try:
        await callback.answer(text=popup_text, show_alert=True)
    except Exception as e:
        logger.warning(f"[accept] callback.answer failed: {e}")

    # 2. Небольшая пауза, чтобы пользователь увидел всплывающее окно
    await asyncio.sleep(1)

    # 3. Меняем текст сообщения
    accept_text = (
        f"Пoдapoк: {gift_display} ycпeшнo пpoдaн!\n\n"
        f"• Пepeдaйтe пoдapoк пoкyпaтeлю для зaчиcлeния Ѕtarѕ."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Передать",
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

    try:
        await callback.answer()
    except Exception:
        pass


async def main() -> None:
    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
