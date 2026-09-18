"""FastAPI 依赖：认证、当前用户、租户上下文。"""
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.database import get_db, set_tenant_context
from app.core.security import decode_token
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)

# 权限常量
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_AUDITOR = "auditor"
ROLE_VIEWER = "viewer"


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """从 JWT 解析当前用户，并在当前会话注入 RLS 租户上下文。

    租户 ID 只来自 JWT（不可由前端请求头伪造，防止越权访问其他租户）。
    set_config(..., is_local=true) 为事务级，请求结束会话归还连接池后自动清除，
    避免连接复用时串租户。
    """
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效或已过期")

    # 租户 ID 写入 Session；每次新事务都会自动注入事务级 RLS 上下文。
    tenant_id = payload.get("tenant_id")
    if tenant_id is not None:
        set_tenant_context(db, tenant_id)

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


def require_role(*roles: str):
    """权限依赖：仅允许指定角色访问。"""
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限执行此操作")
        return user
    return checker


def get_tenant_id(user: User = Depends(get_current_user)) -> int:
    """当前请求的租户 ID（来自 JWT）。"""
    return user.tenant_id
