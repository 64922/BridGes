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
```

密钥字段支持直接环境变量或 `<NAME>_FILE` 文件引用。详见 `infra/manual/README.md`。

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
