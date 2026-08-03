/**
 * 新聊天空白态输入区顶部轮换的学习名言（恰好五条，Issue 13）。
 *
 * 逐条出处、事实与版权核查记录在
 * `docs/design/0004-new-chat-blank-state-quotes.md`：
 * 五句均为作者逝世远超版权保护期的中国古典文献原文，属公有领域，
 * 不含任何第三方品牌的专有文案。
 */
export interface LearningQuote {
  text: string;
  /** 展示用出处（「—— 」前缀由组件添加） */
  source: string;
}

export const LEARNING_QUOTES: readonly LearningQuote[] = [
  { text: "学而不思则罔，思而不学则殆。", source: "《论语·为政》" },
  { text: "知之者不如好之者，好之者不如乐之者。", source: "《论语·雍也》" },
  { text: "读书破万卷，下笔如有神。", source: "杜甫《奉赠韦左丞丈二十二韵》" },
  { text: "吾生也有涯，而知也无涯。", source: "《庄子·养生主》" },
  { text: "少壮不努力，老大徒伤悲。", source: "汉乐府《长歌行》" },
] as const;

/** 轮换节奏：与参考网页的可变文字机制一致（见设计文档 0004 的基线记录） */
export const QUOTE_ROTATE_MS = 8000;
