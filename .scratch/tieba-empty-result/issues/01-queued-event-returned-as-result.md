# 01 — 贴吧并发排队被误报为成功空结果

Status: ready-for-human

## 问题与根因

用户截图显示三轮吧内检索约一秒内结束，均显示 `tieba_browser` 成功、0 条。
在真实预算账本（并发上限 2）下，按 worker 协议重放三个并发请求，能够稳定复现：
即使最终结果包含候选，`BrowserTiebaSearch` 仍返回成功、0 个候选。

`BrowserRequestBudget.handle()` 的 `None` 同时用于“已识别但正在排队”和
“不是请求事件”。`receive_result()` 用这个返回值判断是否是控制事件，将第三个
请求的排队事件提前作为最终结果返回。上层对缺失 `hits` 使用空列表，导致静默
成功；真正的结果、错误和请求完成消息留在队列中，还可能污染后续查询。

## 修复

- 接收层按协议事件类型识别 `started` / `finished`，只有明确的 `False`
  才按拒绝处理；排队时继续接收，不改变预算调度器的原有三态合同。
- 更新贴吧配方、检索和读取能力版本，避免新运行复用旧语义节点产物。
- 增加端口到接收层的连续两轮检索回归，以及排队后的超时和进程退出回归；
  同时核对候选、消息队列消费、真实请求结果和预算占位释放。

## 验证

使用 conda `agent` 环境中的 Python 直接执行（本机 `conda run` 启动失败）：

```powershell
& C:/Users/33755/anaconda3/envs/agent/python.exe -m pytest tests/tieba/test_browser_queued_result.py -q
```

修复前 3 failed；修复后与并发调度、请求计量、认证等待回归合计 25 passed。
本次修改文件 Ruff 检查通过，`pip check` 通过。
全仓 Ruff 报告 456 项问题，mypy 报告 23 个文件中的 141 项问题；
两者均未报告本次修改文件的问题。

扩大回归 `pytest tests/tieba tests/lifecycle/test_tieba_delivery_history_lifecycle.py`
完成：445 passed in 119.64s。初次执行在沙箱中卡于 Windows `socketpair`
创建、尚未进入测试请求；允许本地 socket 和测试浏览器后完整通过。
未改动前端和数据库 schema，无需前端构建或数据库迁移。

## 范围与后续验证

本次确认的是可确定复现的接收协议缺陷，没有使用用户的真实贴吧登录态进行
线上采集验收，不能据此保证所有网络、认证或页面结构问题均已解决。
重新启动后端以加载新接收逻辑和新 worker，再用“宿舍条件”发起新一轮查询；
历史消息保留原证据，不重写。无需数据库迁移。
