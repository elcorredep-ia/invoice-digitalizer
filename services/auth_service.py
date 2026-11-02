from __future__ import annotations

from datetime import datetime, timezone

import bcrypt
from sqlalchemy import select

from database.db import SessionLocal, User


def _to_user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_default_admin() -> None:
    with SessionLocal() as session:
        admin = session.execute(select(User).where(User.username == "admin")).scalar_one_or_none()
        if admin is None:
            session.add(
                User(
                    username="admin",
                    email="admin@invoice.local",
                    password_hash=hash_password("Admin123!"),
                    full_name="Administrator",
                    role="admin",
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.commit()


def register_user(username: str, email: str, password: str, full_name: str, role: str = "user") -> dict:
    with SessionLocal() as session:
        existing = session.execute(select(User).where((User.username == username) | (User.email == email))).scalar_one_or_none()
        if existing:
            raise ValueError("El usuario o correo ya existe")

        new_user = User(
            username=username.strip(),
            email=email.strip(),
            password_hash=hash_password(password),
            full_name=full_name.strip(),
            role=role,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
        return _to_user_dict(new_user)


def authenticate_user(username: str, password: str):
    with SessionLocal() as session:
        user = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        user.last_login_at = datetime.now(timezone.utc)
        user.updated_at = datetime.now(timezone.utc)
        session.commit()
        return _to_user_dict(user)


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            return False
        if not verify_password(old_password, user.password_hash):
            return False
        user.password_hash = hash_password(new_password)
        user.updated_at = datetime.now(timezone.utc)
        session.commit()
        return True
