# Issue 46 — 前端：一个数据获取 module，组件不再自备 fetch 生命周期

Status: completed（实施 + 双轴审查 + 全量回归后提交）
Type: task
来源：架构评审候选 5（Worth exploring）。评审报告：architecture-review-20260806-231807.html
词汇：module / interface / depth / seam / adapter / leverage / locality
依赖类别：local-substitutable（浏览器 fetch 真 adapter / 内存 fake 测试 adapter）

## 问题（当前状态，含证据）

- `apps/web/src/lib/api.ts`：2756 行、~130 个导出函数——**集中但扁平**的请求层，
  每个组件直接调用并自管状态。
- 42 处 `loading` state、260 处 `error` state 分布在 52 个文件：每个组件复制
  「fetch → setLoading → setError → retry」模式（NewChatHome、Composer、
  PluginCenter、McpCenter、ProfileCenter、TaskSchedule、DataPrivacy、
  knowledge-base、search-page 等）。
- `ImageTaskCard.tsx` / `VideoTaskCard.tsx`：两份 ~80 行几乎相同的轮询状态机
  （5s 间隔、useEffect+useRef、卸载清理、成功回调）。
- 手写 SSE 解析 `readSseStream()`（api.ts:928-961）；`downloadChatAttachment()`
  直接操作 DOM（<a> + createObjectURL）；`uploadRawBytes()` 用 XHR。
- **前端零单测**：无 jest/vitest 配置；组件不注入数据依赖，无法脱离网络测试。
- e2e 用手写 mock 状态机复制服务器行为（issue11-chat.spec.ts 等），脆弱。

删除测试：删掉某个组件的 loading/error 块，复杂度不转移（浅），但**系统级**成本
是每加一个页面就复制一份，且每份都是「新请求前忘记清 error」之类 bug 的机会。

## 方案：深数据获取 module

### 1. 新模块 `apps/web/src/lib/data.ts`（小 interface，内部深实现）

```ts
// 对外 interface（组件只声明要什么数据，不管怎么拿）
export function useApiQuery<T>(key: string, fetcher: () => Promise<T>, opts?: {
  pollMs?: number;              // 轮询任务（图片/视频任务卡）
  retry?: number;               // 失败重试次数
}): { data: T | null; error: ApiError | null; loading: boolean; reload: () => void }

export function useApiMutation<TArgs, TResult>(
  fetcher: (args: TArgs) => Promise<TResult>
): { run: (args: TArgs) => Promise<TResult>; pending: boolean; error: ApiError | null }

export class ApiError extends Error { code?: string }
```

内部实现（深的部分）：
- 统一 loading/error/reload 生命周期（同一文件，一处实现）；
- `pollMs` 轮询循环（卸载清理、重试、stop-on-unmount）——取代两份任务卡轮询；
- `createSseReader()`：把 `readSseStream` 收进内部（chat 页专用钩子
  `useChatStream` 后续可加，本期先不碰流式页）。

### 2. Seam：客户端接口可替换（解锁单测）

- `lib/api.ts` 导出保持不动（它是 transport 实现），`data.ts` 经一个窄 seam
  依赖：`type ApiClient = { request<T>(path, init): Promise<T> }`。
- 真 adapter：现有 fetch 包装；测试 adapter：内存 fake（测试里注册路由表）。
- 组件不再 import api.ts 函数而是 import data.ts 钩子 → 组件可单测。

### 3. 迁移顺序（控制回归面）

1. 建 `data.ts` + `ApiClient` seam + vitest 配置（首次单测落地：hook 生命周期、
   pollMs 轮询、错误重试——用内存 adapter）。
2. 试点两个轮询卡：`ImageTaskCard` / `VideoTaskCard` → `useApiQuery(..., {pollMs:5000})`，
   删各自 ~80 行状态机。
3. 高流量请求组件逐个迁移（先 TaskSchedule / PluginCenter / McpCenter /
   ProfileCenter / knowledge-base / search-page），每批跑 e2e。
4. 其余组件按需迁移（本 issue 不承诺 52 个全迁；以轮询与高频页为主）。

### 边界（本期不做）

- chat 流式页的 SSE 主流程保持现状（事件多、状态复杂，另立 issue）；
  但 `readSseStream` 移入 data.ts 内部由 `useChatStream` 独占。
- 不引入 react-query 等新依赖：仓库无此依赖，`data.ts` 手写 ~200 行即可覆盖需求。

### 验收标准

- [ ] `apps/web/src/lib/data.ts` + `ApiClient` seam 落地
- [ ] vitest 配置 + 首个前端单测套件（hook 生命周期/轮询/重试，内存 adapter）
- [ ] ImageTaskCard/VideoTaskCard 轮询迁移完成，删掉重复状态机
- [ ] 至少 5 个高频组件迁移到 `useApiQuery/useApiMutation`
- [ ] e2e 全量通过（playwright）；`npm run build` 通过
- [ ] ruff 无关；前端 lint（如配置）通过

### 风险与开放问题

- e2e 的 page.route mock 是针对 api.ts 具体路径的：迁移后请求路径不变（transport
  不变），mock 不受影响——这是迁移顺序上先建 seam 后动组件的原因。
