"""认证路由。"""
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import hash_password, verify_password, create_access_token
from app.models import User
from app.schemas import LoginRequest, TokenResponse, UserOut

router = APIRouter()


def _find_login_user(db: Session, username: str):
    """PostgreSQL 通过受控函数跨 RLS 查登录账号；测试用 SQLite 走 ORM。"""
    if db.get_bind().dialect.name != "postgresql":
        return db.query(User).filter(User.username == username).first()
    row = db.execute(
        text("SELECT * FROM public.zhidan_auth_lookup(:username)"),
        {"username": username},
    ).mappings().first()
    return SimpleNamespace(**row) if row else None


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = _find_login_user(db, req.username)
    if user is None or not user.is_active or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(subject=user.id, tenant_id=user.tenant_id, role=user.role)
    return TokenResponse(access_token=token, role=user.role, is_master=user.is_master)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
