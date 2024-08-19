from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String,
                        create_engine, BigInteger)
from sqlalchemy.orm import (declarative_base, relationship, scoped_session,
                            sessionmaker)

from constants import HOST_DB, NAME_DB, PASSWORD_DB, PORT_DB, USERNAME_DB

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=True)
    phone_number = Column(String, unique=True, nullable=False)
    user_link = Column(String, nullable=True)

    subscriptions = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )
    reviews = relationship(
        "Review", back_populates="user", cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    start_datetime = Column(DateTime, nullable=False)
    end_datetime = Column(DateTime, nullable=False)
    subscription_link = Column(String, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    user = relationship("User", back_populates="subscriptions")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    review_text = Column(String, nullable=False)

    user = relationship("User", back_populates="reviews")


# Создание соединения с базой данных PostgreSQL
DATABASE_URL = f"postgresql://{USERNAME_DB}:{PASSWORD_DB}@{HOST_DB}:{PORT_DB}/{NAME_DB}"
engine = create_engine(DATABASE_URL)

# Создание таблиц в базе данных
Base.metadata.create_all(engine)

# Создаем фабрику сессий
session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)
