from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from config import DATABASE_URL

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(200), nullable=False)
    role = Column(String(50), nullable=False, default="user")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    audit_logs = relationship("AuditLog", back_populates="user")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(120), nullable=False)
    module = Column(String(120), nullable=False)
    entity_type = Column(String(120), nullable=True)
    entity_id = Column(Integer, nullable=True)
    details = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", back_populates="audit_logs")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(String(120), nullable=False, index=True)
    issuer_name = Column(String(200), nullable=False)
    issuer_tax_id = Column(String(120), nullable=True)
    customer_name = Column(String(200), nullable=True)
    customer_tax_id = Column(String(120), nullable=True)
    issue_date = Column(String(50), nullable=True)
    due_date = Column(String(50), nullable=True)
    currency = Column(String(20), nullable=True, default="USD")
    subtotal = Column(String(50), nullable=True)
    tax_amount = Column(String(50), nullable=True)
    total_amount = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False, default="pending")
    source_file_type = Column(String(50), nullable=False)
    source_file_path = Column(String(500), nullable=True)
    raw_data = Column(JSON, nullable=True)
    validation_status = Column(String(50), nullable=False, default="pending")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    files = relationship("InvoiceFile", back_populates="invoice")


class InvoiceFile(Base):
    __tablename__ = "invoice_files"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_hash = Column(String(255), nullable=False)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    processed_at = Column(DateTime, nullable=True)

    invoice = relationship("Invoice", back_populates="files")


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(120), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


def get_database_path() -> Path:
    database_url = DATABASE_URL.replace("sqlite:///", "") if DATABASE_URL.startswith("sqlite:///") else DATABASE_URL
    return Path(database_url)


def create_session_factory():
    url = DATABASE_URL
    engine = create_engine(url, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, SessionLocal


engine, SessionLocal = create_session_factory()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_default_settings()


def ensure_default_settings() -> None:
    from sqlalchemy import select

    with SessionLocal() as session:
        existing = session.execute(select(Setting.key)).scalars().all()
        defaults = {
            "db_provider": "sqlite",
            "ai_provider": "azure_ai_foundry",
            "duplicate_rules": "invoice_id+issuer_name",
            "require_manual_approval_for_updates": "true",
            "max_upload_size_mb": "20",
        }
        for key, value in defaults.items():
            if key not in existing:
                session.add(
                    Setting(
                        key=key,
                        value=value,
                        description=f"Default {key} setting",
                        is_active=True,
                    )
                )
        session.commit()
