# Invoice Digitalizer

Web application for invoice digitization, duplicate detection, and intelligent query.

## Features

- User authentication and authorization
- Invoice upload (PDF, XML, images)
- AI-based extraction simulation and validation layer
- Duplicate and update detection by invoice id + issuer
- Audit logging
- Admin user management and settings

## Run locally

1. Create a virtual environment and install dependencies:
   - `python -m venv .venv`
   - `.venv\Scripts\activate`
   - `python -m pip install -r requirements.txt`
2. Copy `.env.example` to `.env` if needed.
3. Start the app:
   - `streamlit run app.py`

## Default admin user

The app creates a default admin user automatically if none exists:

- username: `admin`
- password: `Admin123!`

Change the password after the first login.
