# Warehouse-automation-tools

仓储制单自动化工作台。

FastAPI、PostgreSQL 与原生 HTML/CSS/JavaScript 构建的仓储制单应用。

本仓库只公开脱敏源代码，不包含运行环境、真实客户资料、上传图片、数据库、日志、密钥或原项目历史。GitHub 托管代码不等于上线运行；FastAPI 和 PostgreSQL 需要另行部署，不能直接在 GitHub Pages 中运行。

## 功能

- 采购单：手动/拍照识别制单、人工审核及 ERP 对接边界。
- 退货：数据库导入更新、运单扫描与批量匹配、防重复提醒、供应商分 Sheet 或合并 Excel 导出。
- 其他出库：拍照识别后人工确认制单。
- 委外入库/出库：手动/拍照制单、审核、驳回、修改后重新提交、已审核单 Excel 导出与 A4 横向打印。
- 子账号角色权限、多租户隔离、PostgreSQL RLS 和接口限流。

委外与其他出库流程不会自动推送 ERP 或变更库存；正式 ERP 调用须取得对应接口授权并验证字段映射。代码和演示夹具不是任何企业的真实订单数据。

## 本地运行

需要自行安装 Python 3.11+ 与 PostgreSQL。不要对正在使用的数据库进行演示初始化。

1. 创建空数据库，例如 `zhidan`。
2. 将 `.env.example` 复制为 `.env`，填写自己的数据库连接和强随机 `SECRET_KEY`；真实 API 凭证只保存到本地配置中。
3. 在项目目录安装依赖：`python -m pip install -r requirements.txt`。
4. 执行迁移：`python -m alembic upgrade head`。
5. 可选演示初始化：先设置环境变量 `SEED_ADMIN_PASSWORD`（至少 12 个字符，必须自选），再执行 `python -m scripts.seed`。演示登录名为 `admin`，没有内置通用密码。
6. 启动：`python -m uvicorn app.main:app --host 127.0.0.1 --port 8010`，访问 `http://127.0.0.1:8010/console-v2/`。

Windows 可使用 `setup_db.bat`、`start.bat`。数据库账号加固脚本会创建/轮换应用角色并更新本地 `.env`，执行前务必备份。

## 测试

`python -m unittest discover -s tests -q`

Python 单元测试使用模拟数据与内存数据库。浏览器测试需要自行准备 Node.js 和 Playwright，可运行 `node tests/subcontract_modes.e2e.cjs`、`node tests/bubble_interaction.e2e.cjs`。可设置 `PYTHON` 为已安装 Python 的路径；浏览器验收模拟业务 API，不创建正式订单。

## 隐私与部署

- `.env`、`config/`、`uploads/`、`backups/`、`logs/`、测试截图及数据库文件均不应提交。
- `tests/` 中的凭证字符串只是测试夹具，不用于实际登录；不要复用它们。
- 生产环境必须设置自己的密钥、数据库密码和账号密码，使用非超级用户应用连接并启用 RLS。
- 公网运行前还须配置 HTTPS、反向代理、访问边界、备份及恢复验证；当前开发跨域配置需要按部署环境限制。
- 仓库不包含生产环境交接文档、个人电脑路径、真实供应商名单或真实运单。
