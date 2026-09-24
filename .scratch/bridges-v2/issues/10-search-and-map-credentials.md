# 10 — Tavily 与高德凭据管理

**What to build:** 用户可在设置中查看 Tavily 与高德的配置状态，并验证、更换相应凭据；失败时原配置继续有效。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

**实施前必读：** [ADR-0030](../../../docs/adr/0030-v2-campus-assistant-and-approved-desktop-interaction.md)、[V2 设计索引](../../../docs/v2/README.md)及索引所列六份设计文档；先核对阻塞票已验收。

- [ ] Tavily 与高德凭据各自展示状态、验证进度和错误；旧明文不回显、不预填，也不进入响应或日志。
- [ ] 候选凭据通过最小只读探测后原子替换，失败保留旧引用；终端首次配置的 Qwen 与 Tavily 流程仍可使用。
- [ ] 高德服务端路线、浏览器地图及其安全配置按实际用途分组；浏览器端不暴露应保密的安全密钥。
- [ ] 无高德配置时只让通勤能力提示配置缺口，不影响普通聊天和其他模块。