- 轮询迁移最敏感：`pollMs` 语义必须与原实现一致（首次立即拉取、间隔、卸载清理、
  成功停轮询）——用单测先锁死再迁移。
- `ApiClient` 的类型推导：api.ts 的函数返回值大多来自 generated.ts 类型，
  fetcher 透传即可，无需重写请求层。

## 实施记录（2026-08-07，双轴审查后提交）

**验收对照：**

- [x] `apps/web/src/lib/data.ts` + `ApiClient` seam（窄接口 + 内存 fake 测试
      adapter；真 adapter 即 api.ts 的 fetch 包装，组件以 fetcher 闭包使用）
- [x] vitest 配置（`vitest.config.ts` + `npm run test:unit`）+ 首个前端单测
      套件 19 条：hook 生命周期 / reload 可等待 / enabled 守卫 / 轮询
      （首拉、间隔、停轮询、失败静默、首载失败置错、卸载清理）/ 重试 /
      mutation pending-error-rethrow，全部走内存 adapter
- [x] ImageTaskCard / VideoTaskCard 轮询迁移 `useApiQuery(..., {pollMs:5000})`，
      各删 ~80 行状态机；取消/重试/删除改 `useApiMutation`
- [x] 6 个高频组件迁移：TaskSchedule / PluginCenter / McpCenter /
      ProfileCenter / knowledge-base（含 2.5s 轮询）/ search-page（含空查询
      守卫与竞态守卫删除）——超过验收要求的 5 个
- [x] `npm run build`、前端 lint（仅既有 warning）、`tsc --noEmit` 通过；
      e2e 全量 272 通过（见下「e2e 基线」）
- [x] ruff 无关（纯前端改动）

**接口超出 spec 草图（均为迁移必要，已在 data.ts JSDoc 记档）：**

- `stopWhen`：轮询「成功停轮询」验收项必须（hook 无法从数据推断终态）；
- `enabled`：search-page 空查询守卫（hook 不可条件调用）；
- `isFetching`：区分「首次加载」与「任意请求在途」（search 页"正在搜索…"）；
- `reload(): Promise<void>`：变更后「先等数据就位再继续」的调用方 await
  （ProfileCenter 对话框关闭时机依赖；否则 版本历史 会捕获到旧记录——issue25
  e2e 抓出的真实竞态）。

**实现语义与原来的差异（均有理由，记档）：**

1. **轮询间隔为「结算后 + pollMs」而非墙钟固定间隔**：setTimeout 链避免请求
   重叠（原 setInterval 在请求超时时会叠请求）；knowledge-base 原实现本就是
   setTimeout 链（2.5s 结算后），任务卡的 setInterval 语义在 5s 低频下无差异。
2. **任务卡初始即终态时仍发一次请求**（原实现短路不轮询也不请求）：一次幂等
   GET，返回同一终态；换取 hook 无条件的简单语义。
3. **消息投影刷新不再覆盖轮询数据**（原 `setTask(initialTask)` prop 恒赢）：
   服务端任务表是权威，轮询数据更近真相；换任务（key 变化）由
   `polled?.task_id === initialTask.task_id` 守卫防串台。
4. **首载失败（pollMs 模式）置 error 供错误页展示，轮询继续可自动恢复**：
   修复评审发现的「pollMs 首载失败被静默吞掉 → 无限转圈、错误分支死代码」；
   知识库首载失败不再静默（与原件 reload(false) 置错一致），且比原件多出
   自动恢复。
5. **TaskSchedule 的 onSaved/onChanged 由乐观本地更新改为整页重取**：与
   ProfileCenter 等一致的服务端权威语义；重取失败会进整页错误态（原验证轮询
   的 load 失败同样如此，非新类状态）。
6. **createSseReader/readSseStream 未移入 data.ts**：spec 边界节自相矛盾
   （「本期先不碰流式页」vs「readSseStream 移入 data.ts」），且迁移会引入
   api.ts ↔ data.ts 循环依赖；SSE 重构归 Issue 47（单一流事件 adapter）。

**e2e 基线（相对 HEAD=44feca2 的既有问题，与本次改动无关）：**

- issue04 附件文件控件用例：`git stash` A/B 验证在基线同样失败（未改任何相关
  文件）；issue08 视觉回归：基线同样失败且 diff 区域与本次运行完全一致。
- issue30 朗读用例：并行负载下偶发超时，单独运行 11/11 通过。
- 本次改动相关的 issue12（首帧闪内容）、issue35（并行时序）、issue38（加载态
  扫描）已修复——根因是 hook 首帧 `loading=false`（isFetching 初始值改为 true）。

**审查残余（判断性，未改）：**

- 任务卡三连 `useApiMutation` + busy/actionError 合并呈两份同类形状（评审建
  议抽共用钩子）；两卡领域不同、状态集不同，抽钩子需类型体操，收益有限，留待
  第三个轮询消费者出现再抽。
- `toApiError` 兜底文案「请求失败。」压平逐页语境兜底；仅对非 Error 抛出生效，
  实际极少触发。
- data.ts 转发导出 `ApiError`（spec 接口要求 data.ts 拥有 ApiError；真实类在
  api.ts，转发保持 instanceof 与 classifyApiError 兼容）。
