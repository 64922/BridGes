"""全局百炼运行凭据合同（GQ-01）。

正式运行的唯一 Qwen 认证来源：``BRIDGES_QWEN_API_KEY`` 或
``BRIDGES_QWEN_API_KEY_FILE`` 都解析为同一 Secret 配置（``config.Settings``
已统一加载两者）。本模块是"全局凭据"的单一事实源——CLI 启动硬门、
``doctor``、API 组合根与后台执行器都从这里读取与校验，不再各自发明
``settings.qwen_api_key`` 检查逻辑。

合同要点：
- Key 正文绝不进入异常消息、日志或 repr（只引用环境变量名与配置指引）；
- 校验只验证"必需值可读取"，不发起任何可能计费的探测；
- ``test`` 环境由确定性适配器驱动，不要求全局 Key；
- V2 Issue 09 起，设置页可在运行期更换该凭据：候选密钥先经最小只读探测，
  成功后才整体替换（保存为 ``GLOBAL_QWEN_CREDENTIAL_ID``，与交互式首启同一
  项），失败保留旧凭据；解析优先级仍是文件/环境变量优先、凭据库兜底。
"""

from __future__ import annotations

from pydantic import SecretStr

from bridges.config import Settings
from bridges.credentials.ids import GLOBAL_QWEN_CREDENTIAL_ID
from bridges.credentials.store import CredentialStoreError, CredentialStorePort

#: 面向运维的中文配置指引（不含任何秘密正文），供启动硬门与 doctor 复用。
GLOBAL_QWEN_KEY_GUIDANCE = (
    "未配置全局百炼运行凭据。请在启动前设置环境变量 BRIDGES_QWEN_API_KEY，"
    "或设置 BRIDGES_QWEN_API_KEY_FILE 指向含密钥的只读文件（文件引用优先"
    "用于容器或长期部署），然后重新执行 BridGes start。"
)

#: 设置页缺少密钥且无法探测时的中文指引（含操作顺序）。
GLOBAL_QWEN_KEY_SETTINGS_GUIDANCE = (
    "当前没有可用的 Qwen 密钥：请先在本页「Qwen 凭据」中输入密钥并验证保存，"
    "再填写并验证主模型 ID。"
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


def resolve_settings_qwen_key(
    settings: Settings | None, credential_store: CredentialStorePort | None
) -> SecretStr | None:
    """解析设置页可用的 Qwen 凭据：文件/环境变量优先，其次凭据库。

    布局与 Tavily/高德设置一致（``SETTINGS_*`` 命名空间的读法）：显式配置的
    环境变量或 ``_FILE`` 引用优先；否则读取交互式首启或设置页保存的全局凭据。
    凭据库不可读时返回 None（调用方给出"先配置密钥"的操作顺序提示），绝不
    把凭据库异常升级为请求失败。
    """
    if settings is not None:
        configured = resolve_global_qwen_key(settings)
        if configured is not None:
            return configured
    if credential_store is None:
        return None
    try:
        stored = credential_store.get(GLOBAL_QWEN_CREDENTIAL_ID)
    except (CredentialStoreError, OSError):
        return None
    if stored is None or not stored.get_secret_value():
        return None
    return stored
