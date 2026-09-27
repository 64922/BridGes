# 04 — 修复学习书页识别的图片请求并提供准确失败原因

**What to build:** 用户上传原始三张教材照片后，学习流程完成书页识别并进入预习；发生不可重试的参数错误时明确说明原因，保留材料与进度，修复后可安全重试。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-human

## 问题与证据边界

学习轮次在 `study.recognize` 收到 `client_error_400`，界面统一显示“学习处理模型暂时不可用，请稍后重试”。OCR 和视觉适配器实际请求均默认使用 `min_pixels=3072`。同日同模型另一条文档 OCR 记录返回参数下限 65536；那不是该学习轮次的调用记录，因此参数不兼容是强证据支持的首要原因，仍须对原学习路径最终确认。

## 任务内容

1. 先构建能关联学习轮次、识别节点、具体能力和模型的复现路径。使用用户原始教材图片的隔离副本，不从包含照片缩略图的页面截图替代原始图片。原材料不可访问时明确记录缺口。
2. 捕获脱敏请求参数和上游错误摘要，验证实际生效模型对像素参数的约束。优先核验 `min_pixels`，若存在其他 400 原因继续定位，不把另一条文档调用当成最终结论。
3. 统一 OCR、视觉理解和主模型能力探测的图片参数构造：依据已验证模型合同发送有效参数，或在可证明兼容时省略可选参数。避免只改学习调用点而其他共用适配器继续发送错误参数，也不将未验证的统一阈值宣称适用于所有模型。
4. 保持一个学习轮次的主模型锁定、凭据来源与配置原子激活规则。模型探测应覆盖真实图片调用形态，避免探测成功但业务请求必然失败。
5. 区分参数错误、鉴权问题、模型不可用、超时和响应解析错误。给用户简洁中文原因及合理下一步；内部记录保留脱敏错误码、能力、模型和关联标识。非重试错误不得提示等待即可恢复，不记录图片 base64 或密钥。
6. 验证识别失败后的页状态、追加照片与重试行为。失败不能标记书页已识别或推进阶段；成功重试不重复添加已识别页，保留页序和现有状态恢复合同。
7. 覆盖知识库 OCR 与一般视觉理解的共用适配器回归，但不重构整个学习工作流。使用原始三张图片完成真实识别、结构解析并进入预习。

## 验收标准

- [x] 提供与学习轮次直接关联的实际原因证据，明确是否验证了像素参数假设。
- [x] 参数不兼容场景修复前失败、修复后通过，OCR 和视觉请求构造均覆盖。
- [x] 激活探测与真实图片业务请求不再因参数形态差异产生假通过。
- [x] 原始三张书页成功进入识别和预习阶段，页数、页序及结构化内容可核对。
- [x] 参数错误不再被展示为笼统的稍后重试，超时等错误仍保留合理重试能力。
- [x] 失败、重试与进程恢复不重复书页、不虚假推进阶段、不跨会话引用附件。
- [x] 共用 OCR／视觉调用及运行模型锁回归通过；真实调用和模拟测试分别记录。

## 范围与协作

不切换为另一套隐藏识图模型，不绕过现有主模型验证，不将聊天书页写入全局知识库。本票拥有共享图片请求构造和能力探测；仅修改与本次错误相关的日志及提示，避免全站错误系统重构。

## 执行与验收记录

**分支／提交：** `codex/issue-04-study-image-recognition`（worktree `.worktrees/04-study-image-recognition`）；实现与测试 `37f7de3`，本条记录与留档随后的 docs 提交。基线 `main @ 6939da3`。留档证据：主仓 `.tmp/issue04/`（含 `README.md` 索引；**不含密钥**）。

### 1. 原始图片来源确认

三张照片是**用户原始教材页**，取自真实数据目录的会话附件对象（只读解密后逐张校验 sha256 与库记录一致），复制到 `.tmp/issue04/pages/` 做隔离副本，未改真实库与对象库，未用包含缩略图的页面截图替代：

| 文件 | object_id | sha256 前缀 | 字节 | 附件绑定 |
| --- | --- | --- | --- | --- |
| 1.jpg | `McvuC3ko_M9ob5mQqxO4xw` | `a0c4a9d7` | 703146 | 会话 `0YV6EJm3jw0P3qZlKcc24A`，消息 `yYaCDthrh9wj6WsJh_U6hg`，ordinal 1 |
| 2.jpg | `zGgatwjOm-oVipV6ERJ4gg` | `102cec04` | 705462 | 同上，ordinal 2 |
| 3.jpg | `7za5oeRZHk3n-cUU7UbCNg` | `96b0501e` | 657690 | 同上，ordinal 3 |

