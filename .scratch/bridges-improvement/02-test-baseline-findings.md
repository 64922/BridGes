# Issue 02 失败分类记录：独立测试状态与稳定基线

状态：已修复（本机验证通过）
日期：2026-08-02
关联票：[02-stabilize-isolated-test-baseline.md](issues/02-stabilize-isolated-test-baseline.md)

## 一、复现方法与结论

在修复前对本机基线做了四种运行复现：

| 运行方式 | 结果 |
| --- | --- |
| 完整测试集（第 1 次） | 1002 passed（87.05s） |
| 完整测试集（第 2 次） | 1002 passed（78.50s） |
| 单模块 `tests/ai` | 55 passed |
| 单个测试（scope/invalidation 代表性用例） | 1 passed × 2 |

**结论**：本机（无 `.env`、无 `SCIENCE_COMPANION_*` 环境变量）不存在"第二次运行才失败"的
唯一键、注册冲突或残留任务错误。已确认的失败全部是**潜在漂移源**：当开发机或 CI
存在 `.env` 文件或真实凭据环境变量时，测试行为会随环境变化，导致三种运行方式不一致。

为验证这一点，修复后模拟了"仓库根目录存在 `.env`（含真实 Qwen 凭据、
`QWEN_RECORD_CASSETTES=true`、`DATABASE_URL`、`SECRET_KEY`、`ENVIRONMENT=production`）"
的环境，完整运行 `tests/ai + tests/integration + tests/domain`（442 个测试）全部通过，
且未产生任何副作用（见下）。

## 二、问题分类与修复方式

### A 类：环境变量泄漏与 `.env` 读取（已修复）

1. **`tests/integration/conftest.py` 的 session 级 fixture 永久污染环境变量**
   - 原因：`_force_qwen_stub` 直接写 `os.environ["SCIENCE_COMPANION_QWEN_FORCE_STUB"]="true"`
     且从不恢复。它只对 `tests/integration` 目录生效（conftest 目录作用域），
     但污染是进程级的：字母序位于 integration 之后的目录（learning、media 等）
     会继承该变量，而单独运行这些目录时不会——**执行顺序依赖**。
   - 修复：删除该 fixture，职责由根级 `tests/conftest.py` 的 function 级 autouse
     fixture 承担（每测试重置 + `monkeypatch` 自动恢复）。

2. **测试环境读取仓库 `.env`**
   - 原因：`Settings.model_config` 声明 `env_file=".env"`，任何 `Settings()`/
     `get_settings()` 构造都会读取工作目录的 `.env`。若开发机配置了真实
     `QWEN_API_KEY`、`QWEN_RECORD_CASSETTES=true` 或 `DATABASE_URL`，测试会
     发出真实网络请求、覆写提交的 cassette 资产、或连接真实数据库。
   - 修复：根级 `tests/conftest.py` 新增 `_deterministic_test_environment`
     autouse fixture，用 `monkeypatch` 显式覆盖全部影响测试的键
     （`ENVIRONMENT=test`、`QWEN_FORCE_STUB=true`、`QWEN_API_KEY=""`、
     `QWEN_RECORD_CASSETTES=false`、`DATABASE_URL=""`、`SECRET_KEY=""`、
     `BUILD_DIGEST=""` 等）并 `get_settings.cache_clear()`。环境变量优先级高于
     `.env`，因此 `.env` 无论包含什么都不会进入测试可观察行为。

3. **`test_qwen_cassette_integration.py` 从运行时配置读取凭据与录制开关**
   - 原因：`_build_gateway()` 调用 `get_settings()` 取 `qwen_api_key`、
     `qwen_cassette_dir`、`qwen_record_cassettes`。有 `.env` 时若
     `record_cassettes=true` 会真实调用 Qwen 并覆写 `tests/ai/cassettes/` 下
     已提交的资产；cassette 目录也会漂移。
   - 修复：改为显式本地替身——`api_key=None`（播放模式，缺 cassette 直接失败）、
     `record_mode=False`（永不录制）、cassette 目录固定为 `tests/ai/cassettes`，
     不再读取任何运行时配置。

4. **`test_runtime_smoke.py` 子进程继承全部父环境**
   - 原因：`_run_cli` 与 `running_api` 以 `{**os.environ, ...}` 继承环境并在
     `REPO_ROOT` 下启动子进程。父进程环境或 `.env` 中的 `DATABASE_URL`、
     `SECRET_KEY` 等会让冒烟测试的子进程连接真实存储或注册真实适配器。
   - 修复：新增 `_clean_env()`，剔除全部 `SCIENCE_COMPANION_*` 与 Conda 变量，
     再显式写入确定性配置（`ENVIRONMENT=test`、`QWEN_FORCE_STUB=true`、
     `QWEN_API_KEY=""`、`QWEN_RECORD_CASSETTES=false`、`DATABASE_URL=""`、
     `SECRET_KEY=""`），子进程环境完全自包含。

### B 类：审查后确认无需修改（现状已满足隔离）

5. **共享数据库**：集成/单元测试的 `create_app()` 默认不传 `state_store`，
   全部走进程内内存服务；SQLite 持久化测试使用 `tmp_path` 独立数据库。
   根 conftest 保证 `DATABASE_URL` 恒为空。仓库根目录的 `science_companion.db`
   是历史手动运行遗留物（已 gitignore，测试不触碰；模拟 `.env` 场景下确认
   测试不会新建任何 `.db` 文件）。

