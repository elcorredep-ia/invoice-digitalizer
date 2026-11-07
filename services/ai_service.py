from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:  # pragma: no cover
    RapidOCR = None

from config import (
    AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID,
    AZURE_CONTENT_UNDERSTANDING_API_VERSION,
    AZURE_CONTENT_UNDERSTANDING_ENDPOINT,
    AZURE_CONTENT_UNDERSTANDING_KEY,
)


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_read_text(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _clean_money(value: Any) -> str:
    text = _stringify(value)
    if not text:
        return ""

    text = text.replace("$", "").replace("USD", "").replace("MXN", "").replace("M.N.", "").replace("M.N", "").replace("MN", "")
    text = text.replace(",", "")
    text = re.sub(r"(?i)[A-Za-z]+$", "", text)
    text = text.replace(" ", "")
    return text.strip()


def _normalize_ocr_text(text: str) -> str:
    normalized = text.strip()
    normalized = re.sub(r"(?i)\bclie\s*nte\b", "Cliente", normalized)
    normalized = re.sub(r"(?i)\bdirecc?i[nó]n\b", "DIRECCIÓN", normalized)
    normalized = re.sub(r"(?i)\bS\.?\s*A\s*DEC\.?\s*V\.?\b", "S.A. DE C.V.", normalized)
    normalized = re.sub(r"(?i)\bS\.?\s*A\s*DE\s*C\.?\s*V\.?\b", "S.A. DE C.V.", normalized)
    normalized = re.sub(r"(?i)\bsub\s*total\b", "Subtotal", normalized)
    normalized = re.sub(r"(?i)\biva\b", "IVA", normalized)
    normalized = re.sub(r"(?i)\btotal\b", "TOTAL", normalized)
    return normalized


def _repair_ocr_spacing(text: str) -> str:
    cleaned = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    cleaned = cleaned.replace("*", "")
    cleaned = re.sub(r"(?<=\w)\s*'\s*(?=[A-Z])", '"', cleaned)
    cleaned = re.sub(r'(?<=[A-Za-z])"(?=[A-Z])', ' "', cleaned)
    cleaned = re.sub(r"(?<=[a-z])\s+(?=(?!de\b|del\b|la\b|el\b|y\b|en\b|un\b|una\b|por\b|para\b|con\b)[a-z]{2,})", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    if cleaned.count('"') == 1 and not cleaned.endswith('"'):
        cleaned += '"'
    if cleaned.count('"') == 1 and cleaned.startswith('"') and not cleaned.endswith('"'):
        cleaned += '"'
    return cleaned.strip()


def _cleanup_name(value: Any) -> str:
    text = _repair_ocr_spacing(_stringify(value))
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\.+$", ".", text)
    text = text.rstrip(" ,:;-")
    return text


def _extract_text_from_line(line: str) -> str:
    if ":" not in line:
        return ""
    return line.split(":", 1)[1].strip()


def _extract_from_xml(file_path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(file_path).getroot()

        def find_text(path: str) -> str:
            element = root.find(path)
            return element.text.strip() if element is not None and element.text else ""

        return {
            "invoice_number": find_text(".//InvoiceId") or find_text(".//id") or "AUTO-XML",
            "seller_name": find_text(".//IssuerName") or find_text(".//SellerName") or find_text(".//emisor") or "Unknown seller",
            "rfc": find_text(".//IssuerTaxId") or find_text(".//RFC") or find_text(".//SellerTaxId") or "",
            "customer_name": find_text(".//CustomerName") or find_text(".//cliente") or "",
            "seller_address": find_text(".//SellerAddress") or find_text(".//IssuerAddress") or "",
            "issue_date": find_text(".//IssueDate") or find_text(".//fecha") or "",
            "subtotal": find_text(".//Subtotal") or "0",
            "taxes": find_text(".//TaxAmount") or "0",
            "total": find_text(".//TotalAmount") or find_text(".//Total") or "0",
        }
    except Exception:
        return {}


def _field_value(raw_value: Any) -> str:
    if isinstance(raw_value, dict):
        for key in ("valueString", "stringValue", "valueDate", "dateValue", "valueDateTime", "dateTimeValue", "content", "text", "value"):
            if key in raw_value:
                return _stringify(raw_value[key])
        for key in ("valueNumber", "numberValue", "valueBoolean", "booleanValue"):
            if key in raw_value:
                return _stringify(raw_value[key])
        for key in ("valueObject", "valueArray", "values"):
            if key in raw_value:
                return _field_value(raw_value[key])
        for key in ("Amount", "amount", "value", "content", "text"):
            if key in raw_value:
                return _field_value(raw_value[key])
        if "values" in raw_value:
            return _stringify(raw_value["values"])
        return _stringify(raw_value)
    if isinstance(raw_value, list):
        if not raw_value:
            return ""
        first_value = raw_value[0]
        if isinstance(first_value, dict):
            return _field_value(first_value)
        return _stringify(raw_value)
    return _stringify(raw_value)


def _raw_key_from_line(line: str) -> str:
    if ":" not in line:
        return ""
    return line.split(":", 1)[0].strip().lower()


def _apply_text_field_aliases(payload: dict[str, Any], line: str) -> dict[str, Any]:
    key = _raw_key_from_line(line)
    value = _extract_text_from_line(line)
    if not value or not key:
        return payload

    invoice_keys = ("invoice", "invoice_number", "invoiceid", "invoice_id", "invoice_number_id", "folio")
    seller_keys = ("seller", "seller_name", "issuer", "issuer_name", "supplier", "supplier_name", "vendor", "vendor_name")
    customer_keys = ("customer", "customer_name", "buyer", "buyer_name", "client", "client_name")
    rfc_keys = ("rfc", "tax_id", "taxid", "issuer_tax_id", "issuertaxid", "seller_tax_id", "sellertaxid")
    address_keys = ("seller_address", "issuer_address", "supplier_address", "billing_address")

    if any(alias in key for alias in invoice_keys):
        payload["invoice_number"] = value or payload["invoice_number"]
    elif any(alias in key for alias in rfc_keys):
        payload["rfc"] = value or payload["rfc"]
    elif any(alias in key for alias in address_keys):
        payload["seller_address"] = value or payload["seller_address"]
    elif any(alias in key for alias in seller_keys) and ("name" in key or key in {"seller", "issuer", "supplier", "vendor"}):
        payload["seller_name"] = value or payload["seller_name"]
    elif any(alias in key for alias in customer_keys) and ("name" in key or key in {"customer", "buyer", "client"}):
        payload["customer_name"] = value or payload["customer_name"]
    elif "subtotal" in key:
        payload["subtotal"] = value or payload["subtotal"]
    elif "tax" in key and "total" not in key:
        payload["taxes"] = value or payload["taxes"]
    elif "total" in key and ("amount" in key or "total" in key):
        payload["total"] = value or payload["total"]

    return payload


def _normalize_content_understanding_result(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        for item in data:
            found = _normalize_content_understanding_result(item)
            if found:
                return found
        return {}

    if not isinstance(data, dict):
        return {}

    normalized: dict[str, Any] = {}

    field_sets: list[Any] = []
    for candidate_key in ("fields", "results", "analyzeResult", "document", "documents", "documentResults", "content", "items", "contents", "pages"):
        candidate = data.get(candidate_key)
        if candidate is not None:
            field_sets.append(candidate)

    for candidate in field_sets:
        if isinstance(candidate, (dict, list)):
            nested = _normalize_content_understanding_result(candidate)
            if nested:
                return nested

    items: list[tuple[str, Any]] = []
    if isinstance(data.get("fields"), dict):
        items.extend(data["fields"].items())
    elif isinstance(data.get("fields"), list):
        for entry in data["fields"]:
            if isinstance(entry, dict):
                items.extend(entry.items())
    else:
        for key, raw_value in data.items():
            if key.lower() in {"kind", "source", "span", "spans", "pages", "paragraphs", "tables", "sections", "metadata", "unit", "page_number", "startpagenumber", "endpagenumber", "confidence"}:
                continue
            items.append((key, raw_value))

    for key, raw_value in items:
        lower_key = str(key).lower()
        normalized_value = _field_value(raw_value)
        if lower_key in {"invoice_number", "invoicenumber", "invoiceid", "invoice_id", "folio_number", "folionumber", "issuer_invoice_id", "issuerinvoiceid", "documentid", "document_id", "invoiceidvalue"}:
            normalized["invoice_number"] = normalized_value
        elif lower_key in {"seller_name", "sellername", "issuer_name", "issuername", "supplier_name", "suppliername", "vendor_name", "vendorname", "issuer", "supplier", "vendor", "merchantname", "merchant_name", "company_name", "companyname"}:
            normalized["seller_name"] = normalized_value
        elif lower_key in {"rfc", "tax_id", "taxid", "seller_tax_id", "sellertaxid", "issuer_tax_id", "issuertaxid", "vendor_tax_id", "vendortaxid", "merchant_tax_id", "merchanttaxid", "customer_tax_id", "customertaxid", "taxpayerid", "taxpayer_id"}:
            normalized["rfc"] = normalized_value
        elif lower_key in {"customer_name", "customername", "buyer_name", "buyername", "client_name", "clientname", "customer", "buyer", "client", "recipient_name", "recipientname"}:
            normalized["customer_name"] = normalized_value
        elif lower_key in {"seller_address", "selleraddress", "issuer_address", "issueraddress", "supplier_address", "supplieraddress", "billing_address", "billingaddress", "merchantaddress", "merchant_address", "vendoraddress", "vendor_address", "customer_address", "customeraddress"}:
            normalized["seller_address"] = normalized_value
        elif lower_key in {"issue_date", "issuedate", "invoice_date", "invoicedate", "date", "emission_date", "emissiondate"}:
            normalized["issue_date"] = normalized_value
        elif lower_key in {"subtotal", "sub_total", "subtotalamount", "before_tax_total", "amount_subtotal", "subtotal_amount"}:
            normalized["subtotal"] = normalized_value
        elif lower_key in {"taxes", "tax_amount", "taxamount", "total_tax", "taxtotal", "tax_total", "taxamounttotal", "totaltaxamount", "VAT", "vat"}:
            normalized["taxes"] = normalized_value
        elif lower_key in {"total", "total_amount", "totalamount", "grand_total", "amount_total", "totalamountwithtax", "totalamountwithouttax"}:
            normalized["total"] = normalized_value
        elif lower_key in {"purchase_details", "purchase_details_json", "items", "line_items", "purchasedetails", "lineitems"}:
            normalized["purchase_details"] = raw_value

        if lower_key.endswith("name") and lower_key not in {"invoice_number", "seller_name", "rfc"} and not normalized.get("seller_name"):
            candidate = normalized_value
            if candidate and not re.search(r"\b(?:invoice|factura|folio|rfc|subtotal|total|iva)\b", candidate, flags=re.I):
                normalized["seller_name"] = candidate

    if not normalized.get("rfc"):
        for value in data.values():
            if isinstance(value, dict):
                candidate = value.get("valueString") or value.get("stringValue") or value.get("content")
                if isinstance(candidate, str) and re.fullmatch(r"[A-Z&Ñ]{3,4}[0-9]{6}[A-Z0-9]{3}", candidate.strip().upper()):
                    normalized["rfc"] = candidate.strip().upper()
                    break

    if not normalized:
        for nested in data.values():
            if isinstance(nested, (dict, list)):
                found = _normalize_content_understanding_result(nested)
                if found:
                    return found

    if "purchase_details" not in normalized and isinstance(data.get("fields"), dict) and "items" in data["fields"]:
        normalized["purchase_details"] = data["fields"].get("items")

    return normalized


def _extract_from_azure_content_understanding(file_path: Path) -> dict[str, Any]:
    if requests is None:
        return {}

    endpoint = AZURE_CONTENT_UNDERSTANDING_ENDPOINT.strip()
    api_key = AZURE_CONTENT_UNDERSTANDING_KEY.strip()
    if not endpoint or not api_key:
        return {}

    analyzer_id = AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID.strip() or "prebuilt-invoice"
    api_version = AZURE_CONTENT_UNDERSTANDING_API_VERSION.strip() or "2024-12-01-preview"
    url = f"{endpoint.rstrip('/')}/contentunderstanding/analyzers/{analyzer_id}:analyze?api-version={api_version}"

    try:
        with file_path.open("rb") as invoice_file:
            response = requests.post(
                url,
                headers={"Ocp-Apim-Subscription-Key": api_key, "Accept": "application/json"},
                files={"file": (file_path.name, invoice_file.read(), "application/octet-stream")},
                timeout=60,
            )
        response.raise_for_status()
        data = response.json()
        return _normalize_content_understanding_result(data)
    except Exception:
        return {}


def _extract_from_ocr(file_path: Path) -> dict[str, Any]:
    if RapidOCR is None:
        return {}

    try:
        ocr = RapidOCR()
        results, _ = ocr(str(file_path))
        if not results:
            return {}

        text_lines: list[str] = []
        for block in results:
            text = str(block[1]).strip()
            if text:
                text_lines.append(text)

        text_blob = "\n".join(text_lines)
        lines = [line.strip() for line in text_blob.splitlines() if line.strip()]

        payload: dict[str, Any] = {
            "invoice_number": "",
            "seller_name": "",
            "rfc": "",
            "customer_name": "",
            "seller_address": "",
            "issue_date": "",
            "purchase_details": {},
            "subtotal": "",
            "taxes": "",
            "total": "",
        }

        previous_line = ""
        for line in lines:
            normalized = _normalize_ocr_text(re.sub(r"\s+", " ", line).strip())
            lower = normalized.lower()

            if not payload["seller_name"] and previous_line and re.search(r"\brfc\b", normalized, flags=re.I):
                candidate = previous_line.strip()
                if candidate and not re.search(r"(?:factura|folio|fecha|cliente|direcci[oó]n|rfc)", candidate, flags=re.I):
                    payload["seller_name"] = _cleanup_name(candidate)

            match = re.search(r"(?:no\s+de\s+)?(?:folio|invoice|factura)\s*(?:no|num|number|id)?\s*[:#-]?\s*([A-Z0-9-]+(?:[/-][A-Z0-9-]+)*)\b", normalized, flags=re.I)
            if match and not payload["invoice_number"]:
                captured = match.group(1).strip().upper()
                if captured and captured.lower() not in {"factura", "invoice", "folio"}:
                    payload["invoice_number"] = captured

            if payload["invoice_number"].upper() in {"INVOICE", "FACTURA", "FOLIO"}:
                payload["invoice_number"] = ""

            match = re.search(r"\bINV[-: ]?[A-Z0-9-]+\b", normalized, flags=re.I)
            if match and not payload["invoice_number"]:
                payload["invoice_number"] = match.group(0).strip().upper()

            match = re.search(r"(?:seller|issuer|supplier|vendor)\s*[:#-]?\s*([A-Za-z0-9&./()'\- ][A-Za-z0-9&./()'\- ]+)", normalized, flags=re.I)
            if match and not payload["seller_name"]:
                payload["seller_name"] = _cleanup_name(re.sub(r"^[^A-Za-z0-9]+", "", match.group(1)).strip(" -:"))

            match = re.search(r"(?:cliente|customer|client|buyer)\s*[:#-]?\s*(.+)", normalized, flags=re.I)
            if match and not payload["customer_name"]:
                payload["customer_name"] = _cleanup_name(re.sub(r"^[^A-Za-z0-9]+", "", match.group(1)).strip(' "\''))

            match = re.search(r"(?:direcci[oó]n|address|domicilio)\s*[:#-]?\s*(.+)", normalized, flags=re.I)
            if match and not payload["seller_address"]:
                payload["seller_address"] = _cleanup_name(re.sub(r"^[^A-Za-z0-9]+", "", match.group(1)).strip(' "\''))

            match = re.search(r"(?:rfc|tax\s*id)\s*[:#-]?\s*([A-Z0-9]+)", normalized, flags=re.I)
            if match and not payload["rfc"]:
                payload["rfc"] = match.group(1).strip().upper()

            match = re.search(r"\b(?:subtotal|sub_total)\b\s*[:#-]?\s*([$]?[0-9][0-9,]*(?:\.\d{1,2})?)", normalized, flags=re.I)
            if match and not payload["subtotal"]:
                payload["subtotal"] = _clean_money(match.group(1))

            match = re.search(r"\b(?:iva|impuesto|tax(?:es)?|tax\s*amount)\b\s*(?:\([^)]*\))?\s*[:#-]?\s*([$]?[0-9][0-9,]*(?:\.\d{1,2})?)", normalized, flags=re.I)
            if match and not payload["taxes"]:
                payload["taxes"] = _clean_money(match.group(1))

            match = re.search(r"\b(?:total)\b\s*(?:amount|due)?\s*[:#-]?\s*([$]?[0-9][0-9,]*(?:\.\d{1,2})?)", normalized, flags=re.I)
            if match and not payload["total"]:
                payload["total"] = _clean_money(match.group(1))

            previous_line = normalized

        if not any(payload.values()):
            return {}

        return payload
    except Exception:
        return {}


def extract_invoice_data(file_path: Path) -> dict[str, Any]:
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    payload: dict[str, Any] = {
        "invoice_number": f"INV-{hashlib.sha256(file_path.name.encode('utf-8')).hexdigest()[:12]}",
        "seller_name": "Unknown seller",
        "rfc": "",
        "customer_name": "",
        "seller_address": "",
        "issue_date": "",
        "purchase_details": {},
        "subtotal": "0",
        "taxes": "0",
        "total": "0",
    }

    azure_data = _extract_from_azure_content_understanding(file_path)
    if azure_data:
        payload.update({key: value for key, value in azure_data.items() if value not in (None, "") or key in {"purchase_details"}})
        payload["invoice_number"] = payload.get("invoice_number") or file_path.stem.upper()
        payload["seller_name"] = payload.get("seller_name") or "Unknown seller"
        payload["purchase_details"] = payload.get("purchase_details") or {}
        payload["raw_data"] = {
            "source_file": str(file_path),
            "source_type": suffix.lstrip("."),
            "extracted": payload,
        }
        return payload

    if suffix == ".xml":
        xml_data = _extract_from_xml(file_path)
        if xml_data:
            payload.update(xml_data)
            payload["purchase_details"] = payload.get("purchase_details") or {}
            payload["raw_data"] = {
                "source_file": str(file_path),
                "source_type": suffix.lstrip("."),
                "extracted": payload,
            }
            return payload

    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        ocr_data = _extract_from_ocr(file_path)
        if ocr_data:
            payload.update({key: value for key, value in ocr_data.items() if value not in (None, "") or key in {"purchase_details"}})
            payload["invoice_number"] = payload.get("invoice_number") or file_path.stem.upper()
            payload["seller_name"] = payload.get("seller_name") or "Unknown seller"
            payload["purchase_details"] = payload.get("purchase_details") or {}
            payload["raw_data"] = {
                "source_file": str(file_path),
                "source_type": suffix.lstrip("."),
                "extracted": payload,
            }
            return payload

    text_blob = _safe_read_text(file_path)
    if text_blob:
        lines = [line.strip() for line in text_blob.splitlines() if line.strip()]
        previous_line = ""
        for line in lines:
            key = _raw_key_from_line(line)
            lower_line = line.lower()

            if "folio" in lower_line or "no de folio" in lower_line:
                match = re.search(r"(?:no\s+de\s+)?(?:folio|invoice|factura)\s*(?:no|num|number|id)?\s*[:#-]?\s*([A-Z0-9-]+(?:[/-][A-Z0-9-]+)*)\b", line, flags=re.I)
                if match:
                    payload["invoice_number"] = match.group(1).strip().upper()

            payload = _apply_text_field_aliases(payload, line)

            if key in {"invoice", "invoice_number", "invoice_id", "invoiceid", "folio"} or ("invoice" in lower_line and ("number" in lower_line or "id" in lower_line or "folio" in lower_line)):
                value = _extract_text_from_line(line)
                if value:
                    payload["invoice_number"] = value.replace("FOLIO:", "").replace("FACTURA:", "").strip()
            if key in {"seller", "seller_name", "issuer", "issuer_name", "supplier", "supplier_name", "vendor", "vendor_name"}:
                payload["seller_name"] = _extract_text_from_line(line) or payload["seller_name"]
            if key in {"customer", "customer_name", "buyer", "buyer_name", "client", "client_name"}:
                payload["customer_name"] = _extract_text_from_line(line) or payload["customer_name"]
            if key in {"rfc", "tax_id", "taxid", "issuer_tax_id", "issuer_taxid", "seller_tax_id", "sellertaxid"} or "rfc" in lower_line or "tax_id" in lower_line or "taxid" in lower_line:
                payload["rfc"] = _extract_text_from_line(line) or payload["rfc"]
            if key in {"seller_address", "issuer_address", "supplier_address", "billing_address"} or ("address" in lower_line and ("seller" in lower_line or "issuer" in lower_line or "supplier" in lower_line)):
                payload["seller_address"] = _extract_text_from_line(line) or payload["seller_address"]
            if "subtotal" in lower_line:
                payload["subtotal"] = _clean_money(_extract_text_from_line(line)) or payload["subtotal"]
            if ("taxes" in lower_line or "tax_amount" in lower_line or "taxamount" in lower_line) and "tax_id" not in lower_line and "taxid" not in lower_line and "rfc" not in lower_line:
                payload["taxes"] = _clean_money(_extract_text_from_line(line)) or payload["taxes"]
            if "total" in lower_line and "amount" in lower_line:
                payload["total"] = _clean_money(_extract_text_from_line(line)) or payload["total"]
            if "total" in lower_line and "tax" not in lower_line and "amount" not in lower_line:
                payload["total"] = _clean_money(_extract_text_from_line(line)) or payload["total"]

            if not payload["seller_name"] and previous_line and not re.search(r"(?:factura|folio|fecha|cliente|direcci[oó]n|rfc)", previous_line, flags=re.I):
                payload["seller_name"] = previous_line.strip()

            previous_line = line.strip()

    default_invoice_number = f"INV-{hashlib.sha256(file_path.name.encode('utf-8')).hexdigest()[:12]}"
    if payload["invoice_number"] == default_invoice_number and "-" in file_path.stem:
        payload["invoice_number"] = file_path.stem.upper()

    if payload["seller_name"] == "Unknown seller":
        payload["seller_name"] = file_path.stem.replace("-", " ").title() or "Unknown seller"

    payload["seller_name"] = _cleanup_name(payload.get("seller_name") or "")
    payload["customer_name"] = _cleanup_name(payload.get("customer_name") or "")
    payload["seller_address"] = _cleanup_name(payload.get("seller_address") or "")

    payload["purchase_details"] = payload.get("purchase_details") or {}
    payload["raw_data"] = {
        "source_file": str(file_path),
        "source_type": suffix.lstrip("."),
        "extracted": {key: value for key, value in payload.items() if key != "raw_data"},
    }
    return payload


def evaluate_invoice_data(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    existing_map = existing or {}
    candidate_map = candidate or {}

    same_invoice_number = _normalize(existing_map.get("invoice_number") or existing_map.get("invoice_id")) == _normalize(candidate_map.get("invoice_number") or candidate_map.get("invoice_id"))
    same_seller = _normalize(existing_map.get("seller_name") or existing_map.get("issuer_name")) == _normalize(candidate_map.get("seller_name") or candidate_map.get("issuer_name"))

    if same_invoice_number and same_seller:
        existing_fields = {
            "invoice_number": existing_map.get("invoice_number") or existing_map.get("invoice_id"),
            "seller_name": existing_map.get("seller_name") or existing_map.get("issuer_name"),
            "rfc": existing_map.get("rfc") or existing_map.get("issuer_tax_id"),
            "customer_name": existing_map.get("customer_name"),
            "seller_address": existing_map.get("seller_address"),
            "issue_date": existing_map.get("issue_date"),
            "purchase_details": existing_map.get("purchase_details"),
            "subtotal": existing_map.get("subtotal"),
            "taxes": existing_map.get("taxes") or existing_map.get("tax_amount"),
            "total": existing_map.get("total") or existing_map.get("total_amount"),
        }
        candidate_fields = {
            "invoice_number": candidate_map.get("invoice_number") or candidate_map.get("invoice_id"),
            "seller_name": candidate_map.get("seller_name") or candidate_map.get("issuer_name"),
            "rfc": candidate_map.get("rfc") or candidate_map.get("issuer_tax_id"),
            "customer_name": candidate_map.get("customer_name"),
            "seller_address": candidate_map.get("seller_address"),
            "issue_date": candidate_map.get("issue_date"),
            "purchase_details": candidate_map.get("purchase_details"),
            "subtotal": candidate_map.get("subtotal"),
            "taxes": candidate_map.get("taxes") or candidate_map.get("tax_amount"),
            "total": candidate_map.get("total") or candidate_map.get("total_amount"),
        }

        if existing_fields == candidate_fields:
            return {"decision": "duplicate", "reason": "Same invoice number and seller with identical data."}

        return {"decision": "update", "reason": "Same invoice number and seller but different data requires approval."}

    return {"decision": "new", "reason": "No duplicate candidate found."}
