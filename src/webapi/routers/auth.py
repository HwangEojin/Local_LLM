from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import auth
from ..deps import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody, response: Response):
    if not auth.verify_login(body.username, body.password):
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않음")
    token = auth.make_session_cookie(body.username)
    response.set_cookie(auth.COOKIE_NAME, token, max_age=auth.SESSION_MAX_AGE_S, httponly=True, samesite="lax")
    return {"user": body.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(current_user)):
    return {"user": user}
