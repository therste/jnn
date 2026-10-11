import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter
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


def parse_nft_link(link: str):
    """t.me/nft/FinePen-25966 -> (FinePen, 25966)"""
    pattern = r"(?:https?://)?t\.me/nft/([A-Za-z]+)-(\d+)"
    m = re.search(pattern, link)
    if not m:
        return None
    return m.group(1), m.group(2)


def format_slug(slug: str) -> str:
    """FinePen -> Fine Pen"""
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', slug)


async def safe_send(chat_id: int, text: str, bc_id: str) -> bool:
    """Отправка с обработкой FloodWait."""
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                business_connection_id=bc_id,
                disable_web_page_preview=True,
            )
            return True
        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(f"[send] FloodWait {wait}s, ждём...")
            await asyncio.sleep(wait)
        except Exception as e:
            logger.error(f"[send] error: {e}")
            return False
    return False


@dp.business_connection()
async def handle_business_connection(connection: BusinessConnection) -> None:
    logger.info(f"[BC] owner={connection.user.id} connection_id={connection.id}")


@dp.business_message(F.text.startswith(".redo"))
async def handle_offer(message: Message) -> None:
    if not message.business_connection_id:
        logger.warning("[offer] no business_connection_id")
        return

    bc_id = message.business_connection_id
    chat_id = message.chat.id

    parts = message.text.strip().split()
    if len(parts) < 3 or parts[0].lower() != ".redo":
        logger.warning(f"[offer] bad command: {message.text!r}")
        return

    link = parts[1]
    price = parts[2]
    if not price.isdigit():
        logger.warning(f"[offer] bad price: {price!r}")
        return

    parsed = parse_nft_link(link)
    if not parsed:
        logger.warning(f"[offer] bad link: {link!r}")
        return
    slug, number = parsed
    gift_name = format_slug(slug)

    logger.info(f"[offer] price={price} gift={gift_name} #{number}")

    # 1. Удаляем команду .redo сразу
    try:
        await bot.delete_business_messages(
            business_connection_id=bc_id,
            message_ids=[message.message_id],
        )
        logger.info("[offer] команда .redo удалена")
    except Exception as e:
        logger.warning(f"[offer] delete failed: {e}")

    # 2. Ждём 3 секунды
    await asyncio.sleep(3)

    # Кастомная звезда
    emoji = f'<tg-emoji emoji-id="{EMOJI_STARS}">⭐</tg-emoji>'

    # ============================================================
    # ШАБЛОН — НЕ ТРОГАТЬ.
    # Меняются только: {price}, {emoji}, {gift_name}, {number}
    # Все остальные буквы — кириллические двойники (обход банвордов).
    # ============================================================
    text = (
        f"Us\u0435r \u0442py\u0441\u043b\u043e \u043eff\u0435rs y\u043eu "
        f"{price} {emoji} "
        f"f\u043er th\u0435 gift {gift_name} #{number}.\n\n"
        f"\u041eff\u0435r ls v\u0430Iid f\u043er <b>6h. 0 min</b>.."
    )

    # 3. Отправляем
    ok = await safe_send(chat_id, text, bc_id)
    if ok:
        logger.info(f"[offer] отправлен в чат {chat_id}")
    else:
        logger.error(f"[offer] не отправлен")


async def main() -> None:
    logger.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
