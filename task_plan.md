# Task Plan — Issue 06：交付源码与容器统一运行合同

状态：进行中（2026-08-02）

## 目标

实现 `.scratch/bridges-improvement/issues/06-deliver-unified-source-and-container-runtime.md`
的全部验收标准：`BridGes start` 同步启动构建后的 Web、API、后台执行器与提醒调度器；
校验依赖与目录权限、获取数据目录单实例锁、执行迁移、等待健康检查、输出本地电脑端
访问地址；关键服务失败非零退出并显示可操作中文错误；Ctrl+C 按顺序停止并释放锁；
重复启动被拒、异常终止可安全恢复；Docker/Podman 卷语义一致；两条路径均不要求
`.env`；文档只承诺源码 Conda、`.venv` 与 Docker/Podman，无 Windows 安装包与手机承诺。

## 任务清单

1. [x] 新建 `src/bridges/runtime/`：`lock.py`（跨平台数据目录单实例锁：POSIX flock /
   Windows msvcrt，OS 建议锁进程退出自动释放，中文错误）、`executor.py`（真实后台
   执行器：周期性清理 pending_cleanup 与孤立文件队列）、`scheduler.py`（提醒调度器：
   监督循环 + 到期任务分发接缝，未配置数据库时待机不崩溃）
   → 验证：单测覆盖锁轮转/重复启动拒绝/进程死亡自动释放、worker 单轮真实清理、
   scheduler 心跳（mypy/ruff 通过）
2. [x] CLI `src/bridges/cli/main.py`：`worker` 真实实现（替换 "not implemented" 桩）、
   新增 `scheduler` 命令；`start`/`serve` 改为监督编排：校验依赖与目录权限 → 单实例锁 →
   迁移 → 启动 api/web/worker/scheduler 四个子进程 → 等待健康检查 → 输出电脑端访问
   地址 → 任一失败非零退出并报中文错误 → Ctrl+C/SIGBREAK/SIGTERM 按顺序停止并释放锁
   → 验证：`BridGes start --help`；开发与生产 profile 全流程冒烟（空临时数据目录 +
   空闲端口 + 构建产物）通过：启动、迁移、重复启动拒绝、优雅停止（退出码 0）、
   停止后再次启动成功
3. [x] `infra/compose/docker-compose.yml`：新增 worker 与 scheduler 服务（同一 API 镜像、
   同一 bridges-data 卷、同一入口密钥自举、依赖 API 健康、SIGTERM 优雅停止）
   → 验证：compose YAML 解析通过；无 docker 环境时用 python yaml 校验（4 服务均解析）
4. [x] 文档：`README.md` 补充 `.venv` 旅程、前端构建步骤、四进程说明、单实例锁说明、
   仅支持源码 Conda/`.venv`/Docker/Podman 且无 Windows 安装包的范围声明、电脑端
   承诺；`infra/manual/README.md` 同步更新
   → 验证：文档与实现一致，无 Windows 安装包/手机访问暗示
5. [x] 测试 `tests/runtime/test_runtime_contract.py`（11 个测试全过，含全流程冒烟：
   启动/迁移/健康检查/重复启动拒绝/优雅停止/再次启动，13.96s）+ 更新
   `tests/integration/test_cli_contract.py` 的 serve 帮助文本断言
   → 验证：pytest -k "cli or startup or health or runtime" 全部通过
6. [x] 全量回归：pytest 全套、ruff、mypy src、npm typecheck、npm build
   → 验证：1035 passed（基线 1022 + 13 新增），mypy 0 错误，ruff 新代码 0 错误
   （仓库 253 个预存错误为既有 ruff 版本漂移，与本票无关），npm typecheck 通过，
   npm build 产出 .next/standalone（.next 已 gitignore）
7. [x] 冒烟旅程：空临时数据目录分别执行 Conda（agent 环境）与 `.venv` 旅程，验证
   启动、健康检查、重复启动拒绝和 Ctrl+C/停止后的再次启动
   → 验证：两环境各 11/11 通过（runtime 契约测试含全流程冒烟）
8. [x] /code-review 代码审查（Standards+Spec 双轴）→ 修复发现的 bug → 复跑验证
   → 验证：修复 8 项（见提交信息），1035 全绿、mypy 0 错误、venv 复跑 11/11
9. [x] 更新 issue 06 的 Acceptance criteria 勾选状态 + Comments + 状态，提交 git
   → 验证：提交信息包含工作总结与 bug 修改总结

## 状态

已完成（2026-08-02）：1035 passed，issue 06 标记 ready-for-human 等待人工验收。

## 验收命令

```powershell
conda run -n agent python -m pytest -k "cli or startup or health or runtime"
conda run -n agent BridGes start --help
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
docker compose config   # 本机无 docker，改用 python yaml 解析校验
```
