from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from database.db import Invoice, InvoiceFile, Setting, SessionLocal, User
from services.ai_service import evaluate_invoice_data, extract_invoice_data
from services.audit_service import log_event

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def _build_file_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _save_uploaded_file(file_obj: Any) -> tuple[Path, str]:
    file_name = file_obj.name
    safe_name = file_name.replace(" ", "_")
    target_path = UPLOAD_DIR / safe_name
    target_path.write_bytes(file_obj.getvalue())
    return target_path, safe_name


def _get_invoice_payload(invoice: Invoice | None) -> dict[str, Any]:
    if invoice is None:
        return {}
    return {
        "invoice_number": invoice.invoice_number or invoice.invoice_id or "",
        "seller_name": invoice.seller_name or invoice.issuer_name or "",
        "rfc": invoice.rfc or invoice.issuer_tax_id or "",
        "customer_name": invoice.customer_name,
        "seller_address": invoice.seller_address,
        "issue_date": invoice.issue_date,
        "purchase_details": invoice.purchase_details or {},
        "subtotal": invoice.subtotal,
        "taxes": invoice.taxes or invoice.tax_amount,
        "total": invoice.total or invoice.total_amount,
    }


def list_invoices() -> list[Invoice]:
    with SessionLocal() as session:
        return session.execute(select(Invoice).order_by(Invoice.created_at.desc())).scalars().all()


def list_users() -> list[User]:
    with SessionLocal() as session:
        return session.execute(select(User).order_by(User.created_at.desc())).scalars().all()


def get_audit_logs() -> list:
    from database.db import AuditLog

    with SessionLocal() as session:
        return session.execute(select(AuditLog).order_by(AuditLog.created_at.desc())).scalars().all()


def get_settings() -> list[Setting]:
    with SessionLocal() as session:
        return session.execute(select(Setting).order_by(Setting.id)).scalars().all()


def set_setting(key: str, value: str, description: str | None = None) -> Setting:
    with SessionLocal() as session:
        setting = session.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
        if setting is None:
            setting = Setting(key=key, value=value, description=description or key, is_active=True)
            session.add(setting)
        else:
            setting.value = value
            setting.description = description or setting.description
            setting.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(setting)
        return setting


def get_dashboard_summary() -> dict[str, int]:
    with SessionLocal() as session:
        invoice_count = len(session.execute(select(Invoice.id)).scalars().all())
        user_count = len(session.execute(select(User.id)).scalars().all())
        duplicate_count = len(session.execute(select(Invoice.id).where(Invoice.status == "duplicate")).scalars().all())
        pending_count = len(session.execute(select(Invoice.id).where(Invoice.validation_status == "needs_approval")).scalars().all())
        return {
            "invoices": invoice_count,
            "users": user_count,
            "duplicates": duplicate_count,
            "pending": pending_count,
        }


def preview_uploaded_invoice(file_obj: Any) -> dict[str, Any]:
    if file_obj is None:
        raise ValueError("Please select a valid file.")

    target_path, safe_name = _save_uploaded_file(file_obj)
    file_bytes = file_obj.getvalue()
    file_hash = _build_file_hash(file_bytes)
    file_type = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else "unknown"

    extracted = extract_invoice_data(target_path)
    return {
        "file_name": safe_name,
        "file_type": file_type,
        "target_path": str(target_path),
        "file_hash": file_hash,
        "extracted": extracted,
    }


def _merge_edited_invoice_data(extracted: dict[str, Any], edited_data: dict[str, Any] | None) -> dict[str, Any]:
    if not edited_data:
        return extracted

    merged = dict(extracted)
    for key in (
        "invoice_number",
        "seller_name",
        "rfc",
        "customer_name",
        "seller_address",
        "issue_date",
        "purchase_details",
        "subtotal",
        "taxes",
        "total",
        "currency",
        "customer_tax_id",
        "due_date",
        "issuer_name",
        "issuer_tax_id",
        "tax_amount",
        "total_amount",
    ):
        if key not in edited_data:
            continue
        value = edited_data.get(key)
        if value is None:
            continue
        if key == "purchase_details" and isinstance(value, str):
            try:
                merged[key] = json.loads(value)
            except json.JSONDecodeError:
                merged[key] = value
        else:
            merged[key] = value
    return merged


