"""启动流程和设置 API 共用的凭据标识。"""

RUNTIME_TAVILY_CREDENTIAL_ID = "global-tavily-api-key"
SETTINGS_TAVILY_CREDENTIAL_ID = "settings-tavily-api-key"
AMAP_WEB_SERVICE_CREDENTIAL_ID = "amap-web-service-key"
AMAP_BROWSER_MAP_CREDENTIAL_ID = "amap-browser-map-credentials"
#: 全局百炼运行凭据（ADR-0024）：交互式首启写入、设置页更换后覆盖同一项，
#: 因此下次 ``BridGes start`` 解析凭据时直接取到最新值（V2 Issue 09）。
GLOBAL_QWEN_CREDENTIAL_ID = "global-qwen-api-key"

#: 凭据条目键（工单 01）：解析器缓存、设置页卡片状态与地图代理回写共用同一组
#: 字面量，避免同一张卡在三处各写一个键而读错。
QWEN_ITEM = "qwen"
TAVILY_ITEM = "tavily"
AMAP_WEB_SERVICE_ITEM = "amap_web_service"
AMAP_BROWSER_MAP_ITEM = "amap_browser_map"
