"""Issue 04：arXiv worker 预热的装配点测试（``create_app`` 挂载）。

用替身替换 ``bridges.api.main.ArxivSearchService``，只记录预热调用：
development/production 启动时预热常驻 worker；test 环境与 closeout
替身模式（无真实 worker）不预热，避免单测/集成装配产生子进程。
"""

from __future__ import annotations

import pytest

from bridges.api import main as api_main
from bridges.config import get_settings


class _WarmupSpyService:
    """替换装配点里的 ArxivSearchService：记录预热调用，其余不做事。"""

    instances: list[_WarmupSpyService] = []

    def __init__(self, *, client: object = None, observability: object = None) -> None:
        self.client = client
        self.observability = observability
        self.warmup_calls = 0
        _WarmupSpyService.instances.append(self)

    def warmup(self) -> bool:
        self.warmup_calls += 1
        return True

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _spy_arxiv_service(monkeypatch: pytest.MonkeyPatch) -> None:
    _WarmupSpyService.instances = []
    monkeypatch.setattr(api_main, "ArxivSearchService", _WarmupSpyService)


def test_development_startup_warms_up_resident_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "development")
    get_settings.cache_clear()

    api_main.create_app()

    assert len(_WarmupSpyService.instances) == 1
    assert _WarmupSpyService.instances[0].warmup_calls == 1


def test_test_environment_skips_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()

    api_main.create_app()

    assert _WarmupSpyService.instances[-1].warmup_calls == 0


def test_closeout_fixture_mode_skips_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    monkeypatch.setenv("BRIDGES_CLOSEOUT_FIXTURES", "true")
    get_settings.cache_clear()

    api_main.create_app()

    assert _WarmupSpyService.instances[-1].warmup_calls == 0
