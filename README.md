# Invoice Digitalizer

Web application for invoice digitization using Azure Content Understanding, duplicate detection, and audit-ready validation.

## Features

- User authentication and authorization
- Invoice upload (PDF, XML, images, and text)
- Azure Content Understanding invoice extraction
- Standardized English invoice schema: seller name, customer name, RFC, seller address, invoice number, issue date, purchase details JSON, subtotal, taxes, and total
- Duplicate and update detection by invoice number + seller name
- Audit logging
- Admin user management and settings

## Run locally

1. Create a virtual environment and install dependencies:
   - `python -m venv .venv`
   - `.venv\Scripts\activate`
   - `python -m pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and configure the Azure Content Understanding variables.
3. Start the app:
   - `streamlit run app.py`

## Default admin user

The app creates a default admin user automatically if none exists:

- username: `admin`
- password: `Admin123!`

Change the password after the first login.
