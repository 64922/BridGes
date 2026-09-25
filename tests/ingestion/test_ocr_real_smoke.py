"""Issue 14：可选真实 OCR smoke（显式 opt-in）。

仅当 ``BRIDGES_OCR_REAL_SMOKE=1`` 且环境已配置安装级全局 Qwen Key 时
执行：禁用 Stub、fixture、cassette 与录制（只使用 Issue 09 生产组合的
真实适配器），调用真实 OCR 两张本地渲染的图：一张含已知短中文的最小
测试图（验证非空实际输出、固定模型、恰好 1 条页级锁以及重启后可查），
一张代表材料页面（课堂笔记类普通文档，Issue 07 验证去科学预设后的通用
提示词仍读到材料正文）。

测试代码只判断凭据是否配置（``is_global_qwen_key_configured``），绝不
读取、打印、哈希或断言 Key、图片 base64 或完整 OCR 文本。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bridges.ai.fixed_models import OCR_MODEL_ID
from bridges.ai.production import build_production_composition
from bridges.ai.sqlite_recorder import SqliteModelRunLockRecorder
from bridges.config import Settings
from bridges.credentials.global_credential import is_global_qwen_key_configured
from bridges.ingestion.ocr import OcrPageRequest, QwenOcrPort
from bridges.storage import BridgesDatabase
from bridges.storage.database import SCHEMA_VERSION

REAL_SMOKE_ENV = "BRIDGES_OCR_REAL_SMOKE"

#: 最小测试图中的已知短中文（不参与断言正文内容，只用于渲染）。
KNOWN_CN_TOKEN = "桥接OCR测试"


def _render_known_cn_png() -> bytes:
    """生成含已知短中文的 640x200 PNG（PyMuPDF 内置中文字体，无网络依赖）。"""
    import fitz  # type: ignore[import-untyped]  # PyMuPDF

    document = fitz.open()
    page = document.new_page(width=640, height=200)
    page.insert_text(
        (40, 120),
        KNOWN_CN_TOKEN,
        fontsize=36,
        fontname="china-s",
    )
    pixmap = page.get_pixmap(dpi=144)
    return bytes(pixmap.tobytes("png"))


#: 代表材料（课堂笔记，非科学图表）页面上的已知行文。
REPRESENTATIVE_MATERIAL_LINES = (
    "课堂笔记：第三章 高斯定理",
    "1. 电场通量与电荷的关系",
    "2. 电通量等于场强与面积的乘积",
    "复习要点：例题 3.2 与课后习题",
)
#: 用于判断识别是否真的读到了代表材料内容的词（不逐字断言，避免把单次
#: 调用当作识别质量承诺；逐字准确率由学习模式书页识别的质量验证覆盖）。
REPRESENTATIVE_MATERIAL_TOKENS = ("高斯定理", "电通量", "电场", "笔记")


def _render_representative_material_png() -> bytes:
    """生成代表材料页面的 960x540 PNG（多行普通文档文字，非科学图表）。"""
    import fitz  # type: ignore[import-untyped]  # PyMuPDF

    document = fitz.open()
    page = document.new_page(width=960, height=540)
    for index, line in enumerate(REPRESENTATIVE_MATERIAL_LINES):
        page.insert_text(
            (60, 100 + index * 70),
            line,
            fontsize=24,
            fontname="china-s",
        )
    pixmap = page.get_pixmap(dpi=120)
    return bytes(pixmap.tobytes("png"))


def _smoke_ready() -> tuple[Settings, str] | None:
    """凭据是否配置：只判断「已配置/未配置」，不读取 Key 内容。"""
    if os.environ.get(REAL_SMOKE_ENV) != "1":
        return None
    settings = Settings()
    if not is_global_qwen_key_configured(settings):
        return None
    if settings.qwen_cassette_dir is not None:
        # 显式禁用 cassette/录制：真实 smoke 不得回放 fixture。
        return None
    return settings, ""


@pytest.mark.skipif(
    os.environ.get(REAL_SMOKE_ENV) != "1",
    reason="真实 OCR smoke 需显式设置 BRIDGES_OCR_REAL_SMOKE=1",
)
def test_real_ocr_smoke_records_one_page_lock(tmp_path: Path) -> None:
    ready = _smoke_ready()
    if ready is None:
        pytest.skip("未配置安装级全局 Qwen Key（或配置了 cassette），真实 smoke 不可用。")
    settings, _ = ready

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    composition = build_production_composition(settings)
    port = QwenOcrPort(
        gateway=composition.gateway,
        recorder=SqliteModelRunLockRecorder(database),
    )
    run_id = f"ingestion-ocr:smoke-account:doc-smoke:claim-{os.getpid()}"
    text = port.extract(
        OcrPageRequest(
            account_id="smoke-account",
            object_id="smoke-object",
            document_id="doc-smoke-object",
            run_id=run_id,
            page_ordinal=1,
            call_ordinal=1,
            media_type="image/png",
            content_hash="smoke-hash",
            content=_render_known_cn_png(),
        )
    )

    # 真实响应：非空实际输出（不打印/不断言 OCR 文本正文）。
    assert text.strip()

    # 恰好 1 条页级锁：固定模型、成功状态、业务关联与 run 正确。
    locks = database.connection.execute(
        "SELECT * FROM model_run_locks", ()
    ).fetchall()
    assert len(locks) == 1
    lock = locks[0]
    assert str(lock["actual_model_id"]) == OCR_MODEL_ID
    assert str(lock["status"]) == "success"
    assert str(lock["capability_name"]) == "qwen_ocr"
    assert str(lock["run_id"]) == run_id
    assert str(lock["account_id"]) == "smoke-account"
    links = database.connection.execute(
        "SELECT * FROM model_run_lock_links", ()
    ).fetchall()
    assert len(links) == 1
    assert str(links[0]["operation"]) == "ocr_page:1"
    # 锁只含脱敏参数：绝不含 base64、prompt 或 OCR 文本。
    assert "image_base64" not in str(lock["parameters"])
    assert "prompt" not in str(lock["parameters"])
    assert KNOWN_CN_TOKEN not in str(lock["parameters"])

    # 重启后可查：关闭数据库重新打开，页级锁仍按 run 可查。
    database.close()
    reopened = BridgesDatabase(tmp_path / "bridges.db")
    # 用注册表常量判等：硬编码版本号会随迁移漂移（曾写死 47）。
    assert reopened.initialize() == SCHEMA_VERSION
    recorder = SqliteModelRunLockRecorder(reopened)
    persisted = recorder.list_locks_by_run("smoke-account", run_id)
    assert len(persisted) == 1
    assert persisted[0].business_refs[0].object_id == "doc-smoke-object"
    reopened.close()


@pytest.mark.skipif(
    os.environ.get(REAL_SMOKE_ENV) != "1",
    reason="真实 OCR smoke 需显式设置 BRIDGES_OCR_REAL_SMOKE=1",
)
def test_real_ocr_smoke_reads_representative_material(tmp_path: Path) -> None:
    """Issue 07：通用 OCR 在代表材料（课堂笔记）上给出可用的真实文本。

    与科学图表冒烟区分：本用例的输入是普通文档页面，验证去科学预设后的
    提示词仍能读到材料正文，并且仍然恰好产生一条页级锁。
    """
    ready = _smoke_ready()
    if ready is None:
        pytest.skip("未配置安装级全局 Qwen Key（或配置了 cassette），真实 smoke 不可用。")
    settings, _ = ready

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    composition = build_production_composition(settings)
    port = QwenOcrPort(
        gateway=composition.gateway,
        recorder=SqliteModelRunLockRecorder(database),
    )
    run_id = f"ingestion-ocr:smoke-account:doc-material:claim-{os.getpid()}"
    text = port.extract(
        OcrPageRequest(
            account_id="smoke-account",
            object_id="smoke-material",
            document_id="doc-smoke-material",
            run_id=run_id,
            page_ordinal=1,
            call_ordinal=1,
            media_type="image/png",
            content_hash="smoke-material-hash",
            content=_render_representative_material_png(),
        )
    )

    # 真实响应：非空，且确实读到了材料正文（词级判定，不逐字断言正文）。
    assert text.strip()
    assert any(
        token in text for token in REPRESENTATIVE_MATERIAL_TOKENS
    ), "通用 OCR 未读到代表材料正文"

    locks = database.connection.execute(
        "SELECT * FROM model_run_locks", ()
    ).fetchall()
    assert len(locks) == 1
    assert str(locks[0]["actual_model_id"]) == OCR_MODEL_ID
    assert str(locks[0]["status"]) == "success"
    assert str(locks[0]["run_id"]) == run_id
    # 锁与日志仍只含脱敏参数：绝不含代表材料正文。
    assert not any(
        line in str(locks[0]["parameters"]) for line in REPRESENTATIVE_MATERIAL_LINES
    )
    database.close()
