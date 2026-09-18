"""系统设置路由（自动审核、AI API、ERP 配置）。"""
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.ai_config import get_ai_config, save_ai_config
from app.core.config import get_settings as get_app_settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_role, ROLE_ADMIN
from app.models import User, TenantSetting

router = APIRouter()


class SettingsUpdate(BaseModel):
    auto_audit_enabled: Optional[bool] = None
    audit_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    erp_type: Optional[str] = None
    erp_base_url: Optional[str] = Field(default=None, max_length=500)
    erp_sid: Optional[str] = Field(default=None, max_length=200)
    erp_appkey: Optional[str] = Field(default=None, max_length=512)
    erp_appsecret: Optional[str] = Field(default=None, max_length=2048)
    erp_token: Optional[str] = Field(default=None, max_length=4096)
    ai_base_url: Optional[str] = Field(default=None, max_length=500)
    ai_vision_model: Optional[str] = Field(default=None, max_length=200)
    ai_api_key: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("ai_base_url", "erp_base_url")
    @classmethod
    def validate_ai_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("API 地址必须是有效的 http 或 https 地址")
        if parsed.username or parsed.password:
            raise ValueError("API 地址不能包含账号或密码")
        return value.strip().rstrip("/")


def _ai_public(config) -> dict:
    return {
        "ai_base_url": config.base_url,
        "ai_vision_model": config.vision_model,
        "ai_api_key_configured": bool(config.api_key),
        "ai_api_key_hint": config.api_key_hint,
        "ai_configured": config.configured,
    }


def _secret_hint(value: str | None) -> str:
    if not value:
        return ""
    return f"••••{value[-4:]}" if len(value) > 4 else "••••"


def _erp_public(setting: TenantSetting | None) -> dict:
    app_settings = get_app_settings()
    base_url = (setting.erp_base_url if setting else None) or app_settings.erp_base_url
    sid = (setting.erp_sid if setting else None) or app_settings.erp_sid
    appkey = (setting.erp_appkey if setting else None) or app_settings.erp_appkey
    appsecret = (setting.erp_appsecret if setting else None) or app_settings.erp_appsecret
    token = setting.erp_token if setting else None
    erp_type = (setting.erp_type if setting else None) or app_settings.erp_type
    configured = bool(base_url and appkey and appsecret and (sid if erp_type == "wdt_qyb" else token))
    return {
        "erp_type": erp_type,
        "erp_base_url": base_url or "",
        "erp_sid_configured": bool(sid),
        "erp_sid_hint": _secret_hint(sid),
        "erp_appkey_configured": bool(appkey),
        "erp_appkey_hint": _secret_hint(appkey),
        "erp_appsecret_configured": bool(appsecret),
        "erp_appsecret_hint": _secret_hint(appsecret),
        "erp_token_configured": bool(token),
        "erp_token_hint": _secret_hint(token),
        "erp_configured": configured,
    }


@router.get("")
def get_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = db.get(TenantSetting, user.tenant_id)
    ai = _ai_public(get_ai_config())
    if s is None:
        return {"code": 0, "message": "ok", "data": {
            "auto_audit_enabled": False, "audit_threshold": 0.95,
            **_erp_public(None),
            **ai,
        }}
    return {"code": 0, "message": "ok", "data": {
        "auto_audit_enabled": s.auto_audit_enabled,
        "audit_threshold": float(s.audit_threshold),
        **_erp_public(s),
        **ai,
    }}


@router.put("")
def update_settings(
    req: SettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    fields = req.model_fields_set
    ai_fields = {"ai_base_url", "ai_vision_model", "ai_api_key"}
    if fields & ai_fields:
        current = get_ai_config()
        ai = save_ai_config(
            base_url=(req.ai_base_url or "") if "ai_base_url" in fields else current.base_url,
            vision_model=(req.ai_vision_model or "") if "ai_vision_model" in fields else current.vision_model,
            api_key=req.ai_api_key if "ai_api_key" in fields else None,
        )
    else:
        ai = get_ai_config()

    db_fields = {
        "auto_audit_enabled", "audit_threshold", "erp_type", "erp_base_url", "erp_sid",
        "erp_appkey", "erp_appsecret", "erp_token",
    }
    updates = req.model_dump(exclude_unset=True, include=db_fields)
    for secret_field in ("erp_sid", "erp_appkey", "erp_appsecret", "erp_token"):
        if updates.get(secret_field) == "":
            updates.pop(secret_field)
    if updates:
        s = db.get(TenantSetting, user.tenant_id)
        if s is None:
            s = TenantSetting(tenant_id=user.tenant_id)
            db.add(s)
        for key, value in updates.items():
            setattr(s, key, value)
        db.commit()
    s = db.get(TenantSetting, user.tenant_id)
    return {
        "code": 0,
        "message": "ok",
        "data": {"updated": True, **_ai_public(ai), **_erp_public(s)},
    }
