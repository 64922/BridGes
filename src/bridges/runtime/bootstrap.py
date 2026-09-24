"""本机托管启动的配置、凭据与 Web 构建 bootstrap。

``BridGes start`` 是普通桌面用户的唯一启动接口时，本模块把首次初始化、
可恢复的 Web 构建和安装级 Qwen 凭据收敛到一个小的公共接口。容器、CI 与
手工运维仍可继续直接通过 ``BRIDGES_*`` 环境变量/文件引用运行，不依赖本模块
的交互流程。
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr, ValidationError

from bridges.config import Settings, secret_file_reference
from bridges.credentials.ids import (
    RUNTIME_TAVILY_CREDENTIAL_ID,
    SETTINGS_TAVILY_CREDENTIAL_ID,
)
from bridges.credentials.store import CredentialStoreError, OsCredentialStore
from bridges.persistence import PersistenceError, resolve_database_path

CONFIG_SCHEMA_VERSION = 1
RUNTIME_QWEN_CREDENTIAL_ID = "global-qwen-api-key"
_SECRET_ENV_FIELDS = (
    "BRIDGES_SECRET_KEY",
    "BRIDGES_SECRET_KEY_FILE",
    "BRIDGES_DATABASE_URL",
    "BRIDGES_DATABASE_URL_FILE",
    "BRIDGES_QWEN_API_KEY",
    "BRIDGES_QWEN_API_KEY_FILE",
    "BRIDGES_TAVILY_API_KEY",
    "BRIDGES_TAVILY_API_KEY_FILE",
    "BRIDGES_AMAP_WEB_SERVICE_KEY",
    "BRIDGES_AMAP_JS_API_KEY",
    "BRIDGES_AMAP_SECURITY_JS_CODE",
    "BRIDGES_AMAP_WEB_SERVICE_KEY_FILE",
    "BRIDGES_AMAP_JS_API_KEY_FILE",
    "BRIDGES_AMAP_SECURITY_JS_CODE_FILE",
    "SCIENCE_COMPANION_SECRET_KEY",
    "SCIENCE_COMPANION_SECRET_KEY_FILE",
    "SCIENCE_COMPANION_DATABASE_URL",
    "SCIENCE_COMPANION_DATABASE_URL_FILE",
    "SCIENCE_COMPANION_QWEN_API_KEY",
    "SCIENCE_COMPANION_QWEN_API_KEY_FILE",
    "SCIENCE_COMPANION_TAVILY_API_KEY",
    "SCIENCE_COMPANION_TAVILY_API_KEY_FILE",
    "SCIENCE_COMPANION_AMAP_WEB_SERVICE_KEY",
    "SCIENCE_COMPANION_AMAP_JS_API_KEY",
    "SCIENCE_COMPANION_AMAP_SECURITY_JS_CODE",
    "SCIENCE_COMPANION_AMAP_WEB_SERVICE_KEY_FILE",
    "SCIENCE_COMPANION_AMAP_JS_API_KEY_FILE",
    "SCIENCE_COMPANION_AMAP_SECURITY_JS_CODE_FILE",
)


class BootstrapError(Exception):
    """本机托管启动无法安全完成；消息不得包含任何秘密正文。"""


class RuntimeCredentialStorePort(Protocol):
    """安装级秘密存储的最小接口。"""

    def get(self, account_id: str) -> SecretStr | None:
        ...

    def save(self, account_id: str, secret: SecretStr) -> None:
        ...


CommandRunner = Callable[[Sequence[str], Path, Mapping[str, str]], None]
Prompt = Callable[[str], str]
Emitter = Callable[[str], None]


@dataclass(frozen=True)
class PreparedRuntime:
    """已经完成本机初始化、可交给 ``start`` 启动的运行时。"""

    settings: Settings
    app_home: Path
    data_dir: Path
    web_dir: Path
    web_artifact: Path
    api_env: Mapping[str, str] = field(repr=False)
    worker_env: Mapping[str, str] = field(repr=False)
    scheduler_env: Mapping[str, str] = field(repr=False)
    web_env: Mapping[str, str] = field(repr=False)


class LocalRuntimeBootstrap:
    """本机托管启动模块。

    启动编排接口是 :meth:`prepare`；文件系统、npm、凭据库和终端提示均可由
    调用方注入替身，因此首次启动和重复启动行为可以在不拉起真实服务的情况下
    通过同一接口测试。Web 环境清理函数作为 CLI 的独立适配端口导出。
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        app_home: Path | None = None,
        command_runner: CommandRunner | None = None,
        credential_store: RuntimeCredentialStorePort | None = None,
        prompt: Prompt | None = None,
        emit: Emitter | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        configured_home = os.environ.get("BRIDGES_HOME", "").strip()
        self.app_home = (
            app_home or (Path(configured_home) if configured_home else default_app_home())
        ).resolve()
        self.command_runner = command_runner or _run_command
        self.credential_store = credential_store or OsCredentialStore(
            data_dir=self.app_home, namespace="runtime"
        )
        self.prompt = prompt or getpass.getpass
        self.emit = emit or (lambda _message: None)

    def prepare(self, *, interactive: bool) -> PreparedRuntime:
        """准备一次 ``desktop`` profile 启动，失败时不启动任何服务。"""
        web_dir = self.repo_root / "apps" / "web"
        self._validate_web_source(web_dir)
        external_settings = self._load_external_settings()

        # 非交互调用（CI、无 TTY 的服务管理器）不能创建配置或构建后才发现
        # 缺少凭据，否则每次失败都会留下半成品状态。交互式首次启动仍按
        # “先准备 Web，再询问 Qwen Key”的用户流程执行。
        qwen_key: SecretStr | None = None
        qwen_source = ""
        tavily_key: SecretStr | None = None
        tavily_source = ""
        if not interactive:
            qwen_key, qwen_source = self._resolve_qwen_key(
                external_settings, interactive=False
            )
            # Issue 01：Tavily Key 与 Qwen 同构解析，但缺 Key 不阻塞启动——
            # 应用可以正常启动，联网搜索入口返回「未配置搜索凭据」投影。
            tavily_key, tavily_source = self._resolve_tavily_key(
                external_settings, interactive=False
            )

        try:
            self.app_home.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BootstrapError(
                f"无法创建本机托管目录，请检查目录权限：{self.app_home}"
            ) from exc

        local_config = self._load_config()
        environment = _environment_name()
        database_url, data_dir, generated_database = self._resolve_database(
            local_config, external_settings
        )
        secret_key, secret_key_file, generated_secret = self._resolve_secret(
            local_config, data_dir, generated_database, external_settings
        )
        database_config: str | None = database_url
        if external_settings.database_url is not None:
            configured_database = local_config.get("database_url")
            database_config = (
                configured_database
                if isinstance(configured_database, str) and configured_database
                else None
        )
        secret_config_file = secret_key_file
        if external_settings.secret_key is not None:
            configured_secret_file = local_config.get("secret_key_file")
            secret_config_file = (
                Path(configured_secret_file).resolve()
                if isinstance(configured_secret_file, str) and configured_secret_file
                else None
            )
        self._write_config(
            database_url=database_config,
            data_dir=data_dir,
            secret_key_file=secret_config_file,
        )

        web_api_base_url = _web_api_base_url(external_settings)
        self._ensure_web_build(web_dir, api_base_url=web_api_base_url)
        if interactive:
            qwen_key, qwen_source = self._resolve_qwen_key(
                external_settings, interactive=True
            )
            # Issue 01：交互式首次安装依次 prompt Qwen Key 与 Tavily Key。
            tavily_key, tavily_source = self._resolve_tavily_key(
                external_settings, interactive=True
            )

        try:
            settings = Settings(
                environment=environment,
                database_url=SecretStr(database_url),
                secret_key=SecretStr(secret_key) if secret_key else None,
                qwen_api_key=qwen_key,
                tavily_api_key=tavily_key,
            )
        except (ValidationError, ValueError) as exc:
            raise BootstrapError(f"本机托管配置无法加载：{exc}") from exc

        api_env = self._runtime_env(
            database_url=database_url,
            secret_key=secret_key,
            secret_key_file=secret_key_file,
            qwen_key=qwen_key,
            qwen_source=qwen_source,
            tavily_key=tavily_key,
            tavily_source=tavily_source,
        )
        scheduler_env = dict(api_env)
        for name in (
            "BRIDGES_QWEN_API_KEY",
            "BRIDGES_QWEN_API_KEY_FILE",
            "SCIENCE_COMPANION_QWEN_API_KEY",
            "SCIENCE_COMPANION_QWEN_API_KEY_FILE",
            "BRIDGES_TAVILY_API_KEY",
            "BRIDGES_TAVILY_API_KEY_FILE",
            "SCIENCE_COMPANION_TAVILY_API_KEY",
            "SCIENCE_COMPANION_TAVILY_API_KEY_FILE",
            "BRIDGES_AMAP_WEB_SERVICE_KEY",
            "BRIDGES_AMAP_JS_API_KEY",
            "BRIDGES_AMAP_SECURITY_JS_CODE",
            "BRIDGES_AMAP_WEB_SERVICE_KEY_FILE",
            "BRIDGES_AMAP_JS_API_KEY_FILE",
            "BRIDGES_AMAP_SECURITY_JS_CODE_FILE",
            "SCIENCE_COMPANION_AMAP_WEB_SERVICE_KEY",
            "SCIENCE_COMPANION_AMAP_JS_API_KEY",
            "SCIENCE_COMPANION_AMAP_SECURITY_JS_CODE",
            "SCIENCE_COMPANION_AMAP_WEB_SERVICE_KEY_FILE",
            "SCIENCE_COMPANION_AMAP_JS_API_KEY_FILE",
            "SCIENCE_COMPANION_AMAP_SECURITY_JS_CODE_FILE",
        ):
            scheduler_env.pop(name, None)
        worker_env = dict(api_env)
        web_env = _web_env(os.environ)
        web_env["API_BASE_URL"] = web_api_base_url

        # ``generated_secret`` 与 ``generated_database`` 只用于让代码阅读者能看出
        # 首次初始化和重复启动共享同一条路径；真正的幂等性由文件/配置存在性保证。
        del generated_secret, generated_database
        return PreparedRuntime(
            settings=settings,
            app_home=self.app_home,
            data_dir=data_dir,
            web_dir=web_dir,
            web_artifact=web_dir / ".next" / "standalone" / "server.js",
            api_env=api_env,
            worker_env=worker_env,
            scheduler_env=scheduler_env,
            web_env=web_env,
        )

    def _validate_web_source(self, web_dir: Path) -> None:
        if not web_dir.exists():
            raise BootstrapError(f"Web 应用目录不存在：{web_dir}")
        for filename in ("package.json", "package-lock.json"):
            if not (web_dir / filename).exists():
                raise BootstrapError(f"Web 构建缺少 {filename}：{web_dir / filename}")

    def _load_external_settings(self) -> Settings:
        try:
            return Settings()
        except (ValidationError, ValueError) as exc:
            raise BootstrapError(f"环境配置无法加载：{exc}") from exc

    def _load_config(self) -> dict[str, object]:
        path = self.app_home / "config.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BootstrapError(f"本机托管配置无法读取：{path}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
            raise BootstrapError(f"本机托管配置版本不受支持：{path}")
        return payload

    def _resolve_database(
        self,
        local_config: Mapping[str, object],
        external_settings: Settings,
    ) -> tuple[str, Path, bool]:
        external = _secret_value(external_settings.database_url)
        if external:
            try:
                path = Path(resolve_database_path(SecretStr(external)))
            except PersistenceError as exc:
                raise BootstrapError(f"BRIDGES_DATABASE_URL 无法解析：{exc}") from exc
            return external, path.parent.resolve(), False

        configured = local_config.get("database_url")
        if isinstance(configured, str) and configured:
            try:
                path = Path(resolve_database_path(SecretStr(configured)))
            except PersistenceError as exc:
                raise BootstrapError(f"本机数据库配置无法解析：{exc}") from exc
            return configured, path.parent.resolve(), False

        data_dir = self.app_home / "data"
        database_url = f"sqlite:///{(data_dir / 'bridges.db').as_posix()}"
        return database_url, data_dir, True

    def _resolve_secret(
        self,
        local_config: Mapping[str, object],
        data_dir: Path,
        generated_database: bool,
        external_settings: Settings,
    ) -> tuple[str | None, Path | None, bool]:
        file_ref = secret_file_reference("SECRET_KEY")
        if file_ref and file_ref[0]:
            file_path, env_name = file_ref
            return (
                _secret_value(external_settings.secret_key) or _read_secret_file(
                    Path(file_path), env_name
                ),
                Path(file_path).resolve(),
                False,
            )
        direct = _secret_value(external_settings.secret_key)
        if direct:
            return direct, None, False

        configured_file = local_config.get("secret_key_file")
        if isinstance(configured_file, str) and configured_file:
            path = Path(configured_file).resolve()
            return _read_secret_file(path, "BRIDGES_SECRET_KEY_FILE"), path, False

        if not generated_database:
            raise BootstrapError(
                "已配置 BRIDGES_DATABASE_URL，但未配置 BRIDGES_SECRET_KEY；"
                "为避免无法解密现有数据，系统不会自动替换数据库密钥。"
            )

        path = data_dir / "secret.key"
        value = _create_or_read_secret_file(path)
        return value, path, True

    def _resolve_qwen_key(
        self, external_settings: Settings, *, interactive: bool
    ) -> tuple[SecretStr | None, str]:
        file_ref = secret_file_reference("QWEN_API_KEY")
        if file_ref and file_ref[0]:
            file_path, env_name = file_ref
            value = _secret_value(external_settings.qwen_api_key)
            return SecretStr(value or _read_secret_file(Path(file_path), env_name)), "file"
        direct = _secret_value(external_settings.qwen_api_key)
        if direct:
            return SecretStr(direct), "environment"

        if external_settings.environment.lower() == "test":
            return None, "test"

        try:
            stored = self.credential_store.get(RUNTIME_QWEN_CREDENTIAL_ID)
        except (CredentialStoreError, OSError) as exc:
            raise BootstrapError("无法读取系统凭据库，请检查当前用户的凭据管理器。") from exc
        if stored is not None and stored.get_secret_value().strip():
            return stored, "credential-store"

        if not interactive:
            raise BootstrapError(
                "未配置全局百炼运行凭据。请设置 BRIDGES_QWEN_API_KEY 或"
                " BRIDGES_QWEN_API_KEY_FILE，然后重新执行 BridGes start。"
            )

        value = self.prompt("请输入百炼 API Key（输入内容不会显示）：").strip()
        if not value:
            raise BootstrapError("百炼 API Key 不能为空，启动已取消。")
        secret = SecretStr(value)
        try:
            self.credential_store.save(RUNTIME_QWEN_CREDENTIAL_ID, secret)
        except (CredentialStoreError, OSError) as exc:
            raise BootstrapError(
                "无法安全保存百炼 API Key，请检查系统凭据管理器；"
                "也可以改用 BRIDGES_QWEN_API_KEY_FILE。"
            ) from exc
        return secret, "credential-store"

    def _resolve_tavily_key(
        self, external_settings: Settings, *, interactive: bool
    ) -> tuple[SecretStr | None, str]:
        """与 Qwen Key 同构的 Tavily Key 解析（Issue 01）。

        解析链：文件引用 → 设置页凭据 → 环境变量 → 安装凭据库（独立 credential id）→
        交互 prompt「请输入 Tavily API Key」。已保存凭据的再次启动不重复
        prompt；凭据读取失败给出准确中文错误。与 Qwen 的差异：非交互调用
        缺 Key 时不阻塞启动——联网搜索入口返回「未配置搜索凭据」投影。
        """
        file_ref = secret_file_reference("TAVILY_API_KEY")
        if file_ref and file_ref[0]:
            file_path, env_name = file_ref
            value = _secret_value(external_settings.tavily_api_key)
            return (
                SecretStr(value or _read_secret_file(Path(file_path), env_name)),
                "file",
            )
        if external_settings.environment.lower() == "test":
            direct = _secret_value(external_settings.tavily_api_key)
            if direct:
                return SecretStr(direct), "environment"
            return None, "test"

        try:
            settings_key = self.credential_store.get(SETTINGS_TAVILY_CREDENTIAL_ID)
        except (CredentialStoreError, OSError) as exc:
            raise BootstrapError(
                "无法读取设置中保存的搜索凭据，请检查当前用户的凭据管理器。"
            ) from exc
        if settings_key is not None and settings_key.get_secret_value().strip():
            return settings_key, "settings-store"

        direct = _secret_value(external_settings.tavily_api_key)
        if direct:
            return SecretStr(direct), "environment"

        try:
            stored = self.credential_store.get(RUNTIME_TAVILY_CREDENTIAL_ID)
        except (CredentialStoreError, OSError) as exc:
            raise BootstrapError(
                "无法读取系统凭据库中的搜索凭据，请检查当前用户的凭据管理器。"
            ) from exc
        if stored is not None and stored.get_secret_value().strip():
            return stored, "credential-store"

        if not interactive:
            return None, "none"

        value = self.prompt("请输入 Tavily API Key（输入内容不会显示）：").strip()
        if not value:
            raise BootstrapError("Tavily API Key 不能为空，启动已取消。")
        secret = SecretStr(value)
        try:
            self.credential_store.save(RUNTIME_TAVILY_CREDENTIAL_ID, secret)
        except (CredentialStoreError, OSError) as exc:
            raise BootstrapError(
                "无法安全保存 Tavily API Key，请检查系统凭据管理器；"
                "也可以改用 BRIDGES_TAVILY_API_KEY_FILE。"
            ) from exc
        return secret, "credential-store"

    def _write_config(
        self,
        *,
        database_url: str | None,
        data_dir: Path,
        secret_key_file: Path | None,
    ) -> None:
        self.app_home.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "profile": "desktop",
            "data_dir": str(data_dir),
            "database_url": database_url,
            "secret_key_file": str(secret_key_file) if secret_key_file else None,
        }
        path = self.app_home / "config.json"
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            raise BootstrapError(f"无法保存本机托管配置：{path}") from exc

    def _ensure_web_build(self, web_dir: Path, *, api_base_url: str) -> None:
        npm = shutil.which("npm")
        if npm is None:
            raise BootstrapError("未找到 npm，请安装 Node.js 20 或更高版本。")

        marker_path = self.app_home / "runtime" / "web-build.json"
        try:
            marker_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BootstrapError(
                f"无法创建 Web 构建状态目录，请检查目录权限：{marker_path.parent}"
            ) from exc
        source_hash = _web_source_hash(web_dir)
        artifact = web_dir / ".next" / "standalone" / "server.js"
        marker = _read_json(marker_path)
        node_modules = web_dir / "node_modules"
        lock_hash = _file_hash(web_dir / "package-lock.json")
        build_env = _web_build_env(os.environ)
        build_env["API_BASE_URL"] = api_base_url
        needs_install = not node_modules.exists() or marker.get("package_lock_hash") != lock_hash
        if needs_install:
            self.emit("正在安装 Web 依赖（npm ci）……")
            self.command_runner(("npm", "ci"), web_dir, build_env)

        needs_build = (
            not artifact.exists()
            or marker.get("source_hash") != source_hash
            or marker.get("package_lock_hash") != lock_hash
            or marker.get("api_base_url") != api_base_url
        )
        if needs_build:
            self.emit("正在构建 Web（npm run build）……")
            self.command_runner(("npm", "run", "build"), web_dir, build_env)
            if not artifact.exists():
                raise BootstrapError(f"Web 构建完成但未生成：{artifact}")
            _write_json_atomic(
                marker_path,
                {
                    "package_lock_hash": lock_hash,
                    "source_hash": source_hash,
                    "api_base_url": api_base_url,
                },
            )

    def _runtime_env(
        self,
        *,
        database_url: str,
        secret_key: str | None,
        secret_key_file: Path | None,
        qwen_key: SecretStr | None,
        qwen_source: str,
        tavily_key: SecretStr | None,
        tavily_source: str,
    ) -> dict[str, str]:
        env = dict(os.environ)
        env["BRIDGES_ENVIRONMENT"] = _environment_name()
        env["BRIDGES_DATABASE_URL"] = database_url
        for name in (
            "BRIDGES_DATABASE_URL_FILE",
            "SCIENCE_COMPANION_DATABASE_URL",
            "SCIENCE_COMPANION_DATABASE_URL_FILE",
        ):
            env.pop(name, None)
        if secret_key_file is not None:
            env["BRIDGES_SECRET_KEY_FILE"] = str(secret_key_file)
            for name in (
                "BRIDGES_SECRET_KEY",
                "SCIENCE_COMPANION_SECRET_KEY",
                "SCIENCE_COMPANION_SECRET_KEY_FILE",
            ):
                env.pop(name, None)
        elif secret_key:
            env["BRIDGES_SECRET_KEY"] = secret_key
            for name in (
                "BRIDGES_SECRET_KEY_FILE",
                "SCIENCE_COMPANION_SECRET_KEY",
                "SCIENCE_COMPANION_SECRET_KEY_FILE",
            ):
                env.pop(name, None)
        if qwen_key is not None and qwen_source in {"environment", "credential-store"}:
            env["BRIDGES_QWEN_API_KEY"] = qwen_key.get_secret_value()
            for name in (
                "BRIDGES_QWEN_API_KEY_FILE",
                "SCIENCE_COMPANION_QWEN_API_KEY",
                "SCIENCE_COMPANION_QWEN_API_KEY_FILE",
            ):
                env.pop(name, None)
        elif qwen_key is not None and qwen_source == "file":
            file_ref = secret_file_reference("QWEN_API_KEY")
            if file_ref and file_ref[0]:
                env["BRIDGES_QWEN_API_KEY_FILE"] = file_ref[0]
                for name in (
                    "BRIDGES_QWEN_API_KEY",
                    "SCIENCE_COMPANION_QWEN_API_KEY",
                    "SCIENCE_COMPANION_QWEN_API_KEY_FILE",
                ):
                    env.pop(name, None)
        if tavily_key is not None and tavily_source in {
            "environment",
            "credential-store",
            "settings-store",
        }:
            env["BRIDGES_TAVILY_API_KEY"] = tavily_key.get_secret_value()
            for name in (
                "BRIDGES_TAVILY_API_KEY_FILE",
                "SCIENCE_COMPANION_TAVILY_API_KEY",
                "SCIENCE_COMPANION_TAVILY_API_KEY_FILE",
            ):
                env.pop(name, None)
        elif tavily_key is not None and tavily_source == "file":
            file_ref = secret_file_reference("TAVILY_API_KEY")
            if file_ref and file_ref[0]:
                env["BRIDGES_TAVILY_API_KEY_FILE"] = file_ref[0]
                for name in (
                    "BRIDGES_TAVILY_API_KEY",
                    "SCIENCE_COMPANION_TAVILY_API_KEY",
                    "SCIENCE_COMPANION_TAVILY_API_KEY_FILE",
                ):
                    env.pop(name, None)
        elif tavily_key is None:
            for name in (
                "BRIDGES_TAVILY_API_KEY",
                "BRIDGES_TAVILY_API_KEY_FILE",
                "SCIENCE_COMPANION_TAVILY_API_KEY",
                "SCIENCE_COMPANION_TAVILY_API_KEY_FILE",
            ):
                env.pop(name, None)
        return env


