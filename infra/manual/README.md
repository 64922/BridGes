# 手动分进程运行示例

T001 支持以下生产运行合同骨架：

- 手动分进程（本目录示例）
- 统一 CLI：`science-companion serve`
- Docker / Podman：`infra/compose/docker-compose.yml`

## 手动分进程

终端 1（API）：

```bash
science-companion api --host 127.0.0.1 --port 8000
```

终端 2（Web）：

```bash
cd apps/web
npm run dev
```

## 健康检查

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8000/health/degraded
```

Web UI 在 http://127.0.0.1:3000 显示同一健康投影。

## 生产合同说明

- 生产镜像使用标准 Python / Node.js，不依赖 Conda `agent`。
- `environment.yml` 仅用于本地开发环境声明。
