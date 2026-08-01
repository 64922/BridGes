# 02 — 建立独立测试状态和稳定基线

Status: ready-for-agent
Blocked by: None
Covered requirements: IMP-03, SCORE-03
ADRs: [ADR-0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [ADR-0016](../../../docs/adr/0016-incremental-vertical-replacement.md)

## What to build

把现有测试从共享数据库、对象目录、能力注册表、时钟和模块级可变单例中隔离出来，使同一测试集能够单独运行、重复运行和改变执行顺序而得到一致结果。先分类并复现现有失败，再修复真实污染源；不得通过放宽断言、吞掉异常或把失败测试标记为跳过来制造绿色基线。

## Acceptance criteria

- [ ] 每个测试获得独立临时数据库、对象目录、凭据替身、能力注册表和可控时钟，测试结束后不影响下一测试。
- [ ] 账户、模型适配器、插件注册和后台任务相关状态不再通过模块级可变单例跨测试泄漏。
- [ ] 同一完整测试集连续运行两次均通过，且不存在第二次运行才出现的唯一键、注册冲突或残留任务错误。
- [ ] 单个测试、单个测试模块和完整测试集三种运行方式结果一致。
- [ ] 网络测试默认使用确定性本地替身；真实 Qwen、DuckDuckGo、arXiv 与 SMTP 冒烟测试必须显式启用且不读取 `.env`。
- [ ] 形成中文失败分类记录，说明原有失败的原因、修复方式和仍需后续 Issue 处理的真实功能缺口。
- [ ] 不新增无条件跳过、宽泛异常捕获或依赖执行顺序的测试。

## Verification

```powershell
conda run -n agent python -m pytest
conda run -n agent python -m pytest
conda run -n agent python -m ruff check .
conda run -n agent python -m mypy src
npm --prefix apps/web run typecheck
```

## Non-goals

- 不在本 Issue 实现 BridGes 新业务能力。
- 不通过删除既有测试或降低断言强度处理失败。
- 不要求访问真实外部服务。

## Blocked by

None - can start immediately.

## Comments

这是后续所有改造的可信验证基线。若发现测试揭示真实产品缺陷，应保留失败证据并交由对应纵向 Issue 修复。
