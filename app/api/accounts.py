"""租户主账号管理子账号。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, ROLE_ADMIN
from app.core.security import hash_password
from app.models import User
from app.schemas import AccountCreate, AccountOut, AccountUpdate

router = APIRouter()


def _require_master_admin(user: User) -> None:
    if user.role != ROLE_ADMIN or not user.is_master:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅主账号可管理子账号")


@router.get("", response_model=list[AccountOut])
def list_accounts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_master_admin(user)
    return db.query(User).filter(User.tenant_id == user.tenant_id).order_by(User.is_master.desc(), User.id).all()


@router.post("", response_model=AccountOut, status_code=201)
def create_account(
    req: AccountCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_master_admin(user)
    username = req.username.strip()
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="账号名已存在")
    account = User(
        tenant_id=user.tenant_id,
        username=username,
        password_hash=hash_password(req.password),
        role=req.role,
        is_master=False,
        is_active=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.put("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    req: AccountUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_master_admin(user)
    account = db.query(User).filter(
        User.id == account_id,
        User.tenant_id == user.tenant_id,
    ).first()
    if account is None:
        raise HTTPException(status_code=404, detail="子账号不存在")
    if account.is_master:
        raise HTTPException(status_code=400, detail="主账号不能在这里修改或停用")
    fields = req.model_fields_set
    if "role" in fields and req.role is not None:
        account.role = req.role
    if "password" in fields and req.password:
        account.password_hash = hash_password(req.password)
    if "is_active" in fields and req.is_active is not None:
        account.is_active = req.is_active
    db.commit()
    db.refresh(account)
    return account
