"""Auth endpoints — login, me, change-password."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cortex.auth import authenticate, create_token, hash_password, verify_password
from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/login")
async def login(req: LoginRequest):
    conn = _h._db()
    user = authenticate(conn, req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": create_token(user["id"], user["username"]), "user": user}


@router.get("/auth/me")
async def me(admin: dict = Depends(require_admin)):
    return {"id": admin["sub"], "username": admin["username"]}


@router.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, admin: dict = Depends(require_admin)):
    conn = _h._db()
    row = conn.execute(
        "SELECT password_hash FROM admin_users WHERE id = ?", (admin["sub"],)
    ).fetchone()
    if not row or not verify_password(req.current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE id = ?",
        (hash_password(req.new_password), admin["sub"]),
    )
    conn.commit()
    return {"ok": True}
