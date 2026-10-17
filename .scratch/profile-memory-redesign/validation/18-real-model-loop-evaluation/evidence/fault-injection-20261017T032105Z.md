# 画像闭环模拟故障报告（2026-10-17T03:21:12+00:00）

- 环境：test + 确定性替身；故障经生产接缝注入，结果不冒充真实模型。
- 代码提交：b2fa0648758bc2ed9bd57187b824cd4e409621dd

## fault-chat-generation

- 注入：聊天流式适配器首次调用失败，重试的新回合成功。
  - [通过] fault:generation-failure-visible：失败运行=[{'status': 'failed', 'error_code': 'internal_error'}] 自动重试=1 最终状态='done'
  - [通过] fault:generation-retry-succeeds：status='done' content='故障恢复后的脚本化回答。'

## fault-extraction-retry

- 注入：提取适配器首次调用失败，重试尝试成功且只写入一次。
  - [通过] fault:extraction-failure-retried：attempts=[2] status=['succeeded']
  - [通过] fault:extraction-recovered-once：活动条目=['我每周三去实验室']

## fault-save-retry

- 注入：原子提交入口首次调用失败，重试后恢复且只写入一次。
  - [通过] fault:save-failure-recovered-once：活动条目=['我喜欢下围棋']
  - [通过] fault:save-failure-recorded：attempts=[2] last_error=[None]
