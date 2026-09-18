"""数据库连接、会话与每事务 RLS 租户上下文。"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)


class TenantSession(Session):
    """在 session.info 中携带可信租户 ID 的业务会话。"""


def set_tenant_context(session: Session, tenant_id: int) -> None:
    """保存 JWT/后台任务给出的可信租户 ID，供每个新事务自动注入。"""
    session.info["tenant_id"] = int(tenant_id)


def _inject_rls_tenant(connection, tenant_id: int) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )


@event.listens_for(TenantSession, "after_begin")
def _restore_tenant_after_begin(session, transaction, connection) -> None:
    """commit/rollback 后的新事务仍使用同一租户，且变量始终是事务级。"""
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None:
        _inject_rls_tenant(connection, tenant_id)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=TenantSession,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""
    pass


def get_db():
    """FastAPI 依赖：每个请求一个数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
