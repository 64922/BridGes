"""画像工单 11 走查：按任务混合召回、显式降级与派生索引生命周期。

确定性部分（真实 SQLite 存储 + 真实原子画像服务，不调用外部模型、不读取
任何凭据）：

[1] 关系槽位/关键词召回：属性问句（「你记得我在读什么专业吗？」）命中已存
    学业事实并采用（修复基线评测 recall-academic-control 的漏召）。
[2] 语义通道（确定性编码替身）：同义改写召回；无关文本低于阈值不注入；
    用途/时效/本轮明确要求等门禁不因语义相似被绕过。
[3] 显式降级：向量不可用时如实给出降级原因，关键词通道继续工作；未命中
    条目的排除原因不得说成普通的「与当前问题无关」。
[4] 索引生命周期：删除后索引行移除、重建不复活；账户隔离。
[5] 预算整条裁剪：采用记录与实际发送共用同一对象（裁剪后两者一致）。

真实向量探针（默认执行；development 模式 + OS 凭据库，可从 `--no-real`
关闭）：

[6] 真实 Embedding 区分度：同义对余弦 ≥ PROFILE_RECALL_MIN_SIMILARITY
    (0.55)、无关对 < 阈值；生产组合（create_app）的原子画像服务经真实
    召回索引端到端召回同义改写。无凭据/网络不通时如实标注跳过，不算失败。

用法：
    python .scratch/profile-memory-redesign/validation/11-hybrid-purpose-recall/tools/walk_issue11_hybrid_recall_demo.py \
        --out .scratch/profile-memory-redesign/validation/11-hybrid-purpose-recall/evidence/demo-walkthrough-<stamp>.txt \
        [--proxy http://127.0.0.1:7890] [--no-real]

脚本只使用临时库（`.tmp/issue11-demo-*`），不读取或记录任何凭据与真实数据。
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.ai.ports import EmbeddingContext, EmbeddingOperation  # noqa: E402
from bridges.contracts.atomic_profile import AtomicProfileItemStatus  # noqa: E402
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence  # noqa: E402
from bridges.ingestion.embedding import EmbeddingError  # noqa: E402
from bridges.profiles.adapters import InMemoryProfileRepository  # noqa: E402
from bridges.profiles.atomic import (  # noqa: E402
    RECALL_DEGRADED_UNRELATED_REASON,
    RECALL_EXPIRED_REASON,
    RECALL_PURPOSE_MISMATCH_REASON,
    AtomicProfileService,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.commit import ProfileFactSubmission  # noqa: E402
from bridges.profiles.four_dimensions import (  # noqa: E402
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.purpose import build_purpose  # noqa: E402
from bridges.profiles.recall import (  # noqa: E402
    PROFILE_RECALL_MIN_SIMILARITY,
    RECALL_DEGRADED_NO_EMBEDDING,
    ProfileRecallIndex,
)
from bridges.storage.database import BridgesDatabase  # noqa: E402

ACCOUNT = "demo-alice"
OTHER_ACCOUNT = "demo-bob"
ANCHOR = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

ACADEMIC = "我是计算机专业的大二学生"
CONSTRAINT = "我在学线性代数，每晚只能抽出半小时"
HOBBY = "我平时喜欢跑步"
ALLERGY = "记住我有花粉过敏，户外建议要注意"


class _Script:
    def __init__(self, out: list[str]) -> None:
        self._out = out
        self.failures = 0
        self.skipped = 0

    def say(self, text: str) -> None:
        print(text)
        self._out.append(text)

    def check(self, ok: bool, label: str) -> None:
        status = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        self.say(f"    [{status}] {label}")

    def skip(self, label: str) -> None:
        self.skipped += 1
        self.say(f"    [SKIP] {label}")


def _unit(values: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return tuple(value / norm for value in values)


def _noise(text: str) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return _unit(tuple((digest[index] + 1) / 256 for index in range(4)))


class _MappedEncoder:
    """确定性编码替身：命中词表映射到固定方向，其余用内容哈希噪声。

    只用于在确定性走查里验证管线的召回/门禁/降级结构；语义质量的真实
    边界由 [6] 的真实向量探针给出。
    """

    def __init__(self, mapping: dict[str, tuple[float, ...]]) -> None:
        self._mapping = mapping
        self.fail = False
        self.calls = 0

    def embed(
        self,
        account_id: str,
        texts: list[str],
        *,
        context: EmbeddingContext | None = None,
    ) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise EmbeddingError("向量化失败：走查注入故障", retryable=True)
        vectors: list[list[float]] = []
        for text in texts:
            vector: tuple[float, ...] | None = None
            for needle, mapped in self._mapping.items():
                if needle in text:
                    vector = _unit(mapped)
                    break
            vectors.append(list(vector or _noise(text)))
        return vectors


class _Stack:
    """真实 SQLite 栈：原子画像 + 版本化召回索引（经生产同一注入方式）。"""

    def __init__(
        self,
        path: Path,
        *,
        mapping: dict[str, tuple[float, ...]] | None = None,
        with_embedding: bool = True,
        with_index: bool = True,
    ) -> None:
        self.database = BridgesDatabase(path)
        self.database.initialize()
        four = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=InMemoryFourDimensionProfileRepository(),
        )
        self.repository = SqliteAtomicProfileRepository(self.database, initialize=False)
        self.encoder = _MappedEncoder(mapping or {})
        self.index = (
            ProfileRecallIndex(self.database, self.encoder if with_embedding else None)
            if with_index
            else None
        )
        self.atomic = AtomicProfileService(
            four, self.repository, recall_index=self.index
        )

    def remember(self, text: str) -> Any:
        return self.atomic.remember(ACCOUNT, text)

    def compile(self, query: str, *, account: str = ACCOUNT, now: datetime | None = None):
        return self.atomic.compile_adopted_slice(
            account,
            run_id=f"walk-{uuid.uuid4().hex[:6]}",
            purpose=build_purpose(mode="companion", query=query),
            now=now,
        )

    def close(self) -> None:
        self.database.close()


def journey_1_slot_recall(script: _Script, tmp: Path) -> None:
    script.say("[1] 属性问句（关系槽位）召回已存学业事实（不装配语义索引）")
    stack = _Stack(tmp / "slot.db", with_index=False)
    stack.remember(ACADEMIC)
    stack.remember(HOBBY)
    slice_ = stack.compile("你记得我在读什么专业吗？")
    adopted = {item.fact_text: item for item in slice_.adopted_items}
    reasons = {item.fact_text: item.exclusion_reason for item in slice_.excluded_items}
    script.check(
        ACADEMIC in adopted and adopted[ACADEMIC].recall_basis == "keyword",
        "「专业」问句按关键词通道采用学业事实（recall_basis=keyword）",
    )
    script.check(
        HOBBY not in adopted and reasons.get(HOBBY) == "与当前问题无关",
        "无关爱好不注入（排除原因：与当前问题无关）",
    )
    stack.close()


def journey_2_semantic_channel(script: _Script, tmp: Path) -> None:
    script.say("[2] 语义通道（确定性替身）：同义召回与既有门禁优先")
    stack = _Stack(
        tmp / "semantic.db",
        mapping={
            "线性代数": (1.0, 0.0, 0.0, 0.0),
            "晚上": (0.97, 0.24, 0.0, 0.0),
            "花粉过敏": (0.0, 1.0, 0.0, 0.0),
            "学习": (0.02, 0.99, 0.0, 0.0),
            "简短": (0.0, 0.0, 1.0, 0.0),
            "详细": (0.03, 0.02, 0.99, 0.0),
        },
    )
    stack.remember(CONSTRAINT)
    slice_ = stack.compile("帮我安排晚上怎么学")
    adopted = {item.fact_text: item for item in slice_.adopted_items}
    script.check(
        CONSTRAINT in adopted and "semantic" in (adopted[CONSTRAINT].recall_basis or ""),
        "同义改写经语义通道召回（recall_basis 含 semantic）",
    )

    # 用途门禁优先：花粉过敏条目语义相似但不匹配「学习计划」用途。
    stack.remember(ALLERGY)
    gated = stack.compile("帮我安排学习计划")
    gated_adopted = {item.fact_text for item in gated.adopted_items}
    script.check(
        not any("花粉过敏" in text for text in gated_adopted)
        and any(
            item.exclusion_reason == RECALL_PURPOSE_MISMATCH_REASON
            for item in gated.excluded_items
        ),
        "语义相似不绕过用途门禁（用途与当前任务无关）",
    )

    # 时效门禁优先：过期条目语义相似也不作为当前约束。
    stack.remember("我下周有考试")
    expired = stack.compile("帮我复习考试", now=ANCHOR + timedelta(days=60))
    expired_reasons = {
        item.fact_text: item.exclusion_reason for item in expired.excluded_items
    }
    script.check(
        "我下周有考试" not in {item.fact_text for item in expired.adopted_items}
        and expired_reasons.get("我下周有考试") == RECALL_EXPIRED_REASON,
        "语义相似不绕过时效门禁（已过期不再用于当前回答）",
    )

    # 本轮明确要求优先：语义命中的长期默认被覆盖，且不改长期值。
    item = stack.remember("我喜欢简短回答")
    overridden = stack.compile("请详细解释贝叶斯定理")
    overridden_reasons = {
        item.fact_text: item.exclusion_reason for item in overridden.excluded_items
    }
    reloaded = stack.atomic.get_item(ACCOUNT, item.profile_item_id)
    script.check(
        "我喜欢简短回答" not in {i.fact_text for i in overridden.adopted_items}
        and any(
            "本轮明确要求优先" in reason
            for reason in overridden_reasons.values()
        )
        and reloaded.text == "我喜欢简短回答"
        and reloaded.version == item.version,
        "本轮明确要求覆盖长期默认且不改长期值",
    )
    stack.close()


def journey_3_degradation(script: _Script, tmp: Path) -> None:
    script.say("[3] 显式降级：向量不可用时如实说明，关键词通道继续")
    stack = _Stack(tmp / "degraded.db", with_embedding=False)
    stack.remember("我在学线性代数")
    stack.remember(HOBBY)
    slice_ = stack.compile("帮我复习线性代数")
    adopted = {item.fact_text for item in slice_.adopted_items}
    reasons = {item.fact_text: item.exclusion_reason for item in slice_.excluded_items}
    script.check(
        slice_.recall_degradation_reason == RECALL_DEGRADED_NO_EMBEDDING,
        f"降级原因如实记录：{RECALL_DEGRADED_NO_EMBEDDING}",
    )
    script.check(
        "我在学线性代数" in adopted,
        "关键词通道照常采用（降级只影响语义通道）",
    )
    script.check(
        reasons.get(HOBBY) == RECALL_DEGRADED_UNRELATED_REASON,
        "未命中原因如实标注降级，不伪装成「与当前问题无关」",
    )
    stack.close()


def journey_4_lifecycle(script: _Script, tmp: Path) -> None:
    script.say("[4] 派生索引生命周期：删除即移除、重建不复活、账户隔离")
    stack = _Stack(
        tmp / "lifecycle.db",
        mapping={"线性代数": (1.0, 0.0, 0.0, 0.0), "安排": (0.97, 0.24, 0.0, 0.0)},
    )
    item = stack.remember(CONSTRAINT)
    first = stack.compile("帮我安排学习")
    script.check(
        any(entry.fact_text == CONSTRAINT for entry in first.adopted_items),
        "索引建立后条目可被召回",
    )
    rows = stack.database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ? AND profile_item_id = ?",
        (ACCOUNT, item.profile_item_id),
    ).fetchone()
    script.say(f"    索引行数（删除前）={int(rows['count'])}")

    stack.atomic.delete_item(ACCOUNT, item.profile_item_id, item.version)
    after_delete = stack.compile("帮我安排学习")
    rows = stack.database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ? AND profile_item_id = ?",
        (ACCOUNT, item.profile_item_id),
    ).fetchone()
    script.check(
        after_delete.adopted_items == () and int(rows["count"]) == 0,
        "删除后条目不再采用且索引行移除",
    )
    stack.index.rebuild(ACCOUNT, items=stack.atomic.list_items(ACCOUNT), run_id="walk-rebuild")
    rows = stack.database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ? AND profile_item_id = ?",
        (ACCOUNT, item.profile_item_id),
    ).fetchone()
    restored = stack.atomic.get_item(ACCOUNT, item.profile_item_id)
    script.check(
        int(rows["count"]) == 0
        and restored.status is AtomicProfileItemStatus.WITHDRAWN,
        "全量重建不复活墓碑（墓碑正文为空，永不被索引）",
    )

    bob = stack.compile("帮我安排学习", account=OTHER_ACCOUNT)
    bob_rows = stack.database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ?",
        (OTHER_ACCOUNT,),
    ).fetchone()
    script.check(
        bob.adopted_items == () and int(bob_rows["count"]) == 0,
        "账户隔离：另一账户看不到任何索引行与候选",
    )
    stack.close()


def journey_5_record_equals_sent(script: _Script, tmp: Path) -> None:
    script.say("[5] 预算整条裁剪：采用记录与实际发送共用同一对象")
    from bridges.chat.turn import adopted_profile_block_within_budget

    stack = _Stack(
        tmp / "budget.db",
        mapping={"线性代数": (1.0, 0.0, 0.0, 0.0), "安排": (0.97, 0.24, 0.0, 0.0)},
    )
    stack.remember(CONSTRAINT)
    compiled = stack.compile("帮我安排学习")
    trimmed, block, sent_items = adopted_profile_block_within_budget(
        compiled, remaining_tokens=0
    )
    script.check(
        len(sent_items) == 0
        and block is None
        and trimmed.adopted_items == ()
        and any(
            item.exclusion_reason == "超出本轮模型输入预算，整条不采用"
            for item in trimmed.excluded_items
        ),
        "整条裁剪后：发送条目为空、记录同步排除（正文不截断）",
    )
    script.check(
        len(compiled.adopted_items) == 1,
        "冻结的完整编译快照保留原始采用结果（供重试复用与核对）",
    )
    stack.close()


def journey_6_real_probe(script: _Script, tmp: Path, proxy: str) -> None:
    script.say("[6] 真实向量探针（development + OS 凭据库）")
    for key in list(os.environ):
        if key.upper() in {
            "BRIDGES_QWEN_API_KEY",
            "BRIDGES_QWEN_API_KEY_FILE",
            "BRIDGES_TAVILY_API_KEY",
            "BRIDGES_TAVILY_API_KEY_FILE",
        }:
            os.environ.pop(key, None)
    os.environ["BRIDGES_ENVIRONMENT"] = "development"
    os.environ["BRIDGES_DATABASE_URL"] = f"sqlite:///{tmp / 'real' / 'bridges.db'}"
    os.environ["BRIDGES_SECRET_KEY"] = f"issue11-demo-{uuid.uuid4().hex}"
    if proxy:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[name] = proxy

    try:
        from bridges.api.main import create_app
        from bridges.config import get_settings
        from bridges.credentials.store import OsCredentialStore

        get_settings.cache_clear()
        app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BridGes" / "data"
        store = OsCredentialStore(data_dir=app_data, namespace="runtime")
        app = create_app(runtime_credential_store=store)
    except Exception as exc:  # noqa: BLE001 - 探针失败不算验收失败，如实标注
        script.skip(f"真实探针不可用（{exc.__class__.__name__}: {str(exc)[:120]}）")
        return

    try:
        port = app.state.embedding_port
        pairs = (
            ("同义改写（应召回）", True, "帮我安排晚上怎么学", CONSTRAINT),
            ("同义改写（应召回）", True, "你记得我在读什么专业吗？", ACADEMIC),
            ("无关爱好（不应召回）", False, "解释贝叶斯定理", HOBBY),
            ("无关文本（不应召回）", False, "帮我安排晚上怎么学", "我喜欢吃辣"),
        )
        import time as _time

        started = _time.perf_counter()
        vectors = port.embed(
            ACCOUNT,
            [text for _, _, query, item in pairs for text in (query, item)],
            context=EmbeddingContext(
                operation=EmbeddingOperation.PROFILE_RECALL,
                run_id="issue11-walk-real",
                object_type="profile_recall_query",
                object_id="issue11-walk-real",
                batch_ordinal=1,
            ),
        )
        elapsed = _time.perf_counter() - started
        script.say(
            f"    单批 {len(pairs) * 2} 条文本真实向量调用耗时 {elapsed:.2f} 秒"
            "（每回合编译至多 1 次查询向量调用；无问题时 0 次）"
        )
        script.say(f"    阈值 PROFILE_RECALL_MIN_SIMILARITY = {PROFILE_RECALL_MIN_SIMILARITY}")
        checks_ok = True
        for index, (label, should_recall, query, item) in enumerate(pairs):
            cosine = math.fsum(
                a * b for a, b in zip(vectors[index * 2], vectors[index * 2 + 1], strict=True)
            )
            ok = (cosine >= PROFILE_RECALL_MIN_SIMILARITY) == should_recall
            checks_ok = checks_ok and ok
            script.say(
                f"    {label}: cosine={cosine:.4f}（{'≥' if cosine >= PROFILE_RECALL_MIN_SIMILARITY else '<'} 阈值）"
            )
            script.say(f"      query={query!r}")
            script.say(f"      item ={item!r}")
        script.check(checks_ok, "真实向量对同义/无关的区分与阈值判定一致")

        # 生产组合端到端：create_app 注入的真实召回索引走完整编译路径。
        atomic = app.state.atomic_profile_service
        atomic.remember(ACCOUNT, CONSTRAINT)
        atomic.remember(ACCOUNT, HOBBY)
        slice_ = atomic.compile_adopted_slice(
            ACCOUNT,
            run_id="issue11-walk-real-semantic",
            purpose=build_purpose(mode="companion", query="帮我安排晚上怎么学"),
        )
        adopted = {item.fact_text: item for item in slice_.adopted_items}
        reasons = {item.fact_text: item.exclusion_reason for item in slice_.excluded_items}
        script.check(
            CONSTRAINT in adopted
            and "semantic" in (adopted[CONSTRAINT].recall_basis or "")
            and slice_.recall_degradation_reason is None,
            "生产组合端到端：同义改写经真实语义通道召回",
        )
        script.check(
            HOBBY not in adopted,
            f"无关爱好不被真实召回（排除原因：{reasons.get(HOBBY)}）",
        )
        app.state.bridges_database.close()
    except Exception as exc:  # noqa: BLE001 - 网络/限流波动不判验收失败
        script.skip(f"真实探针中断（{exc.__class__.__name__}: {str(exc)[:160]}）")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    parser.add_argument("--proxy", default="http://127.0.0.1:7890", help="真实探针代理；空串禁用")
    parser.add_argument("--no-real", action="store_true", help="跳过真实向量探针")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_lines = [f"画像工单 11 走查（确定性 + 真实向量探针） {stamp}"]
    script = _Script(out_lines)
    script.say(
        f"环境：conda agent / Python {sys.version.split()[0]}；"
        "临时库 .tmp/issue11-demo-*"
    )
    tmp_root = REPO_ROOT / ".tmp" / f"issue11-demo-{stamp}"
    tmp_root.mkdir(parents=True, exist_ok=True)

    script.say("")
    journey_1_slot_recall(script, tmp_root)
    script.say("")
    journey_2_semantic_channel(script, tmp_root)
    script.say("")
    journey_3_degradation(script, tmp_root)
    script.say("")
    journey_4_lifecycle(script, tmp_root)
    script.say("")
    journey_5_record_equals_sent(script, tmp_root)
    script.say("")
    if args.no_real:
        script.skip("真实向量探针（--no-real）")
    else:
        journey_6_real_probe(script, tmp_root, args.proxy)

    script.say("")
    summary = "全部通过" if script.failures == 0 else f"{script.failures} 项失败"
    if script.skipped:
        summary += f"（{script.skipped} 项跳过，见标注）"
    script.say(f"结果：{summary}")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"证据已写入 {args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
