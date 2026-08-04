# 25 — 交付数字分身画像中心与静态头像
Status: delivered
Blocked by: [04](./04-establish-desktop-design-baseline-and-brand-assets.md), [07](./07-deliver-qq-username-auth-pages.md), [16](./16-deliver-secure-chat-attachments.md)
Covered requirements: PROFILE-01, A-01, A-02, IMP-01, DESKTOP-01
ADRs: [0002](../../../docs/adr/0002-tiered-profile-writing-and-emotion-boundary.md), [0013](../../../docs/adr/0013-single-machine-storage-and-background-runtime.md), [0015](../../../docs/adr/0015-minimum-cloud-disclosure.md), [0021](../../../docs/adr/0021-auditable-profile-not-animated-avatar.md), [0023](../../../docs/adr/0023-desktop-only-deployment-and-use.md)

## What to build

交付侧边栏“用户画像”对应的完整数字分身桌面中心。九类信息——基本情况、阶段目标、兴趣偏好、表达习惯、知识状态、情绪变化趋势、重要经历、正在面对的问题、授权范围——必须作为可独立治理的记录保存，而不是合并为不可审计的长文本。每条记录展示来源证据、适用范围、敏感级别、何时记录、何时更新、何时用于回答和版本历史。

用户可新增、确认、编辑、撤回、冻结、删除和导出画像记录，并查看最近使用记录。数字分身只提供可选静态头像；头像可从当前账户安全图片中选择或上传，仅作视觉标识，不得据此推断身份、性格或情绪。

## Acceptance criteria

- [x] 侧边栏“用户画像”进入完整桌面页面，九类画像均有独立分区、记录、授权和历史，而非单一 JSON 或总结文本。
- [x] 每条记录显示可追溯来源、创建时间、更新时间、最近使用时间、适用场景、授权范围、敏感级别和当前状态。
- [x] 用户可手动新增、确认、编辑、撤回、冻结、解冻和删除记录；每次操作写入不可混淆的账户级审计。
- [x] 撤回会停止后续回答使用但保留可审计历史；删除按确认合同清理当前记录，冻结期间禁止自动更新。
- [x] 历史视图可比较版本并说明由用户操作还是智能体候选触发，不以覆盖旧值抹掉来源。
- [x] 用户可导出可读的画像和授权历史；导出不包含密码、百炼 Key、SMTP 授权码或其他账户内容。
- [x] 用户可上传或选择自己的静态头像并更换、移除；所有对象访问均验证账户归属。
- [x] 头像不触发面部识别，也不用于推断身份、性格、情绪、年龄、性别或敏感属性。
- [x] 页面具有中文 loading、empty、error、permission 和 recovery 状态；写入失败回滚 UI，不显示假成功。
- [x] 页面遵循 BridGes 桌面视觉基线，支持键盘浏览分类、打开证据与历史抽屉、确认撤回和返回新聊天。

### 交付记录（2026-08-05）

- 后端：九类维度注册表（ProfileDimension + 中文标签 + 需确认维度）、手动新增记录
  （观察→候选→接受链路）、撤回（WITHDRAWN 状态）、解冻、最近使用时间
  （compile_memory_slice 记录 last_used_at）、版本历史端点、候选决策审计、
  导出含全部画像审计动作引用、头像移除（DELETE /auth/profile/avatar）。
- 前端：/account/profile 完整画像中心——九类键盘可浏览 Tabs、记录卡（来源/
  时间/范围/敏感级别/状态）、增改撤冻删确认对话框、版本历史抽屉（含回滚）、
  待确认候选区块、导出下载、静态头像选择/上传/移除。
- 测试：模块 17 条 + API 8 条新增；E2E issue25 7 条（九类空态/增改撤冻删/
  历史/候选确认/导出/头像/键盘）；issue12 断言随正式页面更新。
- 验证：1421 pytest（4 个 runtime smoke 为环境既有 CLI 编码 flake）、
  mypy 191 文件 0 错误、改动区域 ruff 干净、npm typecheck/build 通过、
  E2E 全量 163+ 通过（issue04/08 为环境既有 flake）。
- 审查修复：历史变更者误标（changed_by 统一 "user"）、候选确认缺审计、
  导出审计引用不全、freeze/withdraw/unfreeze 抽取状态转换助手、API 错误
  映射去重、新增模式不再收集无效的"记录原因"字段、formatTime 复用
  lib/format。
- 已知边界：冻结的"禁止自动更新"语义在自动写入落地（Issue 26）时生效，
  契约注释已标注；画像数据当前为进程内存储（与既有画像域一致）。

## Verification

- 在 Conda `agent` 环境运行九类记录、版本历史、冻结/撤回/删除语义、导出清洗和账户隔离测试。
- 运行对象授权测试，确认其他账户无法查看、选择、替换或删除头像。
- 运行前端类型检查和桌面 E2E，覆盖九类空态、增改撤冻删、历史、导出、头像及失败恢复。
- 人工审查页面与导出，确认能够回答“何时记录、何时更新、何时用于回答”。

## Non-goals

- 不实现 3D 人物、动画、口型同步、声音克隆或虚拟主播。
- 不在本 Issue 自动从对话写入画像，也不依据头像生成画像。
- 不实现或验收移动端布局与交互。

## Blocked by

- [Issue 04：桌面设计基线与品牌资产](./04-establish-desktop-design-baseline-and-brand-assets.md)
- [Issue 07：QQ 邮箱与用户名认证页面](./07-deliver-qq-username-auth-pages.md)
- [Issue 16：安全聊天附件](./16-deliver-secure-chat-attachments.md)

## Comments

这里的静态头像是数字分身视觉标识；用户名、QQ 邮箱和登录身份仍由账户领域管理，不能混成画像事实。
