from __future__ import annotations

import streamlit as st
from sqlalchemy import select

from config import APP_NAME
from database.db import AuditLog, Invoice, Setting, User, init_db
from services.ai_service import evaluate_invoice_data
from services.audit_service import log_event
from services.auth_service import authenticate_user, change_password, create_default_admin, register_user
from services.invoice_service import approve_invoice_update, get_audit_logs, get_dashboard_summary, get_settings, list_invoices, list_users, preview_uploaded_invoice, process_uploaded_invoice, set_setting

st.set_page_config(page_title=APP_NAME, page_icon="🧾", layout="wide")


init_db()
create_default_admin()


if "user" not in st.session_state:
    st.session_state.user = None


def logout() -> None:
    st.session_state.user = None


if st.session_state.user is None:
    st.title("Invoice Digitalizer")
    st.caption("AI invoice digitization, validation, and auditing.")

    login_tab, register_tab = st.tabs(["Sign in", "Register user"])

    with login_tab:
        username = st.text_input("User", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Enter", key="login_submit"):
            user = authenticate_user(username.strip(), password)
            if user is None:
                st.error("Invalid credentials or inactive user.")
            else:
                st.session_state.user = user
                st.success("Session started.")
                st.rerun()

    with register_tab:
        new_username = st.text_input("New username", key="register_username")
        email = st.text_input("Email", key="register_email")
        full_name = st.text_input("Full name", key="register_full_name")
        new_password = st.text_input("Password", type="password", key="register_password")
        if st.button("Create account", key="register_submit"):
            try:
                if not new_username or not email or not full_name or not new_password:
                    st.warning("Please complete all fields.")
                else:
                    register_user(new_username, email, new_password, full_name)
                    st.success("User registered successfully.")
            except ValueError as exc:
                st.error(str(exc))

    st.stop()

user = st.session_state.user

st.sidebar.title("Menu")
st.sidebar.write(f"User: {user['full_name']}")
st.sidebar.write(f"Role: {user['role']}")
if st.sidebar.button("Log out"):
    logout()
    st.rerun()

st.title(f"Welcome, {user['full_name']}")

overview, file_upload = st.tabs(["Overview", "Invoice upload"])

with overview:
    summary = get_dashboard_summary()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Invoices", summary["invoices"])
    col2.metric("Users", summary["users"])
    col3.metric("Duplicates", summary["duplicates"])
    col4.metric("Pending", summary["pending"])

    invoices = list_invoices()
    if invoices:
        import pandas as pd

        rows = []
        for invoice in invoices:
            rows.append(
                {
                    "ID": invoice.id,
                    "Invoice": invoice.invoice_number or invoice.invoice_id,
                    "Seller": invoice.seller_name or invoice.issuer_name,
                    "Customer": invoice.customer_name,
                    "Total": invoice.total or invoice.total_amount,
                    "Status": invoice.status,
                    "Validation": invoice.validation_status,
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("No invoice records have been registered yet.")

with file_upload:
    uploaded_file = st.file_uploader("Select an invoice file", type=["pdf", "xml", "png", "jpg", "jpeg", "txt"])
    if uploaded_file is not None:
        preview = preview_uploaded_invoice(uploaded_file)
        extracted = preview["extracted"]
        st.subheader("Review extracted data before registration")
        with st.form("invoice_review_form"):
            fields = [
                ("invoice_number", "Invoice number"),
                ("seller_name", "Seller name"),
                ("rfc", "RFC"),
                ("customer_name", "Customer name"),
                ("seller_address", "Seller address"),
                ("issue_date", "Issue date"),
                ("subtotal", "Subtotal"),
                ("taxes", "Taxes"),
                ("total", "Total"),
            ]
            edited_data = {}
            for key, label in fields:
                value = extracted.get(key) or ""
                edited_data[key] = st.text_input(label, value=str(value), key=f"review_{key}_{uploaded_file.name}")

            if st.form_submit_button("Register invoice"):
                result = process_uploaded_invoice(uploaded_file, user["id"], edited_data=edited_data)
                if result["status"] == "approved":
                    st.success(result["message"])
                elif result["status"] == "rejected":
                    st.warning(result["message"])
                else:
                    st.warning(result["message"])
                st.json(result)
                st.rerun()

            st.caption("Make any necessary corrections before the data is saved to the database.")

admin_tab, history_tab, config_tab, profile_tab = st.tabs(["Users", "Audit log", "Settings", "Profile"])

with admin_tab:
    if user["role"] == "admin":
        users = list_users()
        if users:
            import pandas as pd

            rows = []
            for person in users:
                rows.append(
                    {
                        "ID": person.id,
                        "User": person.username,
                        "Name": person.full_name,
                        "Email": person.email,
                        "Role": person.role,
                        "Active": person.is_active,
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

        with st.form("create_user_form"):
            st.subheader("Create user")
            new_u = st.text_input("User")
            new_email = st.text_input("Email")
            new_name = st.text_input("Full name")
            new_pass = st.text_input("Password", type="password")
            role = st.selectbox("Role", ["user", "admin"])
            submitted = st.form_submit_button("Save")
            if submitted:
                try:
                    register_user(new_u, new_email, new_pass, new_name, role)
                    st.success("User created successfully.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        st.warning("Only administrators can manage users.")

with history_tab:
    logs = get_audit_logs()
    if logs:
        import pandas as pd

        rows = []
        for record in logs:
            rows.append(
                {
                    "ID": record.id,
                    "Action": record.action,
                    "Module": record.module,
                    "User": record.user_id,
                    "Entity": record.entity_type,
                    "Date": record.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("No audit events recorded.")

with config_tab:
    if user["role"] == "admin":
        settings = get_settings()
        for setting in settings:
            with st.form(f"setting_{setting.id}"):
                st.write(f"{setting.key} - {setting.description or 'No description'}")
                new_value = st.text_input(f"Value for {setting.key}", value=setting.value, key=f"value_{setting.id}")
                if st.form_submit_button("Save", key=f"save_{setting.id}"):
                    set_setting(setting.key, new_value, setting.description)
                    st.success(f"Setting saved: {setting.key}")
                    st.rerun()
    else:
        st.warning("Only administrators can configure the system.")

with profile_tab:
    st.subheader("Change password")
    old_password = st.text_input("Current password", type="password", key="profile_old_password")
    new_password = st.text_input("New password", type="password", key="profile_new_password")
    confirm_password = st.text_input("Confirm new password", type="password", key="profile_confirm_password")
    if st.button("Update password", key="profile_update_password"):
        if not old_password or not new_password:
            st.warning("Please complete all fields.")
        elif new_password != confirm_password:
            st.warning("The new password and confirmation do not match.")
        else:
            if change_password(user["id"], old_password, new_password):
                st.success("Password updated successfully.")
                log_event(user["id"], "password_changed", "security", entity_type="user", entity_id=user["id"], details={"status": "success"})
            else:
                st.error("Current password is incorrect.")
