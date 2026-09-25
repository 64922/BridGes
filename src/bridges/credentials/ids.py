"""启动流程和设置 API 共用的凭据标识。"""

RUNTIME_TAVILY_CREDENTIAL_ID = "global-tavily-api-key"
SETTINGS_TAVILY_CREDENTIAL_ID = "settings-tavily-api-key"
AMAP_WEB_SERVICE_CREDENTIAL_ID = "amap-web-service-key"
AMAP_BROWSER_MAP_CREDENTIAL_ID = "amap-browser-map-credentials"
#: 全局百炼运行凭据（ADR-0024）：交互式首启写入、设置页更换后覆盖同一项，
#: 因此下次 ``BridGes start`` 解析凭据时直接取到最新值（V2 Issue 09）。
GLOBAL_QWEN_CREDENTIAL_ID = "global-qwen-api-key"
