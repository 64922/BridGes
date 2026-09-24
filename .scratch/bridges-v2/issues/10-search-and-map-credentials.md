# 10 — Tavily 与高德凭据管理

**What to build:** 用户可在设置中查看 Tavily 与高德的配置状态，并验证、更换相应凭据；失败时原配置继续有效。

**Blocked by:** None — can start immediately

**Status:** ready-for-human

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [x] Tavily 与高德凭据各自展示状态、验证进度和错误；旧明文不回显、不预填，也不进入响应或日志。
- [ ] 候选凭据通过最小只读探测后原子替换，失败保留旧引用；终端首次配置的 Qwen 与 Tavily 流程仍可使用。
- [x] 高德服务端路线、浏览器地图及其安全配置按实际用途分组；浏览器端不暴露应保密的安全密钥。
- [ ] 无高德配置时只让通勤能力提示配置缺口，不影响普通聊天和其他模块。

## Comments

设置页、已认证凭据 API、加密存储与 Tavily／高德只读探针已实现，旧凭据在候选探测失败时保留。浏览器地图目前只探测 JS API 加载器，安全码与 Key 的配对要等受保护的地图代理请求才能实测；当前代码没有通勤模块分发点，因此本票尚未实现通勤缺配置提示。设置页明确披露了第一项限制，普通聊天未接入高德配置。

高德[官方安全方案](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)要求浏览器通过 `serviceHost` 请求 `/_AMapService`，由服务端代理向上游追加 `jscode`；[官方错误码说明](https://lbs.amap.com/api/javascript-api-v2/guide/abc/errorcode)将 `INVALID_USER_SCODE` 定义为安全码与 Key 不匹配。因此加载器连通性探测不能作为安全码配对已验证的依据。