### 2. 关联错误证据：像素参数假设**已验证**

失败轮次 `aDwgMOgxkjkjcLQPSTDfzQ`（`evidence-snapshot.txt`）：节点 `study.recognize`、`client_error_400`、984 ms、`model_lock_id=null`、`graph_version=study-pages-v1`；助手消息同为 `client_error_400`，用户可见文案是「在「study.recognize」步骤失败：学习处理模型暂时不可用，请稍后重试。」——该轮次自己没有留下上游摘要（锁缺失，本票一并修掉）。

按工单要求没有把同模型另一条记录当结论：同日的知识库 OCR 锁（`qwen_ocr` → `qwen3.7-plus-2026-05-26`）携带 `Parameter min_pixels must be greater than or equal to 65536`，只作线索。真正的核验在**学习路径实际生效的模型**上用**用户原始第 1 页**逐形态发真实请求（`repro_pixels.py`，输出见 `verify-run.txt`）：

| 形态 | 结果 |
| --- | --- |
| A 修复前适配器默认（`min_pixels=3072` + `max_pixels=8388608`） | **400** `client_error_400`（与失败轮次同码） |
| A2 同默认 + `ocr_options` | **400**（`ocr_options` 不是原因） |
| B 省略两个可选像素参数 | **200**，`image_tokens=2496` |
| C `min_pixels=65536` + 上限 | **200**，`image_tokens=8114`（约 3.25× token 成本） |
| D 仅 `min_pixels=65536` | **200**（默认上限等价） |

⇒ 原因确认：适配器**自己发明**的像素参数被服务端拒绝；且不只是 `min_pixels` 一项进入实测证据。参数不兼容假设成立，同时排除了 `ocr_options` 与鉴权／区域问题（否则不会出现 200）。

### 3. 验证后的参数合同

- 这两个像素参数是**可选参数**：调用方未给出时不发送，由服务端默认值处理（对当前模型实测有效）；调用方显式给出有效值则原样透传。
- 修复取"不发明参数"而不是硬编码阈值：C／D 只证明下限形态可用，**不宣称 65536 适用于所有模型**。
- B 与 C 的 `image_tokens` 不同（2496 vs 8114），说明 B 确实走的是服务端默认，而不是参数被忽略。

### 4. 请求构造差异（共用面，不只改学习调用点）

| 位置 | 修复前 | 修复后 |
| --- | --- | --- |
| `bridges.ai.image_request`（新增） | 无 | 图片 data URL 与内容块构造的唯一来源 |
| `QwenOcrAdapter` / `QwenVisionAdapter` | 各写一份 `_data_url_for_image`；像素参数默认 `3072`／`8388608` 强制写入 | 取共享构造；像素参数仅在调用方给出时写入；按调用方给的 `request_timeout_seconds` 覆盖默认超时 |
| `ModelCapabilityProbe` | 自己拼内容块、不带像素参数（与当时的适配器形态不一致 → 探测通过、业务必然失败）；探测图 8×8 | 同一个 `image_content_part`；探测图改 64×64（8×8 实测被 `height:8 or width:8 must be larger than 10` 拒绝） |
| `bridges.ai.errors` 4xx 文案 | 所有 4xx 一律「模型服务返回错误…请稍后重试」 | 请求拒绝类 4xx 明说「重试不会恢复」并给下一步（能力中立措辞，映射被聊天／知识库／图片／语音共用）；408／429 仍按瞬时处理；401／403、429 在上游已分别归类为 `auth_error`／`rate_limit`，不受影响 |
| 学习流程失败面 | 失败统一显示「稍后重试」；失败锁不落库；围栏 JSON 直接解析失败；图片调用沿用 60 秒默认超时；隐式补拍对空消息也生效 | 共享映射的中文原因＋按可重试性兜底；**失败尝试自己的锁**随消息终态落库（不再把上一次成功的锁当失败证据）；围栏剥离后仍按原 pydantic 合同校验；整页图片调用 180 秒（实测 OCR 30—44 s、视觉 45—59 s）；隐式补拍仅在流程确实等待补拍时生效 |

### 5. 真实学习结果（`verify` 轮次，最终代码）

