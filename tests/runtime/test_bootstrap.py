"""本机托管启动 bootstrap 的行为合同测试。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from pydantic import SecretStr

from bridges.runtime.bootstrap import BootstrapError, LocalRuntimeBootstrap


class _MemoryCredentialStore:
    def __init__(self) -> None:
        self.value: SecretStr | None = None

    def get(self, account_id: str) -> SecretStr | None:
        del account_id
        return self.value

    def save(self, account_id: str, secret: SecretStr) -> None:
        del account_id
        self.value = secret


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
