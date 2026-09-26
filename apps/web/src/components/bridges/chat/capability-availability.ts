/**
 * 账户级能力可用性（Issue 30 引入，Issue 21 移到独立模块）。
 *
 * 原先定义在朗读控件里，朗读入口随 ADR-0030 退役后仍被输入区（听写）
 * 等能力入口复用，故独立成中立模块。
 */
export interface CapabilityAvailability {
  available: boolean;
  /** 能力不可用时的中文原因（用于禁用入口说明）。 */
  reason?: string;
}
