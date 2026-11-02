from __future__ import annotations

import streamlit as st


def require_admin() -> bool:
    if "user" not in st.session_state or st.session_state.user is None:
        return False
    return st.session_state.user.get("role") == "admin"
