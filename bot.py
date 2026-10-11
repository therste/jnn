import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, BusinessConnection
from aiogram.enums import ParseMode

BOT_TOKEN = "8610517917:AAFj4Y-Pd969G4MJ1KAGT9Cvm9Sx_HtaVtE"

EMOJI_STARS = "5920433463428650761"

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


@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection) -> None:
    logger.info(f"[BC] owner={connection.user.id} connection_id={connection.id}")


def parse_nft_link(link: str):
    """t.me/nft/ResistanceDog-6647 -> (full_link, slug, number)"""
    pattern = r"(?:https?://)?t\.me/nft/([A-Za-z]+)-(\d+)"
    m = re.search(pattern, link)
    if not m:
        return None
    slug = m.group(1)
    number = m.group(2)
    full_link = f"https://t.me/nft/{slug}-{number}"
    return full_link, slug, number


def parse_offer_command(text: str):
    """`.redo <link> <amount> [stars]`"""
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != ".redo":
        return None

    link = parts[1]
    amount = parts[2]
    if not amount.isdigit():
        return None

    parsed = parse_nft_link(link)
    if not parsed:
        return None
    full_link, slug, number = parsed

    return {
        "link": full_link,
        "gift_slug": slug,
        "gift_number": number,
        "amount": amount,
    }


@dp.business_message(F.text.startswith(".redo"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id:
        logger.warning("[offer] no business_connection_id")
        return

    bc_id = message.business_connection_id
    chat_id = message.chat.id

    data = parse_offer_command(message.text)
    if not data:
        logger.warning(f"[offer] parse failed: {message.text!r}")
        return

    logger.info(
        f"[offer] amount={data['amount']} gift={data['gift_slug']}-{data['gift_number']}"
    )

    # Удаляем команду .redo
    try:
        await bot.delete_business_messages(
            business_connection_id=bc_id,
            message_ids=[message.message_id],
        )
        logger.info("[offer] команда .redo удалена")
    except Exception as e:
        logger.warning(f"[offer] delete failed: {e}")

    # Ждём 3 секунды
    await asyncio.sleep(3)

    # Ник пользователя
    user_name = "User"
    if message.from_user:
        user_name = message.from_user.full_name or message.from_user.first_name or "User"

    amount_fmt = f"{int(data['amount']):,}" if len(data['amount']) >= 4 else data['amount']
    gift_plain = f'{data["gift_slug"]}-{data["gift_number"]}'

    emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'

    text = (
        f"Usеr <b>{user_name}</b> оffеrs уоu "
        f"<b>{amount_fmt}</b> {emoji} "
        f"fоr thе gift <b>{gift_plain}</b> "
        f"({data['link']}).\n\n"
        f"Тhе оffеr is vаlid fоr <b>6 h. 0 min</b>.."
    )

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            business_connection_id=bc_id,
            disable_web_page_preview=True,
        )
        logger.info(f"[offer] отправлен в чат {chat_id}")
    except Exception as e:
        logger.error(f"[offer] send failed: {e}")


async def main() -> None:
    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
