"""画像工单 11：按任务混合召回、语义门禁与派生索引生命周期。

覆盖验收：
- 同义/换一种说法的提问经语义通道召回事实，关系属性词（关键词通道）
  也能命中已存学业事实；
- 语义相似不越过用途、时效、冲突与「本轮明确要求优先」等既有门禁；
- 预算不足优先舍弃低相关条目，不丢关键约束；
- 索引或模型不可用时给出显式降级，不伪装成「没有相关记忆」；
- 删除后索引行移除、重建不复活；账户隔离；向量合同变化不混用旧版本。
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridges.ai.ports import EmbeddingContext
from bridges.contracts.atomic_profile import (
    AtomicProfileConflictState,
    AtomicProfileItemStatus,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.ingestion.embedding import EmbeddingError
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import (
    RECALL_CONFLICT_REASON,
    RECALL_DEGRADED_UNRELATED_REASON,
    RECALL_EXPIRED_REASON,
    RECALL_PURPOSE_MISMATCH_REASON,
    AtomicProfileService,
    InMemoryAtomicProfileRepository,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.purpose import build_purpose
from bridges.profiles.recall import (
    RECALL_DEGRADED_PREFIX,
    ProfileRecallContract,
    ProfileRecallIndex,
)
from bridges.storage.database import BridgesDatabase

ALICE = "account-alice"
BOB = "account-bob"
ANCHOR = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

CONSTRAINT = "我在学线性代数，每晚只能抽出半小时"
HOBBY = "我平时喜欢跑步"


def _unit(values: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return tuple(value / norm for value in values)


def _noise_vector(text: str) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return _unit(tuple((digest[index] + 1) / 256 for index in range(4)))


class _MappedEmbeddingPort:
    """测试替身：命中词表关键词的文本映射到固定方向，其余用确定性噪声。

    只验证管线如何消费向量（阈值、门禁、版本、降级），不冒充真实模型语义；
    真实 Embedding 的效果由走查与工单 18 的真实模型评测覆盖。
    """

    def __init__(self, mapping: dict[str, tuple[float, ...]]) -> None:
        self._mapping = mapping
        self.calls: list[list[str]] = []
        self.fail = False

    def embed(
        self,
        account_id: str,
        texts: list[str],
        *,
        context: EmbeddingContext | None = None,
    ) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail:
            raise EmbeddingError("向量化失败：测试注入故障", retryable=True)
        vectors: list[list[float]] = []
        for text in texts:
            vector: tuple[float, ...] | None = None
            for needle, mapped in self._mapping.items():
                if needle in text:
                    vector = _unit(mapped)
                    break
            vectors.append(list(vector or _noise_vector(text)))
        return vectors


def _semantic_env(
    tmp_path: Path,
    mapping: dict[str, tuple[float, ...]],
    *,
    contract: ProfileRecallContract | None = None,
) -> tuple[BridgesDatabase, AtomicProfileService, ProfileRecallIndex, _MappedEmbeddingPort]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = SqliteAtomicProfileRepository(database, initialize=False)
    port = _MappedEmbeddingPort(mapping)
    index = ProfileRecallIndex(
        database, port, **({"contract": contract} if contract else {})
    )
    service = AtomicProfileService(four, repository, recall_index=index)
    return database, service, index, port


def test_relation_slot_keyword_recalls_academic_fact() -> None:
    """「你记得我在读什么专业吗」按属性词命中关系槽，下一轮直接采用。"""

    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    service = AtomicProfileService(four, InMemoryAtomicProfileRepository())
    service.remember(ALICE, "我是计算机专业的大二学生")

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="你记得我在读什么专业吗？"),
        run_id="run-next",
    )

    by_text = {item.fact_text: item for item in slice_.adopted_items}
    assert "我是计算机专业的大二学生" in by_text
    assert by_text["我是计算机专业的大二学生"].recall_basis == "keyword"
    assert slice_.recall_degradation_reason is None


def test_semantic_channel_recalls_paraphrase_and_keeps_unrelated_out(
    tmp_path: Path,
) -> None:
    """换一种说法经语义通道召回；无关爱好不因向量噪声被注入。"""

    database, service, _, _ = _semantic_env(
        tmp_path,
        {
            "线性代数": (1.0, 0.0, 0.0, 0.0),
            "晚上": (0.97, 0.24, 0.0, 0.0),
            "跑步": (0.0, 1.0, 0.0, 0.0),
        },
    )
    service.remember(ALICE, CONSTRAINT)
    service.remember(ALICE, HOBBY)

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="帮我安排晚上怎么学"),
        run_id="run-semantic",
    )

    by_text = {item.fact_text: item for item in slice_.adopted_items}
    assert CONSTRAINT in by_text
    assert "semantic" in (by_text[CONSTRAINT].recall_basis or "")
    assert HOBBY not in by_text
    reasons = {item.fact_text: item.exclusion_reason for item in slice_.excluded_items}
    assert reasons[HOBBY] == "与当前问题无关"
    assert slice_.recall_degradation_reason is None
    assert slice_.recall_index_version is not None
    database.close()


def test_semantic_similarity_does_not_bypass_purpose_gate(tmp_path: Path) -> None:
    """敏感自述即使语义相似，用途不匹配仍不采用。"""

    database, service, _, _ = _semantic_env(
        tmp_path,
        {
            "花粉过敏": (1.0, 0.0, 0.0, 0.0),
            "学习": (0.97, 0.24, 0.0, 0.0),
        },
    )
    service.remember(ALICE, "记住我有花粉过敏，户外建议要注意")

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="帮我安排学习计划"),
        run_id="run-purpose-gate",
    )

    assert slice_.adopted_items == ()
    reasons = {item.exclusion_reason for item in slice_.excluded_items}
    assert RECALL_PURPOSE_MISMATCH_REASON in reasons
    database.close()


def test_semantic_similarity_does_not_bypass_expired_gate(tmp_path: Path) -> None:
    """过期条目语义相似也不再作为当前约束使用。"""

    database, service, _, _ = _semantic_env(
        tmp_path,
        {
            "考试": (1.0, 0.0, 0.0, 0.0),
            "复习": (0.97, 0.24, 0.0, 0.0),
        },
    )
    service.remember(ALICE, "我下周有考试", source_at=ANCHOR - timedelta(days=30))

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="帮我复习考试"),
        run_id="run-expired",
        now=ANCHOR,
    )

    assert slice_.adopted_items == ()
    reasons = {item.exclusion_reason for item in slice_.excluded_items}
    assert RECALL_EXPIRED_REASON in reasons
    database.close()


def test_semantic_similarity_does_not_bypass_conflict_gate(tmp_path: Path) -> None:
    """未解决冲突的条目语义相似也不作确定事实。"""

    database, service, _, _ = _semantic_env(
        tmp_path,
        {
            "大几": (0.97, 0.24, 0.0, 0.0),
            "大二": (0.98, 0.2, 0.0, 0.0),
        },
    )
    service.commit_fact(
        ALICE,
        ProfileFactSubmission(
            text="我是大四学生",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_message_id="message-1",
        ),
    )
    service.correct_fact(
        ALICE, dimension=FourDimension.ACADEMIC_STATUS, text="我是大三学生"
    )
    conflicted = service.commit_fact(
        ALICE,
        ProfileFactSubmission(
            text="我是大二学生",
            dimension=FourDimension.ACADEMIC_STATUS,
            confidence=FourDimensionConfidence.HIGH,
            evidence_message_id="message-2",
        ),
    )
    assert conflicted is not None
    assert conflicted.conflict_state is AtomicProfileConflictState.UNRESOLVED

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="我应该读大几的课程"),
        run_id="run-conflict",
    )

    # 冲突条目即使语义相似也不采用；更正后的确定值可以照常采用。
    assert "我是大二学生" not in {
        item.fact_text for item in slice_.adopted_items
    }
    reasons = {item.fact_text: item.exclusion_reason for item in slice_.excluded_items}
    assert reasons["我是大二学生"] == RECALL_CONFLICT_REASON
    database.close()


def test_explicit_request_still_overrides_semantic_default(tmp_path: Path) -> None:
    """本轮明确要求覆盖语义命中的长期默认，且不改长期记忆。"""

    database, service, _, _ = _semantic_env(
        tmp_path,
        {
            "简短": (1.0, 0.0, 0.0, 0.0),
            "详细": (0.98, 0.2, 0.0, 0.0),
        },
    )
    item = service.remember(ALICE, "我喜欢简短回答")

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="请详细解释贝叶斯定理"),
        run_id="run-override",
    )

    assert slice_.adopted_items == ()
    reasons = {item.exclusion_reason for item in slice_.excluded_items}
    assert any("本轮明确要求优先" in reason for reason in reasons)
    reloaded = service.get_item(ALICE, item.profile_item_id)
    assert reloaded.text == "我喜欢简短回答"
    assert reloaded.version == item.version
    database.close()


def test_budget_prefers_rule_constraint_over_semantic_candidates(
    tmp_path: Path,
) -> None:
    """条目数预算不足时先舍弃低相关（语义）候选，不丢关键约束。"""

    database, service, _, _ = _semantic_env(
        tmp_path,
        {
            "约束词": (1.0, 0.0, 0.0, 0.0),
            "主题": (0.98, 0.2, 0.0, 0.0),
        },
    )
    service.remember(ALICE, "我每天只有30分钟学习时间")
    for index in range(5):
        service.remember(ALICE, f"主题事实{index}：我在练习项目{index}")

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="帮我制定主题学习计划"),
        run_id="run-budget",
    )

    adopted_texts = [item.fact_text for item in slice_.adopted_items]
    assert "我每天只有30分钟学习时间" in adopted_texts
    assert len(adopted_texts) <= 4
    reasons = [item.exclusion_reason for item in slice_.excluded_items]
    assert "超出本轮最小切片预算" in reasons
    database.close()


def test_index_failure_degrades_explicitly_and_keeps_keyword_channel(
    tmp_path: Path,
) -> None:
    """索引/模型故障给出显式降级；关键词通道照常工作。"""

    database, service, _, port = _semantic_env(tmp_path, {})
    service.remember(ALICE, "我在学线性代数")
    port.fail = True

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="帮我复习线性代数"),
        run_id="run-degraded",
    )

    assert slice_.recall_degradation_reason is not None
    assert slice_.recall_degradation_reason.startswith(RECALL_DEGRADED_PREFIX)
    # 关键词通道仍然工作：问题与条目共享「线性代数」词面依据。
    assert any(item.fact_text == "我在学线性代数" for item in slice_.adopted_items)
    database.close()


def test_degraded_unrelated_reason_is_not_no_relevant_memory(tmp_path: Path) -> None:
    """降级时未命中条目的排除原因不得说成普通的「与当前问题无关」。"""

    database, service, _, port = _semantic_env(tmp_path, {})
    service.remember(ALICE, HOBBY)
    port.fail = True

    slice_ = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="解释贝叶斯定理"),
        run_id="run-degraded-unrelated",
    )

    reasons = {item.exclusion_reason for item in slice_.excluded_items}
    assert RECALL_DEGRADED_UNRELATED_REASON in reasons
    assert "与当前问题无关" not in reasons
    database.close()


def test_deleted_item_leaves_index_and_rebuild_does_not_resurrect(
    tmp_path: Path,
) -> None:
    """删除后索引行移除；重建只从当前有效条目重算，不复活旧条目。"""

    database, service, index, _ = _semantic_env(
        tmp_path,
        {"线性代数": (1.0, 0.0, 0.0, 0.0), "检查": (0.97, 0.24, 0.0, 0.0)},
    )
    item = service.remember(ALICE, CONSTRAINT)
    query = "帮我检查线性代数安排"
    first = service.compile_adopted_slice(
        ALICE, purpose=build_purpose(mode="companion", query=query), run_id="run-1"
    )
    assert any(entry.fact_text == CONSTRAINT for entry in first.adopted_items)

    service.delete_item(ALICE, item.profile_item_id, item.version)
    after_delete = service.compile_adopted_slice(
        ALICE, purpose=build_purpose(mode="companion", query=query), run_id="run-2"
    )
    assert after_delete.adopted_items == ()
    rows = database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ? AND profile_item_id = ?",
        (ALICE, item.profile_item_id),
    ).fetchone()
    assert int(rows["count"]) == 0

    # 手工全量重建：输入是当前快照（墓碑正文为空且状态 withdrawn），
    # 重建后该条目仍无索引行，也永远不会被召回。
    index.rebuild(ALICE, items=service.list_items(ALICE), run_id="run-rebuild")
    rebuilt = database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ? AND profile_item_id = ?",
        (ALICE, item.profile_item_id),
    ).fetchone()
    assert int(rebuilt["count"]) == 0
    assert service.get_item(ALICE, item.profile_item_id).status is (
        AtomicProfileItemStatus.WITHDRAWN
    )
    database.close()


def test_index_is_account_isolated(tmp_path: Path) -> None:
    """语义召回索引按账户隔离：另一个账户的条目从不进入候选。"""

    database, service, _, _ = _semantic_env(
        tmp_path,
        {"线性代数": (1.0, 0.0, 0.0, 0.0), "安排": (0.97, 0.24, 0.0, 0.0)},
    )
    service.remember(ALICE, CONSTRAINT)

    bob_slice = service.compile_adopted_slice(
        BOB,
        purpose=build_purpose(mode="companion", query="帮我安排学习"),
        run_id="run-bob",
    )
    assert bob_slice.adopted_items == ()
    assert bob_slice.snapshot_versions == ()
    rows = database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ?",
        (BOB,),
    ).fetchone()
    assert int(rows["count"]) == 0
    database.close()


def test_contract_change_rebuilds_and_never_mixes_vectors(tmp_path: Path) -> None:
    """向量/召回合同变化时旧版本退休，新版本重建后原子切换。

    旧合同与新合同把「条目的改写问句」嵌入到不同方向，但各自与查询同向
    （都应召回）。若切换后沿用旧向量、只对新查询用新向量，两者正交、余弦
    为 0——断言会失败。这条测试因此能观察到「未重建/混用旧向量」的缺陷。
    """

    old_contract = ProfileRecallContract(policy_version="profile-recall-1.0")
    database, service, _, _ = _semantic_env(
        tmp_path,
        {"线性代数": (1.0, 0.0, 0.0, 0.0), "安排": (1.0, 0.0, 0.0, 0.0)},
        contract=old_contract,
    )
    service.remember(ALICE, CONSTRAINT)
    first = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="帮我安排学习"),
        run_id="run-old",
    )
    assert first.recall_index_version is not None
    assert first.adopted_items != ()

    # 合同变化（召回策略升级）：同一库上换新合同，旧向量不得混用。
    new_contract = ProfileRecallContract(policy_version="profile-recall-9.9")
    new_index = ProfileRecallIndex(
        database,
        _MappedEmbeddingPort(
            {"线性代数": (0.0, 1.0, 0.0, 0.0), "安排": (0.0, 1.0, 0.0, 0.0)}
        ),
        contract=new_contract,
    )
    service.set_recall_index(new_index)
    second = service.compile_adopted_slice(
        ALICE,
        purpose=build_purpose(mode="companion", query="帮我安排学习"),
        run_id="run-new",
    )
    assert second.recall_index_version is not None
    assert second.recall_index_version != first.recall_index_version
    # 新合同下条目以新向量重建：与查询同向，语义召回恢复工作。
    by_text = {item.fact_text: item for item in second.adopted_items}
    assert CONSTRAINT in by_text
    assert "semantic" in (by_text[CONSTRAINT].recall_basis or "")

    versions = database.connection.execute(
        "SELECT version_id, contract_hash FROM profile_recall_index_versions"
        " WHERE account_id = ?",
        (ALICE,),
    ).fetchall()
    assert [row["contract_hash"] for row in versions] == [
        new_contract.contract_hash()
    ]
    # 旧版本条目行不残留：也不存在能用旧向量打分的数据。
    old_entries = database.connection.execute(
        "SELECT COUNT(*) AS count FROM profile_recall_index_entries"
        " WHERE account_id = ? AND version_id != ?",
        (ALICE, str(versions[0]["version_id"])),
    ).fetchone()
    assert int(old_entries["count"]) == 0
    database.close()


def test_compile_without_question_skips_embedding_calls(tmp_path: Path) -> None:
    """没有当前问题时不做语义检索，也不产生任何向量调用。"""

    database, service, _, port = _semantic_env(tmp_path, {})
    service.remember(ALICE, CONSTRAINT)
    slice_ = service.compile_adopted_slice(ALICE, run_id="run-no-question")
    assert port.calls == []
    assert slice_.recall_degradation_reason is None
    assert slice_.recall_index_version is None
    # 无问题时不裁剪：有效条目按既有行为全部进入候选。
    assert [item.fact_text for item in slice_.adopted_items] == [CONSTRAINT]
    database.close()
