"""本机托管启动 bootstrap 的行为合同测试。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from pydantic import SecretStr

from bridges.runtime.bootstrap import BootstrapError, LocalRuntimeBootstrap


class _MemoryCredentialStore:
    """按 credential id 隔离的替身，验证 Qwen 与 Tavily 凭据独立存取。"""

    def __init__(self) -> None:
        self.values: dict[str, SecretStr] = {}
        self.fail_ids: set[str] = set()

    def get(self, account_id: str) -> SecretStr | None:
        if account_id in self.fail_ids:
            raise OSError("simulated credential store failure")
        return self.values.get(account_id)

    def save(self, account_id: str, secret: SecretStr) -> None:
        if account_id in self.fail_ids:
            raise OSError("simulated credential store failure")
        self.values[account_id] = secret


class _WebCommandRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path, Mapping[str, str]]] = []

    def __call__(
        self,
        args: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
    ) -> None:
        call = tuple(args)
        self.calls.append((call, cwd, env))
        if call == ("npm", "ci"):
            (cwd / "node_modules").mkdir(parents=True, exist_ok=True)
        elif call == ("npm", "run", "build"):
            artifact = cwd / ".next" / "standalone" / "server.js"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("// 模拟 standalone 服务", encoding="utf-8")


def _web_fixture(root: Path) -> Path:
    web_dir = root / "apps" / "web"
    web_dir.mkdir(parents=True)
    (web_dir / "package.json").write_text('{"name":"web"}', encoding="utf-8")
    (web_dir / "package-lock.json").write_text(
        '{"name":"web","lockfileVersion":3}', encoding="utf-8"
    )
    (web_dir / "src").mkdir()
    (web_dir / "src" / "page.tsx").write_text("export {}", encoding="utf-8")
    return web_dir


def test_first_prepare_creates_persistent_runtime_and_builds_web(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BRIDGES_ENVIRONMENT", raising=False)
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY_FILE", raising=False)
    monkeypatch.delenv("BRIDGES_DATABASE_URL", raising=False)
    monkeypatch.delenv("BRIDGES_SECRET_KEY", raising=False)
    monkeypatch.delenv("BRIDGES_SECRET_KEY_FILE", raising=False)
    web_dir = _web_fixture(tmp_path / "repo")
    credentials = _MemoryCredentialStore()
    runner = _WebCommandRunner()

    prepared = LocalRuntimeBootstrap(
        repo_root=tmp_path / "repo",
        app_home=tmp_path / "app-home",
        command_runner=runner,
        credential_store=credentials,
        prompt=lambda _: "sk-bootstrap-test-key",
    ).prepare(interactive=True)

    config = json.loads((tmp_path / "app-home" / "config.json").read_text())
    assert config["schema_version"] == 1
    assert prepared.settings.environment == "production"
    assert prepared.settings.database_url is not None
    assert prepared.settings.secret_key is not None
    assert prepared.settings.qwen_api_key == SecretStr("sk-bootstrap-test-key")
    assert prepared.web_env["API_BASE_URL"] == "http://127.0.0.1:8000"
    assert (tmp_path / "app-home" / "data" / "bridges.db").parent.exists()
    assert (tmp_path / "app-home" / "data" / "secret.key").exists()
    assert prepared.web_artifact == web_dir / ".next" / "standalone" / "server.js"
    assert [call[0] for call in runner.calls] == [
        ("npm", "ci"),
        ("npm", "run", "build"),
    ]
    for _, _, env in runner.calls:
        assert "BRIDGES_QWEN_API_KEY" not in env
        assert "sk-bootstrap-test-key" not in " ".join(env.values())
        assert env["API_BASE_URL"] == "http://127.0.0.1:8000"
    assert "sk-bootstrap-test-key" not in (tmp_path / "app-home" / "config.json").read_text()


def test_web_build_uses_configured_api_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGES_API_PORT", "9100")
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)
    runner = _WebCommandRunner()

    LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=tmp_path / "app-home",
        command_runner=runner,
        credential_store=_MemoryCredentialStore(),
        prompt=lambda _: "sk-api-port-key",
    ).prepare(interactive=True)

    assert runner.calls[0][2]["API_BASE_URL"] == "http://127.0.0.1:9100"


def test_second_prepare_reuses_runtime_and_skips_prompt_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)
    app_home = tmp_path / "app-home"
    credentials = _MemoryCredentialStore()
    first_runner = _WebCommandRunner()
    LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=app_home,
        command_runner=first_runner,
        credential_store=credentials,
        prompt=lambda _: "sk-bootstrap-test-key",
    ).prepare(interactive=True)

    second_runner = _WebCommandRunner()
    prepared = LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=app_home,
        command_runner=second_runner,
        credential_store=credentials,
        prompt=lambda _: pytest.fail("second start must not prompt for the Key"),
    ).prepare(interactive=True)

    assert prepared.settings.qwen_api_key == SecretStr("sk-bootstrap-test-key")
    assert second_runner.calls == []


def test_missing_key_in_noninteractive_start_fails_without_secret_in_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)
    runner = _WebCommandRunner()

    with pytest.raises(BootstrapError) as exc_info:
        LocalRuntimeBootstrap(
            repo_root=repo_root,
            app_home=tmp_path / "app-home",
            command_runner=runner,
            credential_store=_MemoryCredentialStore(),
            prompt=lambda _: "sk-should-not-be-used",
        ).prepare(interactive=False)

    message = str(exc_info.value)
    assert "BRIDGES_QWEN_API_KEY" in message
    assert "sk-should-not-be-used" not in message


def test_empty_file_references_do_not_override_direct_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_DATABASE_URL_FILE",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "sk-direct-key")
    monkeypatch.setenv("BRIDGES_DATABASE_URL", "sqlite:///direct.db")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "direct-secret")
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY_FILE", "")
    monkeypatch.setenv("BRIDGES_DATABASE_URL_FILE", "")
    monkeypatch.setenv("BRIDGES_SECRET_KEY_FILE", "")
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)

    prepared = LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=tmp_path / "app-home",
        command_runner=_WebCommandRunner(),
        credential_store=_MemoryCredentialStore(),
    ).prepare(interactive=False)

    assert prepared.settings.qwen_api_key == SecretStr("sk-direct-key")
    assert prepared.settings.secret_key == SecretStr("direct-secret")
    assert prepared.api_env["BRIDGES_QWEN_API_KEY"] == "sk-direct-key"
    assert "BRIDGES_QWEN_API_KEY_FILE" not in prepared.api_env


def test_file_references_are_resolved_without_persisting_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_DATABASE_URL_FILE",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)
    database_file = tmp_path / "database-url.txt"
    database_file.write_text(
        f"sqlite:///{(tmp_path / 'external' / 'bridges.db').as_posix()}",
        encoding="utf-8",
    )
    secret_file = tmp_path / "secret.key"
    secret_file.write_text("external-secret-key", encoding="utf-8")
    qwen_file = tmp_path / "qwen.key"
    qwen_file.write_text("sk-file-key", encoding="utf-8")
    monkeypatch.setenv("BRIDGES_DATABASE_URL_FILE", str(database_file))
    monkeypatch.setenv("BRIDGES_SECRET_KEY_FILE", str(secret_file))
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY_FILE", str(qwen_file))
    monkeypatch.setenv("BRIDGES_DATABASE_URL", "sqlite:///wrong.db")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "wrong-secret")
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "sk-wrong-key")

    runner = _WebCommandRunner()
    prepared = LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=tmp_path / "app-home",
        command_runner=runner,
        credential_store=_MemoryCredentialStore(),
    ).prepare(interactive=False)

    assert prepared.settings.database_url == SecretStr(
        f"sqlite:///{(tmp_path / 'external' / 'bridges.db').as_posix()}"
    )
    assert prepared.settings.secret_key == SecretStr("external-secret-key")
    assert prepared.settings.qwen_api_key == SecretStr("sk-file-key")
    config_text = (tmp_path / "app-home" / "config.json").read_text(encoding="utf-8")
    assert "sk-file-key" not in config_text
    assert "external-secret-key" not in config_text
    assert "database-url.txt" not in config_text


# ---------------------------------------------------------------------------
# Issue 01：Tavily Key 安装流程（文件/环境变量/凭据库/prompt，独立凭据库 id）
# ---------------------------------------------------------------------------


def test_interactive_prepare_prompts_qwen_then_tavily_and_saves_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)
    credentials = _MemoryCredentialStore()
    prompts: list[str] = []

    def prompt(question: str) -> str:
        prompts.append(question)
        return "sk-qwen-key" if len(prompts) == 1 else "tvly-install-key"

    prepared = LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=tmp_path / "app-home",
        command_runner=_WebCommandRunner(),
        credential_store=credentials,
        prompt=prompt,
    ).prepare(interactive=True)

    assert prompts == [
        "请输入百炼 API Key（输入内容不会显示）：",
        "请输入 Tavily API Key（输入内容不会显示）：",
    ]
    assert prepared.settings.qwen_api_key == SecretStr("sk-qwen-key")
    assert prepared.settings.tavily_api_key == SecretStr("tvly-install-key")
    # 两者以独立 credential id 进入系统凭据库。
    assert credentials.values["global-qwen-api-key"] == SecretStr("sk-qwen-key")
    assert credentials.values["global-tavily-api-key"] == SecretStr("tvly-install-key")
    assert prepared.api_env["BRIDGES_QWEN_API_KEY"] == "sk-qwen-key"
    assert prepared.api_env["BRIDGES_TAVILY_API_KEY"] == "tvly-install-key"
    config_text = (tmp_path / "app-home" / "config.json").read_text(encoding="utf-8")
    assert "tvly-install-key" not in config_text
    assert "sk-qwen-key" not in config_text


def test_second_prepare_reuses_stored_tavily_key_without_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)
    app_home = tmp_path / "app-home"
    credentials = _MemoryCredentialStore()
    LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=app_home,
        command_runner=_WebCommandRunner(),
        credential_store=credentials,
        prompt=lambda _: "tvly-first-key",
    ).prepare(interactive=True)

    prepared = LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=app_home,
        command_runner=_WebCommandRunner(),
        credential_store=credentials,
        prompt=lambda _: pytest.fail("再次启动不得重复 prompt 任何 Key"),
    ).prepare(interactive=True)

    assert prepared.settings.qwen_api_key == SecretStr("tvly-first-key")
    assert prepared.settings.tavily_api_key == SecretStr("tvly-first-key")
    assert prepared.api_env["BRIDGES_TAVILY_API_KEY"] == "tvly-first-key"


def test_tavily_direct_environment_injects_key_and_cleans_old_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_DATABASE_URL_FILE",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
        "SCIENCE_COMPANION_TAVILY_API_KEY",
        "SCIENCE_COMPANION_TAVILY_API_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "sk-qwen")
    monkeypatch.setenv("BRIDGES_TAVILY_API_KEY", "tvly-direct-key")
    # 同名旧环境变量应被清理，避免双源歧义（指向不存在的路径会让
    # Settings 加载失败，因此旧 FILE 变量只保留未设置状态）。
    monkeypatch.setenv("SCIENCE_COMPANION_TAVILY_API_KEY", "legacy-tavily")
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)

    prepared = LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=tmp_path / "app-home",
        command_runner=_WebCommandRunner(),
        credential_store=_MemoryCredentialStore(),
    ).prepare(interactive=False)

    assert prepared.settings.tavily_api_key == SecretStr("tvly-direct-key")
    assert prepared.api_env["BRIDGES_TAVILY_API_KEY"] == "tvly-direct-key"
    assert "BRIDGES_TAVILY_API_KEY_FILE" not in prepared.api_env
    assert "SCIENCE_COMPANION_TAVILY_API_KEY" not in prepared.api_env
    assert "SCIENCE_COMPANION_TAVILY_API_KEY_FILE" not in prepared.api_env


def test_tavily_file_reference_wins_and_cleanup_direct_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_DATABASE_URL_FILE",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    tavily_file = tmp_path / "tavily.key"
    tavily_file.write_text("tvly-file-key", encoding="utf-8")
    monkeypatch.setenv("BRIDGES_TAVILY_API_KEY_FILE", str(tavily_file))
    monkeypatch.setenv("BRIDGES_TAVILY_API_KEY", "tvly-wrong-direct")
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "sk-qwen")
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)

    prepared = LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=tmp_path / "app-home",
        command_runner=_WebCommandRunner(),
        credential_store=_MemoryCredentialStore(),
    ).prepare(interactive=False)

    assert prepared.settings.tavily_api_key == SecretStr("tvly-file-key")
    assert prepared.api_env["BRIDGES_TAVILY_API_KEY_FILE"] == str(tavily_file)
    assert "BRIDGES_TAVILY_API_KEY" not in prepared.api_env


def test_noninteractive_prepare_without_tavily_key_starts_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_DATABASE_URL_FILE",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", "sk-qwen")
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)

    prepared = LocalRuntimeBootstrap(
        repo_root=repo_root,
        app_home=tmp_path / "app-home",
        command_runner=_WebCommandRunner(),
        credential_store=_MemoryCredentialStore(),
    ).prepare(interactive=False)

    # Issue 01：缺 Tavily Key 不阻塞启动；搜索入口返回「未配置搜索凭据」。
    assert prepared.settings.tavily_api_key is None
    for name in (
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "SCIENCE_COMPANION_TAVILY_API_KEY",
        "SCIENCE_COMPANION_TAVILY_API_KEY_FILE",
    ):
        assert name not in prepared.api_env


def test_empty_tavily_prompt_cancels_start_with_chinese_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)
    calls = 0

    def prompt(question: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "sk-qwen-key"
        return ""

    with pytest.raises(BootstrapError, match="Tavily API Key 不能为空"):
        LocalRuntimeBootstrap(
            repo_root=repo_root,
            app_home=tmp_path / "app-home",
            command_runner=_WebCommandRunner(),
            credential_store=_MemoryCredentialStore(),
            prompt=prompt,
        ).prepare(interactive=True)


def test_tavily_credential_store_failure_gives_accurate_chinese_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "BRIDGES_ENVIRONMENT",
        "BRIDGES_QWEN_API_KEY",
        "BRIDGES_QWEN_API_KEY_FILE",
        "BRIDGES_TAVILY_API_KEY",
        "BRIDGES_TAVILY_API_KEY_FILE",
        "BRIDGES_DATABASE_URL",
        "BRIDGES_SECRET_KEY",
        "BRIDGES_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    repo_root = tmp_path / "repo"
    _web_fixture(repo_root)
    credentials = _MemoryCredentialStore()
    credentials.fail_ids.add("global-tavily-api-key")

    with pytest.raises(BootstrapError, match="凭据管理器"):
        LocalRuntimeBootstrap(
            repo_root=repo_root,
            app_home=tmp_path / "app-home",
            command_runner=_WebCommandRunner(),
            credential_store=credentials,
            prompt=lambda _: "tvly-key",
        ).prepare(interactive=True)