def process_uploaded_invoice(file_obj: Any, user_id: int, edited_data: dict[str, Any] | None = None) -> dict[str, Any]:
    if file_obj is None:
        raise ValueError("Please select a valid file.")

    target_path, safe_name = _save_uploaded_file(file_obj)
    file_bytes = file_obj.getvalue()
    file_hash = _build_file_hash(file_bytes)
    file_type = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else "unknown"

    extracted = _merge_edited_invoice_data(extract_invoice_data(target_path), edited_data)
    invoice_number = extracted.get("invoice_number") or extracted.get("invoice_id") or "INV-AUTO"
    seller_name = extracted.get("seller_name") or extracted.get("issuer_name") or "Unknown seller"

    with SessionLocal() as session:
        existing = session.execute(
            select(Invoice)
            .where(
                ((Invoice.invoice_number == invoice_number) | (Invoice.invoice_id == invoice_number)),
                ((Invoice.seller_name == seller_name) | (Invoice.issuer_name == seller_name)),
            )
            .order_by(Invoice.created_at.desc())
        ).scalars().first()

        if existing is not None:
            decision = evaluate_invoice_data(_get_invoice_payload(existing), extracted)
            if decision["decision"] == "duplicate":
                invoice_file = InvoiceFile(
                    invoice_id=existing.id,
                    file_name=safe_name,
                    file_type=file_type,
                    file_path=str(target_path),
                    file_hash=file_hash,
                    uploaded_at=datetime.now(timezone.utc),
                    processed_at=datetime.now(timezone.utc),
                )
                session.add(invoice_file)
                existing.status = "duplicate"
                existing.validation_status = "rejected"
                existing.updated_at = datetime.now(timezone.utc)
                session.commit()
                log_event(
                    user_id=user_id,
                    action="duplicate_rejected",
                    module="invoices",
                    entity_type="invoice",
                    entity_id=existing.id,
                    details={"reason": decision["reason"], "source_file": safe_name},
                )
                return {
                    "status": "rejected",
                    "message": "Duplicate invoice detected and automatically rejected.",
                    "invoice_number": existing.invoice_number or existing.invoice_id,
                }

            pending_invoice = Invoice(
                invoice_number=invoice_number,
                seller_name=seller_name,
                rfc=extracted.get("rfc") or extracted.get("issuer_tax_id") or "",
                customer_name=extracted.get("customer_name") or "",
                seller_address=extracted.get("seller_address") or "",
                issue_date=extracted.get("issue_date") or "",
                purchase_details=extracted.get("purchase_details") or {},
                subtotal=str(extracted.get("subtotal") or "0"),
                taxes=str(extracted.get("taxes") or extracted.get("tax_amount") or "0"),
                total=str(extracted.get("total") or extracted.get("total_amount") or "0"),
                invoice_id=invoice_number,
                issuer_name=seller_name,
                issuer_tax_id=extracted.get("rfc") or extracted.get("issuer_tax_id") or "",
                customer_tax_id=extracted.get("customer_tax_id") or "",
                due_date=extracted.get("due_date") or "",
                currency=extracted.get("currency") or "USD",
                tax_amount=str(extracted.get("taxes") or extracted.get("tax_amount") or "0"),
                total_amount=str(extracted.get("total") or extracted.get("total_amount") or "0"),
                status="update_required",
                validation_status="needs_approval",
                source_file_type=file_type,
                source_file_path=str(target_path),
                raw_data=extracted,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(pending_invoice)
            session.commit()
            session.refresh(pending_invoice)

            session.add(
                InvoiceFile(
                    invoice_id=pending_invoice.id,
                    file_name=safe_name,
                    file_type=file_type,
                    file_path=str(target_path),
                    file_hash=file_hash,
                    uploaded_at=datetime.now(timezone.utc),
                    processed_at=datetime.now(timezone.utc),
                )
            )
            session.commit()

            log_event(
                user_id=user_id,
                action="update_pending",
                module="invoices",
                entity_type="invoice",
                entity_id=pending_invoice.id,
                details={"reason": decision["reason"], "source_file": safe_name},
            )
            return {
                "status": "update_required",
                "message": "A change was detected and is pending user approval.",
                "invoice_number": pending_invoice.invoice_number,
            }

        invoice = Invoice(
            invoice_number=invoice_number,
            seller_name=seller_name,
            rfc=extracted.get("rfc") or extracted.get("issuer_tax_id") or "",
            customer_name=extracted.get("customer_name") or "",
            seller_address=extracted.get("seller_address") or "",
            issue_date=extracted.get("issue_date") or "",
            purchase_details=extracted.get("purchase_details") or {},
            subtotal=str(extracted.get("subtotal") or "0"),
            taxes=str(extracted.get("taxes") or extracted.get("tax_amount") or "0"),
            total=str(extracted.get("total") or extracted.get("total_amount") or "0"),
            invoice_id=invoice_number,
            issuer_name=seller_name,
            issuer_tax_id=extracted.get("rfc") or extracted.get("issuer_tax_id") or "",
            customer_tax_id=extracted.get("customer_tax_id") or "",
            due_date=extracted.get("due_date") or "",
            currency=extracted.get("currency") or "USD",
            tax_amount=str(extracted.get("taxes") or extracted.get("tax_amount") or "0"),
            total_amount=str(extracted.get("total") or extracted.get("total_amount") or "0"),
            status="approved",
            validation_status="valid",
            source_file_type=file_type,
            source_file_path=str(target_path),
            raw_data=extracted,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(invoice)
        session.commit()
        session.refresh(invoice)

        session.add(
            InvoiceFile(
                invoice_id=invoice.id,
                file_name=safe_name,
                file_type=file_type,
                file_path=str(target_path),
                file_hash=file_hash,
                uploaded_at=datetime.now(timezone.utc),
                processed_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

        log_event(
            user_id=user_id,
            action="invoice_created",
            module="invoices",
            entity_type="invoice",
            entity_id=invoice.id,
            details={"source_file": safe_name, "status": "approved"},
        )

        return {
            "status": "approved",
            "message": "Invoice registered successfully.",
            "invoice_number": invoice.invoice_number,
        }


def approve_invoice_update(invoice_id: int) -> dict[str, Any]:
    with SessionLocal() as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            return {"status": "not_found", "message": "Invoice not found."}
        invoice.status = "approved"
        invoice.validation_status = "valid"
        invoice.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"status": "approved", "message": "Update accepted and invoice replaced."}
