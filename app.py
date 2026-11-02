from __future__ import annotations

import streamlit as st
from sqlalchemy import select

from config import APP_NAME
from database.db import AuditLog, Invoice, Setting, User, init_db
from services.ai_service import evaluate_invoice_data
from services.audit_service import log_event
from services.auth_service import authenticate_user, change_password, create_default_admin, register_user
from services.invoice_service import approve_invoice_update, get_audit_logs, get_dashboard_summary, get_settings, list_invoices, list_users, process_uploaded_invoice, set_setting

st.set_page_config(page_title=APP_NAME, page_icon="🧾", layout="wide")


init_db()
create_default_admin()


if "user" not in st.session_state:
    st.session_state.user = None


def logout() -> None:
    st.session_state.user = None


if st.session_state.user is None:
    st.title("Invoice Digitalizer")
    st.caption("Digitalización de facturas con IA, validación y auditoría.")

    login_tab, register_tab = st.tabs(["Iniciar sesión", "Registrar usuario"])

    with login_tab:
        username = st.text_input("Usuario", key="login_username")
        password = st.text_input("Contraseña", type="password", key="login_password")
        if st.button("Entrar", key="login_submit"):
            user = authenticate_user(username.strip(), password)
            if user is None:
                st.error("Credenciales inválidas o usuario inactivo.")
            else:
                st.session_state.user = user
                st.success("Sesión iniciada.")
                st.rerun()

    with register_tab:
        new_username = st.text_input("Usuario nuevo", key="register_username")
        email = st.text_input("Correo electrónico", key="register_email")
        full_name = st.text_input("Nombre completo", key="register_full_name")
        new_password = st.text_input("Contraseña", type="password", key="register_password")
        if st.button("Crear cuenta", key="register_submit"):
            try:
                if not new_username or not email or not full_name or not new_password:
                    st.warning("Completa todos los campos.")
                else:
                    register_user(new_username, email, new_password, full_name)
                    st.success("Usuario registrado correctamente.")
            except ValueError as exc:
                st.error(str(exc))

    st.stop()

user = st.session_state.user

st.sidebar.title("Menú")
st.sidebar.write(f"Usuario: {user['full_name']}")
st.sidebar.write(f"Rol: {user['role']}")
if st.sidebar.button("Cerrar sesión"):
    logout()
    st.rerun()

st.title(f"Bienvenido, {user['full_name']}")

overview, file_upload = st.tabs(["Resumen", "Carga de factura"])

with overview:
    summary = get_dashboard_summary()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Facturas", summary["invoices"])
    col2.metric("Usuarios", summary["users"])
    col3.metric("Duplicados", summary["duplicates"])
    col4.metric("Pendientes", summary["pending"])

    invoices = list_invoices()
    if invoices:
        import pandas as pd

        rows = []
        for invoice in invoices:
            rows.append(
                {
                    "ID": invoice.id,
                    "Factura": invoice.invoice_id,
                    "Emisor": invoice.issuer_name,
                    "Cliente": invoice.customer_name,
                    "Total": invoice.total_amount,
                    "Estado": invoice.status,
                    "Validación": invoice.validation_status,
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("No existen facturas registradas todavía.")

with file_upload:
    uploaded_file = st.file_uploader("Selecciona un archivo de factura", type=["pdf", "xml", "png", "jpg", "jpeg", "txt"])
    if uploaded_file is not None:
        result = process_uploaded_invoice(uploaded_file, user["id"])
        if result["status"] == "approved":
            st.success(result["message"])
        elif result["status"] == "rejected":
            st.warning(result["message"])
        else:
            st.warning(result["message"])

        st.json(result)

admin_tab, history_tab, config_tab, profile_tab = st.tabs(["Usuarios", "Auditoría", "Configuración", "Perfil"])

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
                        "Usuario": person.username,
                        "Nombre": person.full_name,
                        "Correo": person.email,
                        "Rol": person.role,
                        "Activo": person.is_active,
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

        with st.form("create_user_form"):
            st.subheader("Crear usuario")
            new_u = st.text_input("Usuario")
            new_email = st.text_input("Correo")
            new_name = st.text_input("Nombre completo")
            new_pass = st.text_input("Contraseña", type="password")
            role = st.selectbox("Rol", ["user", "admin"])
            submitted = st.form_submit_button("Guardar")
            if submitted:
                try:
                    register_user(new_u, new_email, new_pass, new_name, role)
                    st.success("Usuario creado correctamente.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        st.warning("Solo los administradores pueden gestionar usuarios.")

with history_tab:
    logs = get_audit_logs()
    if logs:
        import pandas as pd

        rows = []
        for record in logs:
            rows.append(
                {
                    "ID": record.id,
                    "Acción": record.action,
                    "Módulo": record.module,
                    "Usuario": record.user_id,
                    "Entidad": record.entity_type,
                    "Fecha": record.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("Sin eventos de auditoría.")

with config_tab:
    if user["role"] == "admin":
        settings = get_settings()
        for setting in settings:
            with st.form(f"setting_{setting.id}"):
                st.write(f"{setting.key} - {setting.description or 'Sin descripción'}")
                new_value = st.text_input(f"Valor de {setting.key}", value=setting.value, key=f"value_{setting.id}")
                if st.form_submit_button("Guardar", key=f"save_{setting.id}"):
                    set_setting(setting.key, new_value, setting.description)
                    st.success(f"Configuración guardada: {setting.key}")
                    st.rerun()
    else:
        st.warning("Solo los administradores pueden configurar el sistema.")

with profile_tab:
    st.subheader("Cambiar contraseña")
    old_password = st.text_input("Contraseña actual", type="password", key="profile_old_password")
    new_password = st.text_input("Nueva contraseña", type="password", key="profile_new_password")
    confirm_password = st.text_input("Confirmar nueva contraseña", type="password", key="profile_confirm_password")
    if st.button("Actualizar contraseña", key="profile_update_password"):
        if not old_password or not new_password:
            st.warning("Completa todos los campos.")
        elif new_password != confirm_password:
            st.warning("La nueva contraseña y la confirmación no coinciden.")
        else:
            if change_password(user["id"], old_password, new_password):
                st.success("Contraseña actualizada correctamente.")
                log_event(user["id"], "password_changed", "security", entity_type="user", entity_id=user["id"], details={"status": "success"})
            else:
                st.error("La contraseña actual es incorrecta.")
