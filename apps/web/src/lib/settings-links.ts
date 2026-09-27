/**
 * 密钥与模型管理页的分区锚点与「可返回原会话的配置链接」。
 *
 * 通勤消息卡要把用户直接送到缺失的那一组高德凭据，并在配置完成后送回原
 * 会话（原消息与失败结果不动）。锚点字符串因此必须由本模块统一给出，
 * 与 `KeyAndModelSettings` 里各分区的 `id` 取同一组常量，避免两处漂移。
 */

/** 高德 Web 服务（服务端路线与地理编码）凭据分区。 */
export const AMAP_WEB_SERVICE_ANCHOR = "amap-web-service";
/** 高德浏览器地图（页面底图）凭据分区。 */
export const AMAP_BROWSER_MAP_ANCHOR = "amap-browser-map";

/** 可由消息卡直达的凭据分区（只有这两组高德凭据会因缺凭据失败）。 */
export type CredentialAnchor =
  | typeof AMAP_WEB_SERVICE_ANCHOR
  | typeof AMAP_BROWSER_MAP_ANCHOR;

const KEY_MODEL_SETTINGS_PATH = "/account/settings/models";

/**
 * 只接受站内会话路径 `/chat/<id>`。
 *
 * 返回路径来自查询串，直接当 `href` 用会让 `//evil.example`（协议相对地址）
 * 或 `/\evil.example`（反斜杠在 URL 解析里等同斜杠）变成外站跳转；携带查询串
 * 或锚点的取值也不是「原会话」本身，一律当没有返回路径处理。
 */
export function safeChatReturnPath(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/chat/")) return null;
  const rest = value.slice("/chat/".length);
  if (!rest || rest.startsWith("/") || /[?#\s\\\u0000-\u001f\u007f]/.test(rest)) return null;
  return value;
}

/** 密钥与模型管理页链接：带凭据分区锚点，有原会话时一并带返回路径。 */
export function credentialSettingsHref(
  anchor: CredentialAnchor,
  returnTo?: string | null
): string {
  const back = safeChatReturnPath(returnTo);
  const query = back ? `?return_to=${encodeURIComponent(back)}` : "";
  return `${KEY_MODEL_SETTINGS_PATH}${query}#${anchor}`;
}
