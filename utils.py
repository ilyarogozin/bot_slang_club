import datetime
import logging
import time

from dateutil.relativedelta import relativedelta
from telegram import Bot
from telegram.ext import CallbackContext

from constants import CHANNEL_ID, CHAT_ID, MONTHS
from database import Session, Subscription, User

# Включаем логгирование
logging.basicConfig(
    filename="bot.log",  # Имя файла для записи логов
    filemode="a",  # Режим открытия файла, 'a' означает добавление
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# Функция для удаления пользователя из канала
def kick_user_from_channel(bot, user_id: int, chat_id: str) -> None:
    try:
        bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info(
            f"Пользователь с телеграм id: {user_id} был удалён из канала: {chat_id}")
        bot.unban_chat_member(
            chat_id=chat_id, user_id=user_id, only_if_banned=True)
    except Exception as error:
        logger.error(f"Ошибка при удалении пользователя: {str(error)}")
    return None


# Создаем ссылку на вступление в канал с ограничением действия
def create_invite_link(
    bot: Bot,
    expiration_datetime: datetime.datetime,
    chat_id: str,
    retries=3,
    flood_delay=40,
) -> str:
    expiration_timestamp = int(expiration_datetime.timestamp())
    invite_link = None
    for attempt in range(retries):
        try:
            invite_link = bot.create_chat_invite_link(
                chat_id=chat_id, member_limit=1, expire_date=expiration_timestamp
            ).invite_link
            if invite_link:
                return invite_link
        except Exception as error:
            logger.error(
                f"Попытка создать ссылку {attempt + 1} failed: {error}")
            if "Flood control exceeded" in str(error):
                time.sleep(flood_delay)
            else:
                time.sleep(3)  # Ожидание по умолчанию для других ошибок
    if not invite_link:
        logger.error("Failed to create invite link after multiple attempts")
    return invite_link


# Логика обновления подписки
def update_subscription(
    paid_months: int, phone_number: str, start_month: int, start_year: int, tg: str
) -> None:
    start_datetime = datetime.datetime(
        day=1, month=start_month, year=start_year, hour=12, minute=0
    )
    end_datetime = start_datetime + relativedelta(months=paid_months - 1)
    end_datetime = end_datetime.replace(
        day=MONTHS[end_datetime.month][1], hour=23, minute=59
    )
    with create_session() as session:
        try:
            # Получаем пользователя по номеру телефона с блокировкой записи в БД
            user = (
                session.query(User)
                .filter(User.phone_number == phone_number)
                .with_for_update()
                .first()
            )
            # Если новый пользователь
            if not user:
                user_link = f"https://t.me/{tg}"
                new_user = User(phone_number=phone_number, user_link=user_link)
                session.add(new_user)
                session.commit()
                new_subscription = Subscription(
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    user_id=new_user.id,
                )
                session.add(new_subscription)
                session.commit()
            else:
                # Если пользователь уже существует
                new_subscription = Subscription(
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    user_id=user.id,
                )
                session.add(new_subscription)
                session.commit()
        except Exception as error:
            logger.error(
                f"Ошибка при обновлении подписки в update_subscription: {str(error)}"
            )
            session.rollback()
        finally:
            Session.remove()  # Удаляем сессию из контекста
    return None


# Проверяем присутствие пользователя в канале
def check_user_in_channel(context: CallbackContext, user_id: int, chat_id: str) -> bool:
    try:
        member = context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if member.status in {"member", "administrator", "creator"}:
            return True
    except Exception as error:
        logger.warning(f"Пользователь не является участником канала: {error}")
        return False


# Функция для создания сессий к БД с обработкой ошибок
def create_session():
    retries = 5
    for i in range(retries):
        try:
            session = Session()
            return session
        except Exception as error:
            logger.error(f"Ошибка при создании сессии: {str(error)}")
            # Экспоненциальная задержка перед повторной попыткой
            time.sleep(2**i)
    raise Exception("Не удалось создать сессию после нескольких попыток")
