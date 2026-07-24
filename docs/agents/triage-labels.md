# 分诊标签

工程技能使用五种标准分诊角色。下表将这些角色映射到本仓库实际使用的状态字符串。

| 标准角色 | 本仓库状态 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待报告者补充信息 |
| `ready-for-agent` | `ready-for-agent` | 信息完整，可由代理独立执行 |
| `ready-for-human` | `ready-for-human` | 需要人工实现 |
| `wontfix` | `wontfix` | 决定不处理 |

当技能提到某种分诊角色时，使用表中对应的本仓库状态字符串。