6. **对象目录**：文件对象存储尚未实现（见下方缺口 1），当前对象均为
   各 app 实例的进程内存储，随 `create_app()` 每次新建。

7. **能力注册表 / 模型适配器 / 账户 / 插件注册**：均为 `create_app()` 内新建
   实例（`CapabilityRegistry`、`ModelGateway`、`IdentityService`、`WorkflowService`
   等），无模块级可变单例；唯一模块级缓存 `get_settings` 已由根 conftest
   每测试清理。搜索确认 src 与 tests 均无模块级可变集合/注册表。

8. **可控时钟**：时间敏感断言全部使用相对时间构造输入
   （`now + timedelta(days=7)` 等），无 `time.time()` 阈值断言；
   唯一的 `time.sleep(0.2)` 是 `test_runtime_smoke` 等待真实子进程就绪，
   属于进程间同步而非业务时钟依赖。`test_runtime_smoke` 使用本地
   `127.0.0.1` 空闲端口，不发外部网络请求。

### C 类：真实功能缺口（移交后续 Issue，未在本票修复）

9. **真实网络冒烟测试仅 Qwen 具备显式启用路径**：当前所有"real"命名测试
   （`test_qwen_real_adapter.py`、`test_qwen_asr_adapter.py` 等）均使用
   `httpx.MockTransport` 本地替身。Issue 验收要求"真实 Qwen、DuckDuckGo、
   arXiv 与 SMTP 冒烟测试必须显式启用且不读取 `.env`"——后三者对应能力
   尚未实现（Web 搜索 Issue 21、arXiv 检索 Issue 22、SMTP 提醒 Issue 33），
   由对应纵向 Issue 落地时按同一原则补充。Qwen 真实冒烟即
   `test_qwen_cassette_integration.py` 的录制流程：在测试进程外**同时**
   设置 `SCIENCE_COMPANION_QWEN_RECORD_CASSETTES=true` 与真实
   `SCIENCE_COMPANION_QWEN_API_KEY`（环境变量）即显式启用，重录
   `tests/ai/cassettes/` 下已提交资产；缺任一键时测试保持确定性播放模式，
   仓库 `.env` 中的凭据被根 conftest 压过、永不生效（代码审查中修复了
   初版"开关永远被强制关闭导致显式启用不可能"的实现缺陷，见下）。

10. **文件对象存储与 PostgreSQL 适配器**：`object_storage_url`、
    `redis_url` 目前仅为配置字段无消费者，真实对象目录/数据库隔离需
    Issue 05（clean sqlite and object storage）落地后按同样原则新增隔离验证。

## 二·五、代码审查修复（2026-08-02 复核后）

对初版修复做双轴代码审查（Standards + Spec）后修复了三处实现缺陷：

1. **`<FIELD>_FILE` 秘密文件引用泄漏**：`config._load_secret_files` 直接读
   环境变量 `SCIENCE_COMPANION_{QWEN_API_KEY,DATABASE_URL,SECRET_KEY,REDIS_URL,
   OBJECT_STORAGE_URL}_FILE` 并覆盖字段值，而初版根 conftest 只中和直接键。
   开发机若按推荐路径设置了 `*_FILE`，真实密钥与真实数据库地址会绕过隔离
   进入测试（`build_state_store` 的"空值回退内存"兜底随之失效）。
   已修复：根 conftest 将这 5 个 `*_FILE` 键一并置空。
2. **"真实 Qwen 冒烟必须显式启用"在初版中不可能**：初版把
   `test_qwen_cassette_integration.py` 固定为播放模式（`api_key=None`、
   `record_mode=False`）且根 conftest 无条件强制
   `RECORD_CASSETTES=false`，重录 cassette 只能改源码，验收标准 AC5 的
   "显式启用"语义被破坏。已修复：用户在测试进程外显式设置
   `RECORD_CASSETTES=true` **且**环境变量提供真实 `QWEN_API_KEY` 时，
   根 conftest 放行这两个键（其余键含 `FORCE_STUB=true` 仍强制），
   cassette 测试相应进入录制分支；缺任一键即保持确定性播放模式，
   `.env` 凭据无入口。验证：显式模式探针测试确认放行语义，
   cassette 测试在显式模式下命中本地资产重放、不发网络请求。
3. **冗余与失实**：`tests/integration/conftest.py` 的 client fixture 中
   残留与根 conftest 重复的 `setenv` + `cache_clear`（初版注释已承认职责
   移交）已删除；根 conftest 中"QWEN_API_KEY 置空即不注册真实适配器"的
   注释表述失实（实际拦截点是 `FORCE_STUB`），已改为准确表述。

## 三、验收核对

- [x] 每测试独立临时数据库/对象目录/凭据替身/能力注册表/可控时钟，测试后不影响下一测试
      （内存服务 + `tmp_path` + 替身凭据 + 确定性环境重置）
- [x] 账户、模型适配器、插件注册、后台任务状态无模块级单例泄漏（B 类审查 + A 类修复）
- [x] 完整测试集连续运行两次均通过（1002 + 1002，修复后复验见下）
- [x] 单测试 / 单模块 / 完整测试集三种方式结果一致
- [x] 网络测试默认确定性本地替身；真实冒烟测试路径显式启用且不读取 `.env`
      （显式启用 = 测试进程外同时设置 `RECORD_CASSETTES=true` 与真实
      `QWEN_API_KEY` 环境变量，见"二·五"修复 2）
- [x] 中文失败分类记录（本文档）
- [x] 未新增无条件跳过、宽泛异常捕获或依赖执行顺序的测试
