/**
 * 密钥与模型管理页共用文案与格式化（V2 Issue 09）。
 *
 * 指引文案与后端 ``GLOBAL_QWEN_KEY_SETTINGS_GUIDANCE`` 逐字一致：密钥缺失
 * 时字段附近的说明与接口返回的中文原因必须是同一句话，避免两处提示互相
 * 矛盾。
 */

/** 密钥未配置时的操作顺序说明（与后端返回的指引同一句口径）。 */
export const QWEN_CREDENTIAL_FIRST_GUIDANCE =
  "当前没有可用的 Qwen 密钥：请先在本页「Qwen 凭据」中输入密钥并验证保存，再填写并验证主模型 ID。";

/** 末次验证时间的展示文案（无记录时明确说明，不显示空值）。 */
export function formatValidationTime(value: string | null | undefined): string {
  if (!value) return "尚无验证记录";
  return `最近验证：${new Date(value).toLocaleString("zh-CN")}`;
}
