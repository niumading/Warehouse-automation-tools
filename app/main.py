"""FastAPI 应用入口。"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine
from app.core.rate_limit import FixedWindowRateLimiter
from app.core.security import decode_token
from app.api import accounts, auth, master, outbound, purchase, ocr, returns, subcontract, sync, settings as settings_api

settings = get_settings()
rate_limiter = FixedWindowRateLimiter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时校验数据库连接。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"[{settings.app_name}] 数据库连接成功")
    except Exception as e:  # pragma: no cover
        print(f"[{settings.app_name}] 数据库连接失败: {e}（可稍后启动时重试）")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# 本地前端页面（file:// 或不同端口）需要跨域，开发期放开
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_rate_limit(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    auth = request.headers.get("authorization", "")
    payload = decode_token(auth[7:]) if auth.lower().startswith("bearer ") else None
    subject = f"{payload.get('tenant_id')}:{payload.get('sub')}" if payload else client_ip
    if path == "/api/auth/login":
        scope, limit = "login", settings.login_rate_limit_per_minute
    elif path in {"/api/ocr/upload", "/api/outbound/ocr/upload", "/api/subcontract/ocr/upload"}:
        scope, limit = "ocr", settings.ocr_rate_limit_per_minute
    else:
        scope, limit = "api", settings.api_rate_limit_per_minute
    allowed, retry_after = rate_limiter.allow(f"{scope}:{subject}", limit)
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={"code": 429, "message": "请求过于频繁，请稍后再试", "data": {"retry_after": retry_after}},
        )
    return await call_next(request)

# 注意：租户上下文（RLS）在 deps.get_current_user 内依据 JWT 注入到业务会话，
# 不信任前端请求头，避免越权。


# ===== 统一响应格式 {code, message, data} =====
@app.middleware("http")
async def wrap_response(request: Request, call_next):
    """将成功响应包成 {code, message, data}；异常由异常处理器兜底。"""
    from starlette.responses import Response
    response = await call_next(request)
    if request.url.path.startswith("/api") and response.headers.get("content-type", "").startswith("application/json"):
        try:
            import json
            body = json.loads(response.body)
            # 已是统一格式则不再包裹
            if isinstance(body, dict) and set(body.keys()) == {"code", "message", "data"}:
                return response
            wrapped = json.dumps({"code": 0, "message": "ok", "data": body}, ensure_ascii=False).encode()
            return Response(content=wrapped, media_type="application/json", status_code=response.status_code)
        except Exception:
            return response
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors = [
        {key: value for key, value in error.items() if key not in {"ctx", "input"}}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": "参数校验失败", "data": {"errors": errors}},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误，请稍后重试或联系管理员"},
    )


# ===== 路由挂载 =====
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
app.include_router(master.router, prefix="/api/master", tags=["master"])
app.include_router(purchase.router, prefix="/api/purchase", tags=["purchase"])
app.include_router(outbound.router, prefix="/api/outbound", tags=["outbound"])
app.include_router(subcontract.router, prefix="/api/subcontract", tags=["subcontract"])
app.include_router(ocr.router, prefix="/api/ocr", tags=["ocr"])
app.include_router(returns.router, prefix="/api/returns", tags=["returns"])
app.include_router(sync.router, prefix="/api/sync", tags=["sync"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])

# 前端工作台静态托管：访问 /console 即打开制单工作台页面
_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
if os.path.isdir(_WEB_DIR):
    app.mount("/console", StaticFiles(directory=_WEB_DIR, html=True), name="console")

# 新版独立入口，旧版仍由 /console/ 提供。
_WEB_V2_DIR = os.path.join(os.path.dirname(_WEB_DIR), "web-v2")
if os.path.isdir(_WEB_V2_DIR):
    app.mount("/console-v2", StaticFiles(directory=_WEB_V2_DIR, html=True), name="console-v2")


@app.get("/health")
def health():
    return {"code": 0, "message": "ok", "data": {"status": "up"}}


@app.get("/")
def root():
    return {"code": 0, "message": "ok", "data": {"name": settings.app_name, "version": "0.1.0"}}
