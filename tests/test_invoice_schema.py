import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from database.db import AuditLog, Invoice, InvoiceFile, Setting, User, SessionLocal, init_db
from services.ai_service import extract_invoice_data
from services.auth_service import register_user
from services.invoice_service import process_uploaded_invoice


class InvoiceSchemaTests(unittest.TestCase):
    def setUp(self):
        with SessionLocal() as session:
            session.query(InvoiceFile).delete()
            session.query(Invoice).delete()
            session.query(AuditLog).delete()
            session.query(Setting).delete()
            session.query(User).delete()
            session.commit()
        init_db()

    def test_extract_invoice_data_uses_english_fields(self):
        with TemporaryDirectory() as temp_dir:
            invoice_path = Path(temp_dir) / "invoice-1001.txt"
            invoice_path.write_text(
                """invoice_number: INV-1001
seller_name: Fabricantes del Norte
customer_name: Cliente A
rfc: ABC010101XYZ
seller_address: Av. Reforma 123, CDMX
subtotal: 250.00
taxes: 25.00
total: 275.00
""".strip(),
                encoding="utf-8",
            )

            payload = extract_invoice_data(invoice_path)

            self.assertEqual(payload["invoice_number"], "INV-1001")
            self.assertEqual(payload["seller_name"], "Fabricantes del Norte")
            self.assertEqual(payload["customer_name"], "Cliente A")
            self.assertEqual(payload["rfc"], "ABC010101XYZ")
            self.assertEqual(payload["seller_address"], "Av. Reforma 123, CDMX")
            self.assertEqual(payload["subtotal"], "250.00")
            self.assertEqual(payload["taxes"], "25.00")
            self.assertEqual(payload["total"], "275.00")

    def test_extract_invoice_data_accepts_legacy_invoice_aliases(self):
        with TemporaryDirectory() as temp_dir:
            invoice_path = Path(temp_dir) / "invoice-1001.txt"
            invoice_path.write_text(
                """invoice_id: INV-1001
issuer: Contoso
issuer_tax_id: RFC001
customer: Cliente A
total_amount: 350.00
""".strip(),
                encoding="utf-8",
            )

            payload = extract_invoice_data(invoice_path)

            self.assertEqual(payload["invoice_number"], "INV-1001")
            self.assertEqual(payload["seller_name"], "Contoso")
            self.assertEqual(payload["customer_name"], "Cliente A")
            self.assertEqual(payload["rfc"], "RFC001")
            self.assertEqual(payload["taxes"], "0")
            self.assertEqual(payload["total"], "350.00")

    def test_extract_invoice_data_from_image_ocr(self):
        with TemporaryDirectory() as temp_dir:
            invoice_path = Path(temp_dir) / "invoice_image.png"
            image = Image.new("RGB", (1400, 900), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default()
            lines = [
                "Invoice",
                "INV-2002",
                "Seller: Fabrica del Norte",
                "RFC: ABC010101XYZ",
                "Customer: Cliente Z",
                "Subtotal: 320.00",
                "Taxes: 32.00",
                "Total: 352.00",
            ]
            y = 80
            for line in lines:
                draw.text((80, y), line, fill="black", font=font)
                y += 60
            image.save(invoice_path)

            payload = extract_invoice_data(invoice_path)

            self.assertEqual(payload["invoice_number"], "INV-2002")
            self.assertEqual(payload["seller_name"], "Fabrica del Norte")
            self.assertEqual(payload["customer_name"], "Cliente Z")
            self.assertEqual(payload["rfc"], "ABC010101XYZ")
            self.assertEqual(payload["total"], "352.00")

    def test_extract_invoice_data_from_realistic_mx_invoice_ocr(self):
        with TemporaryDirectory() as temp_dir:
            invoice_path = Path(temp_dir) / "invoice_real.png"
            image = Image.new("RGB", (1400, 1200), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default()
            lines = [
                "DISTRIBUIDORA DE LIMPIEZA DEL ESTELLO S.A. DE C.V.",
                "RFC: TBA890512XYZ",
                "Factura",
                "No de folio: F-12053",
                "Fecha: 15 de Octubre de 2026",
                'Cliente: Tienda de Abarrotes "La Esquina"',
                "DIRECCIÓN: Calle Emilio Zapata 45, Col. Centro, CDMX, C.P. 06000",
                "No  Cant. Producto P. Unitario Total",
                "1 15 Garrafas de Cloro (5 L) $130.00 c/u $1,950.00",
                "Subtotal: $5,591.00 M.N.",
                "IVA (16%): $894.56 M.N.",
                "TOTAL: $6,485.56 M.N.",
            ]
            y = 80
            for line in lines:
                draw.text((80, y), line, fill="black", font=font)
                y += 60
            image.save(invoice_path)

            payload = extract_invoice_data(invoice_path)

            self.assertEqual(payload["invoice_number"], "F-12053")
            self.assertEqual(payload["seller_name"], "DISTRIBUIDORA DE LIMPIEZA DEL ESTELLO S.A. DE C.V.")
            self.assertEqual(payload["customer_name"], 'Tienda de Abarrotes "La Esquina"')
            self.assertEqual(payload["rfc"], "TBA890512XYZ")
            self.assertEqual(payload["subtotal"], "5591.00")
            self.assertEqual(payload["taxes"], "894.56")
            self.assertEqual(payload["total"], "6485.56")

    def test_extract_invoice_data_from_azure_content_understanding_payload(self):
        azure_payload = {
            "fields": {
                "InvoiceId": {"valueString": "F-12053"},
                "MerchantName": {"valueString": "DISTRIBUIDORA DE LIMPIEZA DEL ESTELLO S.A. DE C.V."},
                "CustomerName": {"valueString": 'Tienda de Abarrotes "La Esquina"'},
                "SellerTaxId": {"valueString": "TBA890512XYZ"},
                "Subtotal": {"valueString": "$5,591.00"},
                "TaxAmount": {"valueString": "$894.56"},
                "Total": {"valueString": "$6,485.56"},
            }
        }

        normalized = extract_invoice_data.__globals__["_normalize_content_understanding_result"](azure_payload)

        self.assertEqual(normalized["invoice_number"], "F-12053")
        self.assertEqual(normalized["seller_name"], "DISTRIBUIDORA DE LIMPIEZA DEL ESTELLO S.A. DE C.V.")
        self.assertEqual(normalized["customer_name"], 'Tienda de Abarrotes "La Esquina"')
        self.assertEqual(normalized["rfc"], "TBA890512XYZ")
        self.assertEqual(normalized["subtotal"], "$5,591.00")
        self.assertEqual(normalized["taxes"], "$894.56")
        self.assertEqual(normalized["total"], "$6,485.56")

    def test_extract_invoice_data_from_azure_content_understanding_contents_list(self):
        azure_payload = {
            "contents": [
                {
                    "kind": "document",
                    "fields": {
                        "InvoiceId": {"valueString": "A-4859"},
                        "VendorName": {"valueString": "DISTRIBUIDORA DE LIMPIEZA EL DESTELLO, S.A. DE C.V."},
                        "CustomerName": {"valueString": "ABARROTES LA ESPERANZA"},
                        "VendorTaxId": {"valueString": "DLD150824K21"},
                        "CustomerAddress": {"valueString": "Calle Ignacio Zaragoza 45, Col. Centro, Iztapalapa, CDMX, C.P. 09000"},
                        "InvoiceDate": {"valueString": "2026-10-12"},
                        "SubtotalAmount": {"valueObject": {"Amount": {"valueNumber": 3310.0}}},
                        "TotalTaxAmount": {"valueObject": {"Amount": {"valueNumber": 529.6}}},
                        "TotalAmount": {"valueObject": {"Amount": {"valueNumber": 3839.6}}},
                    },
                }
            ]
        }

        normalized = extract_invoice_data.__globals__["_normalize_content_understanding_result"](azure_payload)

        self.assertEqual(normalized["invoice_number"], "A-4859")
        self.assertEqual(normalized["seller_name"], "DISTRIBUIDORA DE LIMPIEZA EL DESTELLO, S.A. DE C.V.")
        self.assertEqual(normalized["customer_name"], "ABARROTES LA ESPERANZA")
        self.assertEqual(normalized["rfc"], "DLD150824K21")
        self.assertEqual(normalized["issue_date"], "2026-10-12")
        self.assertEqual(normalized["subtotal"], "3310.0")
        self.assertEqual(normalized["taxes"], "529.6")
        self.assertEqual(normalized["total"], "3839.6")

    def test_extract_invoice_data_from_azure_document_results_payload(self):
        azure_payload = {
            "analyzeResult": {
                "documents": [
                    {
                        "fields": {
                            "InvoiceId": {"valueString": "A-4859"},
                            "VendorName": {"valueString": "DISTRIBUIDORA DE LIMPIEZA DEL ESTELLO S.A. DE C.V."},
                            "CustomerName": {"valueString": "ABARROTES LA ESPERANZA"},
                            "VendorTaxId": {"valueString": "DLD150824K21"},
                            "InvoiceDate": {"valueDate": "2026-10-12"},
                            "SubtotalAmount": {"valueObject": {"Amount": {"valueNumber": 3310.0}}},
                            "TotalTaxAmount": {"valueObject": {"Amount": {"valueNumber": 529.6}}},
                            "TotalAmount": {"valueObject": {"Amount": {"valueNumber": 3839.6}}},
                        }
                    }
                ]
            }
        }

        normalized = extract_invoice_data.__globals__["_normalize_content_understanding_result"](azure_payload)

        self.assertEqual(normalized["invoice_number"], "A-4859")
        self.assertEqual(normalized["seller_name"], "DISTRIBUIDORA DE LIMPIEZA DEL ESTELLO S.A. DE C.V.")
        self.assertEqual(normalized["customer_name"], "ABARROTES LA ESPERANZA")
        self.assertEqual(normalized["rfc"], "DLD150824K21")
        self.assertEqual(normalized["issue_date"], "2026-10-12")
        self.assertEqual(normalized["subtotal"], "3310.0")
        self.assertEqual(normalized["taxes"], "529.6")
        self.assertEqual(normalized["total"], "3839.6")

    def test_process_uploaded_invoice_uses_edited_values_before_registering(self):
        init_db()

        with TemporaryDirectory() as temp_dir:
            invoice_path = Path(temp_dir) / "invoice-1002.txt"
            invoice_path.write_text(
                """invoice_number: INV-1002
seller_name: Fabricantes del Norte
customer_name: Cliente B
rfc: ABC010101XYZ
seller_address: Av. Reforma 456, CDMX
subtotal: 100.00
taxes: 10.00
total: 110.00
""".strip(),
                encoding="utf-8",
            )

            unique_suffix = uuid4().hex[:8]
            user = register_user(f"reviewer_{unique_suffix}", f"reviewer_{unique_suffix}@example.com", "Password123!", "Reviewer")
            uploaded = io.BytesIO(invoice_path.read_bytes())
            uploaded.name = invoice_path.name

            result = process_uploaded_invoice(
                uploaded,
                user["id"],
                edited_data={
                    "invoice_number": "INV-1002-EDITED",
                    "seller_name": "Fabricantes del Sur",
                    "subtotal": "150.00",
                    "taxes": "15.00",
                    "total": "165.00",
                },
            )

            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["invoice_number"], "INV-1002-EDITED")

            with SessionLocal() as session:
                invoice = session.query(Invoice).filter_by(invoice_number="INV-1002-EDITED").one()
                self.assertEqual(invoice.seller_name, "Fabricantes del Sur")
                self.assertEqual(invoice.subtotal, "150.00")
                self.assertEqual(invoice.total, "165.00")


if __name__ == "__main__":
    unittest.main()
