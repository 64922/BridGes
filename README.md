# Science Companion — 科教智能体

长期科学学习与表达伙伴的首个可运行产品骨架（T001）。

## 本地开发

使用 Conda `agent` 环境（由 `environment.yml` 声明）：

```bash
conda env create -f environment.yml
conda activate agent
pip install -e '.[dev]'
cd apps/web && npm install
```

## 启动

统一 CLI：

```bash
science-companion serve
```

或手动分进程：

```bash
science-companion api        # http://127.0.0.1:8000
cd apps/web && npm run dev   # http://127.0.0.1:3000
```

Docker / Podman：

```bash
docker compose -f infra/compose/docker-compose.yml up
# 或
podman-compose -f infra/compose/docker-compose.yml up
```

## 健康检查

```bash
curl http://127.0.0.1:8000/health
```

Web UI 首页读取同一健康投影并展示存活、就绪与降级语义。

## 配置与密钥引用

所有生产运行方式共享同一配置 Schema，环境变量前缀为 `SCIENCE_COMPANION_`：

```bash
export SCIENCE_COMPANION_ENVIRONMENT=production
export SCIENCE_COMPANION_API_HOST=127.0.0.1
export SCIENCE_COMPANION_API_PORT=8000
export SCIENCE_COMPANION_SECRET_KEY_FILE=/run/secrets/secret_key
# 本地单进程开发持久化；生产环境必须配置等价的持久化数据库地址。
export SCIENCE_COMPANION_DATABASE_URL=sqlite:///./science_companion.db
```

密钥字段支持直接环境变量或 `<NAME>_FILE` 文件引用。详见 `infra/manual/README.md`。
未配置数据库地址时仅进入明确的开发内存模式；生产环境会在就绪检查中失败，避免数据静默丢失。
当前仓库内置的是带 WAL 和 Fernet 状态加密的 SQLite 单实例适配器，适合本地开发和 Compose 单实例；配置数据库时必须同时提供 `SCIENCE_COMPANION_SECRET_KEY` 或文件引用。未接入的 PostgreSQL 地址会明确报错，不会回退到内存。

## 测试

```bash
pytest -q
mypy src
cd apps/web && npm run typecheck
```

## 生产运行合同

- 生产支持手动分进程、统一 CLI、Docker、Podman 四种路径。
- 四种路径读取同一配置 Schema 和密钥引用规则。
- 生产镜像使用标准 Python / Node.js，不检测或要求 Conda。
- `environment.yml` 仅用于本地开发。
