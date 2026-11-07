from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine, inspect, select, text
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
    invoice_number = Column(String(120), nullable=False, index=True, default="INV-AUTO")
    seller_name = Column(String(200), nullable=False, default="Unknown seller")
    rfc = Column(String(120), nullable=True)
    customer_name = Column(String(200), nullable=True)
    seller_address = Column(String(255), nullable=True)
    issue_date = Column(String(50), nullable=True)
    purchase_details = Column(JSON, nullable=True, default=dict)
    subtotal = Column(String(50), nullable=True)
    taxes = Column(String(50), nullable=True)
    total = Column(String(50), nullable=True)
    status = Column(String(50), nullable=False, default="pending")
    source_file_type = Column(String(50), nullable=False)
    source_file_path = Column(String(500), nullable=True)
    raw_data = Column(JSON, nullable=True)
    validation_status = Column(String(50), nullable=False, default="pending")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Legacy compatibility columns kept during migration until the full app is updated.
    invoice_id = Column(String(120), nullable=True, index=True)
    issuer_name = Column(String(200), nullable=True)
    issuer_tax_id = Column(String(120), nullable=True)
    customer_tax_id = Column(String(120), nullable=True)
    due_date = Column(String(50), nullable=True)
    currency = Column(String(20), nullable=True, default="USD")
    tax_amount = Column(String(50), nullable=True)
    total_amount = Column(String(50), nullable=True)

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


def ensure_invoice_schema() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        existing_columns = {column["name"] for column in inspector.get_columns("invoices")}

        schema_updates = {
            "invoice_number": "VARCHAR(120)",
            "seller_name": "VARCHAR(200)",
            "rfc": "VARCHAR(120)",
            "seller_address": "VARCHAR(255)",
            "purchase_details": "JSON",
            "taxes": "VARCHAR(50)",
            "total": "VARCHAR(50)",
        }

        for column_name, column_type in schema_updates.items():
            if column_name not in existing_columns:
                conn.execute(text(f'ALTER TABLE invoices ADD COLUMN "{column_name}" {column_type}'))

    with SessionLocal() as session:
        rows = session.execute(select(Invoice)).scalars().all()
        for row in rows:
            if not row.invoice_number and row.invoice_id:
                row.invoice_number = row.invoice_id
            if not row.seller_name and row.issuer_name:
                row.seller_name = row.issuer_name
            if not row.rfc and row.issuer_tax_id:
                row.rfc = row.issuer_tax_id
            if not row.total and row.total_amount:
                row.total = row.total_amount
            if not row.taxes and row.tax_amount:
                row.taxes = row.tax_amount
            if not row.purchase_details and row.raw_data:
                row.purchase_details = row.raw_data
            if not row.invoice_id and row.invoice_number:
                row.invoice_id = row.invoice_number
            if not row.issuer_name and row.seller_name:
                row.issuer_name = row.seller_name
            if not row.issuer_tax_id and row.rfc:
                row.issuer_tax_id = row.rfc
            if not row.total_amount and row.total:
                row.total_amount = row.total
            if not row.tax_amount and row.taxes:
                row.tax_amount = row.taxes
        session.commit()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_invoice_schema()
    ensure_default_settings()


def ensure_default_settings() -> None:
    from sqlalchemy import select

    with SessionLocal() as session:
        existing = session.execute(select(Setting.key)).scalars().all()
        defaults = {
            "db_provider": "sqlite",
            "ai_provider": "azure_content_understanding",
            "duplicate_rules": "invoice_number+seller_name",
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
