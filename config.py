from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

APP_NAME = os.getenv("APP_NAME", "Invoice Digitalizer")
APP_ENV = os.getenv("APP_ENV", "development")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/invoice_db.sqlite3")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
AZURE_AI_ENDPOINT = os.getenv("AZURE_AI_ENDPOINT", "")
AZURE_AI_API_KEY = os.getenv("AZURE_AI_API_KEY", "")
AZURE_AI_MODEL = os.getenv("AZURE_AI_MODEL", "gpt-4o-mini")
