from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_read_text(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_from_xml(file_path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(file_path).getroot()
        def find_text(path: str) -> str:
            element = root.find(path)
            return element.text.strip() if element is not None and element.text else ""
        return {
            "invoice_id": find_text(".//InvoiceId") or find_text(".//id") or "AUTO-XML",
            "issuer_name": find_text(".//IssuerName") or find_text(".//emisor") or "Unknown issuer",
            "issuer_tax_id": find_text(".//IssuerTaxId") or find_text(".//RFC") or "",
            "customer_name": find_text(".//CustomerName") or find_text(".//cliente") or "",
            "customer_tax_id": find_text(".//CustomerTaxId") or find_text(".//ClienteRFC") or "",
            "issue_date": find_text(".//IssueDate") or find_text(".//fecha") or "",
            "due_date": find_text(".//DueDate") or find_text(".//vencimiento") or "",
            "currency": find_text(".//Currency") or "USD",
            "subtotal": find_text(".//Subtotal") or "0",
            "tax_amount": find_text(".//TaxAmount") or "0",
            "total_amount": find_text(".//TotalAmount") or find_text(".//Total") or "0",
        }
    except Exception:
        return {}


def extract_invoice_data(file_path: Path) -> dict[str, Any]:
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    payload: dict[str, Any] = {
        "invoice_id": f"INV-{hashlib.sha256(file_path.name.encode('utf-8')).hexdigest()[:12]}",
        "issuer_name": "Unknown issuer",
        "issuer_tax_id": "",
        "customer_name": "",
        "customer_tax_id": "",
        "issue_date": "",
        "due_date": "",
        "currency": "USD",
        "subtotal": "0",
        "tax_amount": "0",
        "total_amount": "0",
    }

    if suffix == ".xml":
        xml_data = _extract_from_xml(file_path)
        if xml_data:
            payload.update(xml_data)
            return payload

    text_blob = _safe_read_text(file_path)
    if text_blob:
        lines = [line.strip() for line in text_blob.splitlines() if line.strip()]
        for line in lines:
            if "invoice" in line.lower() and "id" in line.lower():
                payload["invoice_id"] = line.split(":")[-1].strip() or payload["invoice_id"]
            if "issuer" in line.lower() and "name" in line.lower():
                payload["issuer_name"] = line.split(":")[-1].strip() or payload["issuer_name"]
            if "total" in line.lower() and "amount" in line.lower():
                payload["total_amount"] = line.split(":")[-1].strip() or payload["total_amount"]

    if payload["invoice_id"].startswith("INV-") and "-" in file_path.stem:
        payload["invoice_id"] = file_path.stem.upper()

    if payload["issuer_name"] == "Unknown issuer":
        payload["issuer_name"] = file_path.stem.replace("-", " ").title() or "Unknown issuer"

    raw_data = {
        "source_file": str(file_path),
        "source_type": suffix.lstrip("."),
        "extracted": {key: value for key, value in payload.items() if key != "raw_data"},
    }
    payload["raw_data"] = raw_data
    return payload


def evaluate_invoice_data(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    existing_map = existing or {}
    candidate_map = candidate or {}

    same_invoice_id = _normalize(existing_map.get("invoice_id")) == _normalize(candidate_map.get("invoice_id"))
    same_issuer = _normalize(existing_map.get("issuer_name")) == _normalize(candidate_map.get("issuer_name"))

    if same_invoice_id and same_issuer:
        existing_fields = {
            "invoice_id": existing_map.get("invoice_id"),
            "issuer_name": existing_map.get("issuer_name"),
            "issuer_tax_id": existing_map.get("issuer_tax_id"),
            "customer_name": existing_map.get("customer_name"),
            "customer_tax_id": existing_map.get("customer_tax_id"),
            "issue_date": existing_map.get("issue_date"),
            "due_date": existing_map.get("due_date"),
            "currency": existing_map.get("currency"),
            "subtotal": existing_map.get("subtotal"),
            "tax_amount": existing_map.get("tax_amount"),
            "total_amount": existing_map.get("total_amount"),
        }
        candidate_fields = {
            "invoice_id": candidate_map.get("invoice_id"),
            "issuer_name": candidate_map.get("issuer_name"),
            "issuer_tax_id": candidate_map.get("issuer_tax_id"),
            "customer_name": candidate_map.get("customer_name"),
            "customer_tax_id": candidate_map.get("customer_tax_id"),
            "issue_date": candidate_map.get("issue_date"),
            "due_date": candidate_map.get("due_date"),
            "currency": candidate_map.get("currency"),
            "subtotal": candidate_map.get("subtotal"),
            "tax_amount": candidate_map.get("tax_amount"),
            "total_amount": candidate_map.get("total_amount"),
        }

        if existing_fields == candidate_fields:
            return {"decision": "duplicate", "reason": "Same invoice id and issuer with identical data."}

        return {"decision": "update", "reason": "Same invoice id and issuer but different data requires approval."}

    return {"decision": "new", "reason": "No duplicate candidate found."}
