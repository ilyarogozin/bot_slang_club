from datetime import datetime
import time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import (
    declarative_base,
    relationship,
    scoped_session,
    sessionmaker
)

from constants import (
    HOST_DB,
    NAME_DB,
    PASSWORD_DB,
    PORT_DB,
    USERNAME_DB,
    logger,
)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=True)
    phone_number = Column(String, unique=True, nullable=False)
    user_link = Column(String, nullable=True)
    messages_acceptable = Column(Boolean, nullable=False, default="true")

    subscriptions = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )
    reviews = relationship(
        "Review", back_populates="user", cascade="all, delete-orphan"
    )
    button_stats = relationship(
        "ButtonStat", back_populates="user", cascade="all, delete-orphan"
    )


class ButtonStat(Base):
    __tablename__ = "button_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String, ForeignKey("users.phone_number"))
    button = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="button_stats")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    start_datetime = Column(DateTime, nullable=False)
    end_datetime = Column(DateTime, nullable=False)
    subscription_link = Column(String, nullable=True)
    chat_link = Column(String, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    user = relationship("User", back_populates="subscriptions")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    review_text = Column(String, nullable=False)

    user = relationship("User", back_populates="reviews")


# Логирование нажатия кнопки
def log_button_click(phone_number: str, button: str):
    with create_session() as session:
        stat = ButtonStat(phone_number=phone_number, button=button)
        session.add(stat)
        session.commit()
        session.close()


# Получение пользователя по номеру телефона
def get_user_by_phone(phone_number: str):
    with create_session() as session:
        user = session.query(User).filter_by(phone_number=phone_number).first()
        session.close()
        if user:
            return user.telegram_id, user.messages_acceptable
        return None


# Получение телефона по telegram_id
def get_phone_by_telegram_id(telegram_id: int):
    with create_session() as session:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        session.close()
        return user.phone_number if user else None


# Изменение доступности сообщений по telegram_id
def set_messages_acceptable(telegram_id: int, acceptable: bool):
    with create_session() as session:
        updated = session.query(User).filter_by(telegram_id=telegram_id).update(
            {"messages_acceptable": acceptable}
        )
        session.commit()
        session.close()
        return updated > 0


# Изменение доступности сообщений по номеру телефона
def set_messages_acceptable_by_phone(phone_number: str, acceptable: bool):
    with create_session() as session:
        updated = session.query(User).filter_by(phone_number=phone_number).update(
            {"messages_acceptable": acceptable}
        )
        session.commit()
        session.close()
        return updated > 0


# Функция для создания сессий к БД с обработкой ошибок
def create_session():
    for i in range(5):
        db = Session()
        try:
            return db
        except Exception as error:
            logger.error(f"Ошибка при создании сессии (попытка {i + 1}/5): {str(error)}")
            db.close()
            time.sleep(2**i)  # Экспоненциальная задержка
    raise Exception("Не удалось создать сессию после нескольких попыток")


# Создание соединения с базой данных PostgreSQL
DATABASE_URL = f"postgresql+psycopg2://{USERNAME_DB}:{PASSWORD_DB}@{HOST_DB}:{PORT_DB}/{NAME_DB}"
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=300,
)

# Создание таблиц в базе данных
Base.metadata.create_all(engine)

# Создаем фабрику сессий
session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)
