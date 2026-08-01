"""Root-level test isolation fixtures (Issue 02 测试隔离基线).

目标：每个测试从同一确定性环境开始，不受用户环境变量、仓库 ``.env`` 或
前一个测试残留状态的影响。所有覆盖通过 ``monkeypatch`` 应用，测试结束后
自动恢复，因此测试内部自己的环境变量修改不受干扰。
"""

from __future__ import annotations

import os

import pytest

from science_companion.config import get_settings

# 显式覆盖的运行时配置。任何来自用户 shell 环境或仓库 ``.env`` 的同名值
# 都会被这些确定性值压过，保证测试不读取真实凭据、不录制真实网络调用、
# 不连接真实数据库：
#   - QWEN_API_KEY/QWEN_RECORD_CASSETTES：真实适配器注册与录制由
#     QWEN_FORCE_STUB=true 双保险拦截；置空的直接凭据让 .env 无从生效
#   - <FIELD>_FILE 秘密文件引用一并置空：config._load_secret_files 直接读
#     环境变量并覆盖字段值，若开发机按推荐路径设置了 *_FILE，真实密钥或
#     真实 DATABASE_URL 会绕过上面的直接键进入测试
#   - DATABASE_URL/SECRET_KEY 置空：绝不落盘真实数据库文件
#   - BUILD_DIGEST 置空：等价于未设置（evaluation._build_digest 有空值回退）
_DETERMINISTIC_ENV: dict[str, str] = {
    "SCIENCE_COMPANION_ENVIRONMENT": "test",
    "SCIENCE_COMPANION_QWEN_FORCE_STUB": "true",
    "SCIENCE_COMPANION_QWEN_API_KEY": "",
    "SCIENCE_COMPANION_QWEN_API_KEY_FILE": "",
    "SCIENCE_COMPANION_QWEN_WORKSPACE_ID": "",
    "SCIENCE_COMPANION_QWEN_REGION": "cn-beijing",
    "SCIENCE_COMPANION_QWEN_CASSETTE_DIR": "",
    "SCIENCE_COMPANION_QWEN_RECORD_CASSETTES": "false",
    "SCIENCE_COMPANION_DATABASE_URL": "",
    "SCIENCE_COMPANION_DATABASE_URL_FILE": "",
    "SCIENCE_COMPANION_SECRET_KEY": "",
    "SCIENCE_COMPANION_SECRET_KEY_FILE": "",
    "SCIENCE_COMPANION_REDIS_URL": "",
    "SCIENCE_COMPANION_REDIS_URL_FILE": "",
    "SCIENCE_COMPANION_OBJECT_STORAGE_URL": "",
    "SCIENCE_COMPANION_OBJECT_STORAGE_URL_FILE": "",
    "SCIENCE_COMPANION_BUILD_DIGEST": "",
}

# 显式启用真实 Qwen 冒烟（重录 cassette）所需的环境变量开关。只有用户在
# 测试进程外显式设置这两个键（如 ``SCIENCE_COMPANION_QWEN_RECORD_CASSETTES=true``
# 且提供真实 ``SCIENCE_COMPANION_QWEN_API_KEY``）时，对应键才不被覆盖——
# 这是验收标准中"真实 Qwen 冒烟测试必须显式启用"的入口。凭据只接受
# 环境变量显式提供，仓库 ``.env`` 里的值仍被确定性键压过。
_RECORD_CASSETTES_KEY = "SCIENCE_COMPANION_QWEN_RECORD_CASSETTES"
_QWEN_API_KEY_KEY = "SCIENCE_COMPANION_QWEN_API_KEY"


def _explicit_real_recording_requested() -> bool:
    """用户是否显式要求真实录制模式（RECORD=true 且环境变量里给了真实密钥）。"""
    record = os.environ.get(_RECORD_CASSETTES_KEY, "").strip().lower()
    return record in {"true", "1", "yes"} and bool(os.environ.get(_QWEN_API_KEY_KEY))


@pytest.fixture(autouse=True)
def _deterministic_test_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每个测试前重置运行时配置，测试后由 monkeypatch 自动恢复。

    显式真实录制模式（见 ``_explicit_real_recording_requested``）下放行
    RECORD 与 API_KEY 两个键；其余键（含 FORCE_STUB）仍强制覆盖，因此
    其他测试与 create_app 路径保持确定性 stub，不受录制开关影响。
    """
    real_recording = _explicit_real_recording_requested()
    for key, value in _DETERMINISTIC_ENV.items():
        if real_recording and key in {
            _RECORD_CASSETTES_KEY,
            _QWEN_API_KEY_KEY,
        }:
            continue
        monkeypatch.setenv(key, value)
    # get_settings 是进程级 lru_cache；清掉缓存使下一次读取拿到
    # 上面的确定性环境，而不是前一个测试缓存的结果。
    get_settings.cache_clear()