- 三张原图全部真实识别成功：3 次 `qwen_ocr` + 3 次 `qwen_vision` 均 `status=success`，模型恒为 `qwen3.7-plus-2026-05-26`（`verify-run.txt` 的 invoke 轨迹）。
- 页数与页序：**3 页**，ordinal 1／2／3；页级证据 第1页 12 片段（`photo`+`user`）、第2页 8 片段（`text`,`chart`，书上页码 294）、第3页 7 片段（`text`,`chart`，书上页码 295）。
- 进入预习：用户按流程文档化的路径补录后，阶段 `awaiting_pages` → **`tutoring`**，`wait_reason=None`；知识点 **5 个**、预习问题 **4 条**，回答含「已识别本节范围：…」与逐条问题（`acceptance/verify/report_followup.json`）。
- 材料事实如实记录：第 1 页的印刷页码在照片中**确实不可辨认**（放大只看到背面透印痕迹），模型按合同把它列为看不清项、不猜测，用户据实补录"该位置不可辨认"；第 3 页是 13.2 小节首页，用户说明并用「确认第3页属于本节」决定纳入本次学习范围（小节归属由用户拍板，模型不代决）。页序核对使用可读页码（294→295 升序）。

### 6. 恢复场景（`tests/chat/test_study_recognition_failures.py`）

- 参数错误（不可重试）：失败后 `stage=recognizing`、`pages=[]`、`units=[]`——不标记已识别、不推进阶段；失败锁保留能力 `qwen_ocr`、实际模型、`client_error_400`、`status=blocked` 与运行关联（`message.model_id`／`run_lock_id`）。
- 可重试故障（`transient`）：文案保留重试指引；重试成功后三页齐全并进入预习。
- 部分页失败后重试：只保留真识别成功的第 1 页，重试把已识别页识别为重复并跳过（「检测到1张重复书页，已跳过。」），不追加成第 4 页。
- **进程重启**：同一数据目录重建应用（内存态清空、重新登录）后阶段与页级证据与重启前逐字段一致，重试仍收敛到预习。
- 跨会话附件不引用：由既有回归覆盖（`tests/chat/test_v2_06_file_attachments.py`「其他会话不检索本会话附件」、`tests/chat/test_v2_02_resumable_runs.py` 幂等键仅会话内命中）。
- 首次多页上传不再被上一页吞掉：三页首传且第 1 页有看不清项时仍保留 3 页（旧实现会只剩 2 页）。

### 7. 测试命令与结果

模拟测试（本票改动面）：

- `PYTHONPATH=src pytest tests/ai/test_image_request_contract.py tests/chat/test_study_recognition_failures.py tests/chat/test_error_message_mapping.py -q` → **26 passed**（含修复前形态经两个适配器都 400 的反向断言，证明通过不是空断言）。
- `tests/ai`：分支 **173 passed / 0 failed**；main 同命令 **165 passed / 0 failed**（差值＝本票新增用例）。
- `tests/chat tests/plugins`：分支 **140 failed / 531 passed**，与 main 同命令逐条**名称差集为空**（101 + 39 条既有失败一条不多一条不少）。
- 全量 `PYTHONPATH=src pytest tests/ --basetemp=<仓外> --ignore=tests/humanize_eval --deselect tests/integration/test_runtime_smoke.py::test_start_fails_*（3 条本机挂死）` → **249 failed / 3737 passed / 39 skipped / 3 deselected / 0 error**（18:37）；按套件计数与并在其中的 main 基线一致：chat 101、plugins 39、retirement 11、lifecycle 5、ingestion 5、closeout 6、learning_projects 19、mcp 49 等。
- `ruff check`（本票改动文件）`All checks passed!`；全仓 512 errors 与 main 相同。`mypy src` 98 errors／20 files（check 409→410 个源文件）与 main 相同，本票文件无报错。
- 桌面 UI 未改动（本票只动后端图片请求构造与提示），因此未跑 Next/vitest。

真实调用（与模拟测试分开留档）：`verify-run.txt`（像素合同 A/A2/B/C/D、三张原图识别轨迹、补录后预习）、`acceptance/verify/report.json`、`acceptance/verify/report_followup.json`、`acceptance/final/report*.json`（第一轮对照）、`evidence-snapshot.txt`（失败轮次关联证据）。密钥以外部文件引用传入，未出现在命令行、日志、报告或测试夹具；留档前已删除该临时密钥文件，并核验库中不含密钥与图片 base64。

### 8. 未完成事项与边界

- 第 1 页印刷页码在原始照片中不可辨认（材料本身限制，模型按合同不猜测）；如后续需要书页码，应请用户补拍，本票不做猜测或补全。
- 「三页进入预习」的证据链**包含用户按文档化路径的补录／确认步骤**：识别后流程按设计停在 `awaiting_pages` 等用户澄清，这是既有合同（任何看不清内容必须列入 unclear），不是本票遗留缺陷。
- 每个生成轮次按既有合同只落**最后一条**调用锁，因此 OCR 逐次成功的证据来自运行轨迹与页级证据，而不是锁表逐条记录。
- 本票未合并 main、未推送：等人工复核（本记录状态 `ready-for-human`）后按仓库流程 `--no-ff` 合并。


## Comments

暂无。
