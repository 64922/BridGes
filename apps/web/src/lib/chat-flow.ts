/**
 * 聊天首页 → 对话页的待发送消息通道（Issue 11）。
 *
 * 首页发送时先创建对话，把消息暂存到 sessionStorage 后跳转；对话页在
 * 加载完成且对话为空时消费该消息并自动发送。消费即删除（同步执行），
 * 防止刷新或 StrictMode 双触发导致重复发送。
 */
export function chatPromptKey(conversationId: string): string {
  return `bridges:chat:prompt:${conversationId}`;
}

export function chatAttachmentKey(conversationId: string): string {
  return `bridges:chat:attachments:${conversationId}`;
}

/** Issue 28：首页提交人味化任务时暂存的 SKILL 载荷（对话页消费后删除）。 */
export function chatSkillKey(conversationId: string): string {
  return `bridges:chat:skill:${conversationId}`;
}

/** Issue 29：首页提交生涯规划时暂存的「关闭画像」标记（对话页消费后删除）。 */
export function chatNoProfileKey(conversationId: string): string {
  return `bridges:chat:no-profile:${conversationId}`;
}

/** Issue 31：首页提交图片任务时暂存的图片请求载荷（对话页消费后删除）。 */
export function chatImageKey(conversationId: string): string {
  return `bridges:chat:image:${conversationId}`;
}

/** Issue 32：首页提交视频任务时暂存的视频请求载荷（对话页消费后删除）。 */
export function chatVideoKey(conversationId: string): string {
  return `bridges:chat:video:${conversationId}`;
}

/** Issue 34：插件页「在聊天中使用 humanizer」的意图桥（首页消费后删除）。 */
export function pluginHumanizerKey(): string {
  return "bridges:plugin:open-humanizer";
}
