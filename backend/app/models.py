from datetime import datetime, timezone
import uuid
from sqlalchemy import String, Text, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def uid():
    return str(uuid.uuid4())


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    status: Mapped[str] = mapped_column(String(24), default='ai_ready')
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    latest_user_sequence: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Message(Base):
    __tablename__ = 'messages'
    __table_args__ = (UniqueConstraint('conversation_id', 'sequence'),
                     UniqueConstraint('conversation_id', 'client_message_id'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    client_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Handoff(Base):
    __tablename__ = 'handoffs'
    __table_args__ = (UniqueConstraint('conversation_id', 'round'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    round: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    covered_sequence: Mapped[int] = mapped_column(Integer)
    read_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VisitorSession(Base):
    __tablename__ = 'visitor_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Merchant(Base):
    __tablename__ = 'merchant_accounts'
    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(256))


class MerchantSession(Base):
    __tablename__ = 'merchant_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey('merchant_accounts.username'))
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
