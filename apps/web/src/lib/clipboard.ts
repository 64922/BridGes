/**
 * 尝试把文本写入系统剪贴板。
 *
 * 模板必须根据真实结果显示成功或失败，不能在浏览器拒绝权限时仍提示“已复制”。
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (!navigator.clipboard?.writeText) return false;

  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
