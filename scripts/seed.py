"""开发种子脚本：创建演示租户、主账号、主数据。

用法: python -m scripts.seed
"""
from sqlalchemy import create_engine
import os
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import Tenant, User, Supplier, Product, ProductSupplier, Warehouse


def seed():
    settings = get_settings()
    password = os.environ.get("SEED_ADMIN_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError("Set SEED_ADMIN_PASSWORD to a unique password of at least 12 characters before creating demo accounts")
    engine = create_engine(settings.database_admin_url or settings.database_url, pool_pre_ping=True)
    SeedSession = sessionmaker(bind=engine)
    with SeedSession() as db:
        # 租户
        tenant = db.query(Tenant).filter(Tenant.name == "演示客户").first()
        if tenant is None:
            tenant = Tenant(name="演示客户")
            db.add(tenant)
            db.flush()
        else:
            print("租户已存在，跳过")

        # 主账号密码由执行者提供，不内置共享密码。
        if db.query(User).filter(User.tenant_id == tenant.id, User.username == "admin").first() is None:
            db.add(User(
                tenant_id=tenant.id, username="admin",
                password_hash=hash_password(password),
                role="admin", is_master=True,
            ))
            print("已创建主账号 admin（使用调用者提供的密码）")
        else:
            print("admin 已存在")

        # 演示供应商 / 商品
        sups = {}
        for name in ["演示供应商甲", "演示供应商乙", "演示供应商丙"]:
            s = db.query(Supplier).filter(Supplier.tenant_id == tenant.id, Supplier.name == name).first()
            if s is None:
                s = Supplier(tenant_id=tenant.id, name=name)
                db.add(s); db.flush()
            sups[name] = s

        # 演示商品
        prods = {}
        for sku, name, spec in [
            ("SKU-10231", "演示商品甲", "20g"),
            ("SKU-10232", "演示商品乙", "20g"),
            ("SKU-10233", "演示商品丙", "71-90"),
        ]:
            p = db.query(Product).filter(Product.tenant_id == tenant.id, Product.sku == sku).first()
            if p is None:
                p = Product(tenant_id=tenant.id, sku=sku, name=name, spec=spec, unit="盒")
                db.add(p); db.flush()
            prods[name] = p

        # 商品-供应商关联
        links = [(prods["演示商品甲"], sups["演示供应商乙"]),
                 (prods["演示商品乙"], sups["演示供应商乙"]),
                 (prods["演示商品丙"], sups["演示供应商甲"])]
        for p, s in links:
            if db.query(ProductSupplier).filter(
                ProductSupplier.tenant_id == tenant.id,
                ProductSupplier.product_id == p.id,
                ProductSupplier.supplier_id == s.id,
            ).first() is None:
                db.add(ProductSupplier(tenant_id=tenant.id, product_id=p.id, supplier_id=s.id))

        # 演示仓库
        for code, name in [("WH-A", "云仓 A"), ("WH-B", "云仓 B"), ("WH-C", "云仓 C")]:
            if db.query(Warehouse).filter(Warehouse.tenant_id == tenant.id, Warehouse.code == code).first() is None:
                db.add(Warehouse(tenant_id=tenant.id, code=code, name=name))

        db.commit()
        print("种子数据写入完成")
    engine.dispose()


if __name__ == "__main__":
    seed()
