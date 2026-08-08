/**
 * 聊天首页 → 对话页的跨页意图桥。
 *
 * Issue 03：首页发送第一条消息或调用任一功能已改为「原子首轮」命令——
 * 服务端在同一事务内创建会话、用户消息、助手占位与 queued 运行并返回
 * 完整投影，客户端收到成功响应后再导航；sessionStorage 不再承担业务
 * 真相，此处仅保留非消息类的浏览器内意图（插件中心 → 人味化对话框）。
 */
/** Issue 34：插件页「在聊天中使用 humanizer」的意图桥（首页消费后删除）。 */
export function pluginHumanizerKey(): string {
  return "bridges:plugin:open-humanizer";
}
