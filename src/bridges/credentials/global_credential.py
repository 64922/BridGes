"""全局百炼运行凭据合同（GQ-01）。

正式运行的唯一 Qwen 认证来源：``BRIDGES_QWEN_API_KEY`` 或
``BRIDGES_QWEN_API_KEY_FILE`` 都解析为同一 Secret 配置（``config.Settings``
已统一加载两者）。本模块是"全局凭据"的单一事实源——CLI 启动硬门、
``doctor``、API 组合根与后台执行器都从这里读取与校验，不再各自发明
``settings.qwen_api_key`` 检查逻辑。

合同要点：
- Key 正文绝不进入异常消息、日志或 repr（只引用环境变量名与配置指引）；
- 校验只验证"必需值可读取"，不发起任何可能计费的探测；
- ``test`` 环境由确定性适配器驱动，不要求全局 Key。
"""

from __future__ import annotations

from pydantic import SecretStr

from bridges.config import Settings

#: 面向运维的中文配置指引（不含任何秘密正文），供启动硬门与 doctor 复用。
GLOBAL_QWEN_KEY_GUIDANCE = (
    "未配置全局百炼运行凭据。请在启动前设置环境变量 BRIDGES_QWEN_API_KEY，"
    "或设置 BRIDGES_QWEN_API_KEY_FILE 指向含密钥的只读文件（文件引用优先"
    "用于容器或长期部署），然后重新执行 BridGes start。"
)


class GlobalQwenCredentialError(Exception):
    """全局百炼运行凭据缺失或不可读；消息只含配置指引，绝不含 Key 正文。"""


def resolve_global_qwen_key(settings: Settings) -> SecretStr | None:
    """返回全局百炼运行凭据；未配置或为空时返回 None。

    只读取已经加载的配置，不访问网络、不触发探测。
    """
    key = settings.qwen_api_key
    if key is None or not key.get_secret_value():
        return None
    return key


def is_global_qwen_key_configured(settings: Settings) -> bool:
    """全局百炼运行凭据是否已配置（非空即视为已配置）。"""
    return resolve_global_qwen_key(settings) is not None


def require_global_qwen_key(settings: Settings) -> SecretStr:
    """返回全局百炼运行凭据；缺失时抛出不含秘密正文的中文错误。"""
    key = resolve_global_qwen_key(settings)
    if key is None:
        raise GlobalQwenCredentialError(GLOBAL_QWEN_KEY_GUIDANCE)
    return key
