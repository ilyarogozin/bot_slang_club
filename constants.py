import os
import re
import logging

import pytz
from dotenv import load_dotenv

# Включаем логгирование
logging.basicConfig(
    filename="bot.log",  # Имя файла для записи логов
    filemode="a",  # Режим открытия файла, 'a' означает добавление
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

PAYMENT_KEY = os.getenv("PAYMENT_KEY")
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # ID вашего канала
CHAT_ID = os.getenv("CHAT_ID")  # ID вашего чата-болталки
PHONE_NUMBER_REGEX = re.compile(r"^\+[1-9]\d{1,14}$")
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
MODERATOR_IDS = {436665993, 57145578, 270966498}
USERNAME_DB = os.getenv("USERNAME_DB")
PASSWORD_DB = os.getenv("PASSWORD_DB")
HOST_DB = os.getenv("HOST_DB")
PORT_DB = os.getenv("PORT_DB")
NAME_DB = os.getenv("NAME_DB")
DOMAIN = os.getenv("DOMAIN")
TELEGRAM_WEBHOOK = os.getenv("TELEGRAM_WEBHOOK")
PAYMENT_WEBHOOK = os.getenv("PAYMENT_WEBHOOK")
WAITING_NUMBERS = 1
MONTHS = {
    1: ("январь", 31),
    2: ("февраль", 28),
    3: ("март", 31),
    4: ("апрель", 30),
    5: ("май", 31),
    6: ("июнь", 30),
    7: ("июль", 31),
    8: ("август", 31),
    9: ("сентябрь", 30),
    10: ("октябрь", 31),
    11: ("ноябрь", 30),
    12: ("декабрь", 31),
}
TEXT_INVITATION = (
    "Ма френд, привет! ✨\n\n"
    "Ссылка-приглашение для вступления в сленг-клуб "
    "«Sensei, for real!?»: {invite_link}\n\n"
    "Ссылка-приглашение для вступления в чат клуба "
    "«Sensei, for real!?»:  {chat_link}\n\n"
    "Кликни по ссылке, и сленг-клуб и чат клуба автоматически появятся в списке твоих "
    "чатов. И да, с этого момента твоя жизнь круто изменится. Ведь нет "
    "ничего приятнее, чем становиться лучше с каждым днём!\n\n"
    "До встречи в сленг-клубе 😉"
)
LINK_IS_EMPTY = "ссылка отсутствует, обратитесь в поддержку"
THESE_ARE_YOUR_LINKS = (
    "Ма френд, привет!:)\n\n"
    "Ссылка-приглашение для вступления в сленг-клуб «Sensei, for real!?»:  {invite_link}\n\n"
    "Ссылка-приглашение для вступления в чат клуба «Sensei, for real!?»:  {chat_link}\n\n"
    "Переходи скорее по ссылкам 🙌\n"
    "Жду тебя ✨"
)