def default_app_home() -> Path:
    """返回当前用户的本机 BridGes 状态目录。"""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "BridGes"


def _environment_name() -> str:
    for prefix in ("BRIDGES_", "SCIENCE_COMPANION_"):
        env_name = f"{prefix}ENVIRONMENT"
        if env_name in os.environ:
            value = os.environ.get(env_name, "").strip()
            return value or "production"
    return "production"


def _web_api_base_url(settings: Settings) -> str:
    """为 Next.js rewrite 生成与 API 子进程一致的地址。"""
    configured = os.environ.get("API_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = settings.api_host.strip()
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{settings.api_port}"


def _secret_value(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    secret = value.get_secret_value().strip()
    return secret or None


def _read_secret_file(path: Path, env_name: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BootstrapError(f"无法读取 {env_name} 指向的凭据文件：{path}") from exc
    if not value:
        raise BootstrapError(f"{env_name} 指向的凭据文件为空：{path}")
    return value


def _create_or_read_secret_file(path: Path) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BootstrapError(f"无法创建本地状态目录：{path.parent}") from exc
    if path.exists():
        return _read_secret_file(path, "BRIDGES_SECRET_KEY_FILE")
    value = secrets.token_hex(32)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, value.encode("utf-8"))
        finally:
            os.close(descriptor)
        with suppress(OSError):
            os.chmod(path, 0o600)
    except FileExistsError:
        return _read_secret_file(path, "BRIDGES_SECRET_KEY_FILE")
    except OSError as exc:
        raise BootstrapError(f"无法创建本地状态加密密钥：{path}") from exc
    return value


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        digest.update(path.read_bytes())
    except OSError as exc:
        raise BootstrapError(f"无法读取 Web 构建输入：{path}") from exc
    return digest.hexdigest()


def _web_source_hash(web_dir: Path) -> str:
    digest = hashlib.sha256()
    ignored = {"node_modules", ".next", ".e2e-data", "__pycache__", ".mypy_cache"}
    try:
        paths = sorted(
            path
            for path in web_dir.rglob("*")
            if path.is_file()
            and not any(part in ignored for part in path.parts)
            and not path.name.endswith(".tsbuildinfo")
        )
        for path in paths:
            digest.update(path.relative_to(web_dir).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    except OSError as exc:
        raise BootstrapError(f"无法读取 Web 构建输入：{web_dir}") from exc
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError as exc:
        raise BootstrapError(f"无法保存 Web 构建状态：{path}") from exc


def _run_command(args: Sequence[str], cwd: Path, env: Mapping[str, str]) -> None:
    executable = shutil.which(args[0])
    if executable is None:
        raise BootstrapError(f"未找到可执行文件：{args[0]}")
    try:
        subprocess.run([executable, *args[1:]], cwd=cwd, env=dict(env), check=True)
    except subprocess.CalledProcessError as exc:
        raise BootstrapError(f"Web 命令执行失败：{args[0]}（退出码 {exc.returncode}）") from exc
    except OSError as exc:
        raise BootstrapError(f"无法执行 Web 命令：{args[0]}") from exc


def _web_build_env(source: Mapping[str, str]) -> dict[str, str]:
    env = dict(source)
    for name in _SECRET_ENV_FIELDS:
        env.pop(name, None)
    return env


def _web_env(source: Mapping[str, str]) -> dict[str, str]:
    """Web 子进程只继承非秘密配置，避免浏览器进程环境携带 Qwen Key。"""
    return _web_build_env(source)


def web_process_environment(source: Mapping[str, str]) -> dict[str, str]:
    """返回可安全传给 Web 构建或 Web 服务进程的环境变量。"""
    return _web_env(source)
