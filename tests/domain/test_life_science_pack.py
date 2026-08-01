"""T043 生命科学一般研究领域包的公开接缝测试。

覆盖研究文档 5.3 的确定性规则：基因/蛋白身份、关联与机制的边界、
细胞系外推、统计证据（效应量/伪重复/批次）、数据库版本状态和
病原体/人体隐私人工门。不依赖模型私有推理。
"""

from __future__ import annotations

from bridges.ai.capability_registry import CapabilityRegistry
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.domain import DomainPackLoader, DomainPackValidationRuntime
from bridges.domain.life_science import LifeScienceDomainPack


def _loaded_pack() -> tuple[LifeScienceDomainPack, object]:
    pack = LifeScienceDomainPack()
    loaded = DomainPackLoader(
        capability_registry=_capability_registry(),
        require_capability_contracts=True,
    ).load(pack)
    return pack, loaded


def _capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name, input_schema in (
        ("sequence_identifier_resolve", "sequence-identifier/v1"),
        ("annotation_state_check", "annotation-state/v1"),
        ("statistical_evidence_check", "statistical-evidence/v1"),
    ):
        registry.register(
            CapabilityRecord(
                name=name,
                version="1.0.0",
                kind=CapabilityKind.TOOL,
                vendor="test",
                region="local",
                input_schema_version=input_schema,
                output_schema_version="life-science-validation/v1",
            )
        )
    return registry


def _annotation_claim(**overrides: object) -> dict[str, object]:
    claim: dict[str, object] = {
        "claim_id": "claim.t043.annotation",
        "claim_type": "annotation",
        "gene_symbol": "TP53",
        "protein_accession": "P04637",
        "species": "Homo sapiens",
        "tissue_cell_line": "HEK293",
        "experimental_conditions": {"dose": "1 uM", "duration": "24 h"},
        "reference_group": "vehicle",
        "measurement_endpoint": "mRNA expression",
        "database_release": "uniprot-release-2026_01",
        "definition_version": "sequence-annotation-v1",
    }
    claim.update(overrides)
    return claim


def test_manifest_declares_scope_sources_rules_and_version_boundary() -> None:
    pack = LifeScienceDomainPack()
    manifest = pack.manifest

    assert "基因" in "".join(str(item) for item in manifest.scope)
    assert manifest.exclusions
    assert {"entity_identity", "annotation", "association", "mechanism"} <= set(
        manifest.question_types
    )
    assert any(
        "NCBI" in requirement
        for policy in manifest.source_policies
        for requirement in policy.evidence_requirements
    )
    assert any(
        "UniProt" in requirement
        for policy in manifest.source_policies
        for requirement in policy.evidence_requirements
    )
    assert any(rule.rule_id == "life-science.association_vs_mechanism" for rule in manifest.rules)
    assert any(rule.rule_id == "life-science.cell_line_extrapolation" for rule in manifest.rules)
    assert manifest.content_digest
    assert manifest.compatibility.preserves_runtime_contract is True


def test_runtime_replays_identity_annotation_and_degraded_fixtures() -> None:
    pack, loaded = _loaded_pack()

    report = DomainPackValidationRuntime().run(
        loaded,
        fixtures=list(pack.manifest.fixtures),
        run_id="run-t043-life-science",
    )

    assert report.status.value in {"passed", "needs_human"}
    results = {result.fixture_id: result for result in report.fixture_results}
    assert results["life-science.entity-identity.correct"].passed is True
    assert results["life-science.annotation.correct"].passed is True
    assert results["life-science.association.correct"].passed is True
    assert results["life-science.association.as-mechanism"].passed is True
    assert results["life-science.mechanism.cell-line-extrapolation"].passed is True
    assert results["life-science.gene-protein.confusion"].passed is True
    assert results["life-science.statistics.p-value-as-effect"].passed is True
    assert results["life-science.statistics.batch-effect"].passed is True
    assert results["life-science.statistics.pseudoreplication"].passed is True
    assert results["life-science.accession.stale"].passed is True
    assert results["life-science.annotation.unreviewed-as-fact"].passed is True
    assert results["life-science.biosafety.pathogen-enhanced"].passed is True
    assert results["life-science.conflict.evidence"].passed is True
    assert results["life-science.source-status.unknown"].passed is True
    assert results["life-science.coordinate.strand-error"].passed is True


def test_validate_claim_preserves_claim_evidence_fact_lock_and_report() -> None:
    pack, _ = _loaded_pack()
    claim = {
        **_annotation_claim(),
        "fact_lock_set": {"set_id": "platform-lock-set:trace", "locks": []},
    }
    evidence = [
        {
            "evidence_id": "e.annotation.1",
            "relation": "supports",
            "locator": "uniprot:P04637",
        }
    ]

    result = pack.validate_claim(claim, evidence)

    assert result["status"] == "verified"
    assert result["details"]["claim"]["claim_id"] == "claim.t043.annotation"
    assert result["details"]["evidence"][0]["evidence_id"] == "e.annotation.1"
    assert result["details"]["fact_lock_set"]["set_id"] == "platform-lock-set:trace"
    assert result["details"]["fact_lock_reference"]["owner"] == "platform.science.fact_lock"
    assert result["details"]["validation_report"]["annotation_checked"] is True
    assert result["details"]["validation_report"]["database_annotation_is_not_mechanism"] is True


def test_correlation_written_as_mechanism_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.association-as-mechanism",
            claim_type="mechanism",
            description="观察到基因 A 表达与蛋白 B 水平相关，因此基因 A 调控蛋白 B",
            association_only=True,
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "association_as_mechanism" in result["reason_codes"]


def test_cell_line_result_extrapolated_to_human_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.cell-line-extrapolation",
            claim_type="mechanism",
            description="在 HEK293 细胞系中敲除 TP53 使增殖增加，因此该基因在人脑中决定肿瘤发生",
            model_system="HEK293",
            human_extrapolation=True,
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "cell_line_extrapolation" in result["reason_codes"]


def test_gene_protein_identifier_confusion_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.gene-protein-confusion",
            gene_symbol="TP53",
            protein_accession="TP53",
            identity_type="protein",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "gene_protein_confusion" in result["reason_codes"]


def test_p_value_replacing_effect_size_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.p-value-as-effect",
            claim_type="expression_difference",
            measurement_endpoint="mRNA expression",
            statistics={"only_p_value": 0.0001, "effect_size": None},
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "p_value_as_effect_size" in result["reason_codes"]


def test_p_value_with_declared_effect_size_is_not_blocked() -> None:
    """回归：p 值与效应量同时声明是合格报告，不能被误阻断。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.p-value-with-effect",
            claim_type="expression_difference",
            measurement_endpoint="mRNA expression",
            statistics={
                "only_p_value": 0.0001,
                "effect_size": 2.3,
                "ci": "[1.5, 3.1]",
            },
        ),
        [],
    )

    assert result["status"] == "verified"
    assert "p_value_as_effect_size" not in result["reason_codes"]


def test_conflicting_evidence_locks_publish() -> None:
    """同一命题同时存在支持与反驳证据时进入 conflicted，不得单边发布。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.conflict",
            claim_type="association",
            description="两篇独立研究对同一关联给出相反结论，不能单边发布。",
        ),
        [
            {
                "evidence_id": "e.study.1",
                "relation": "supports",
                "locator": "study:2026",
                "lifecycle_status": "active",
            },
            {
                "evidence_id": "e.study.2",
                "relation": "refutes",
                "locator": "study:2026",
                "lifecycle_status": "active",
            },
        ],
    )

    assert result["status"] == "conflicted"
    assert "evidence_conflict" in result["reason_codes"]


def test_unknown_evidence_status_blocks_high_confidence_publish() -> None:
    """关键来源状态未知时不得按已验证发布。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(claim_id="claim.unknown-source"),
        [
            {
                "evidence_id": "e.db.unknown",
                "relation": "supports",
                "locator": "uniprot:P04637",
                "lifecycle_status": "unknown",
            }
        ],
    )

    assert result["status"] in {"blocked", "needs_human"}
    assert "source_status_unknown" in result["reason_codes"]


def test_strand_coordinate_error_is_blocked() -> None:
    """坐标区间不符合 1-based 规范（正链起始大于结束）时阻断。"""
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.coordinate-error",
            database_release="ncbi-refseq-2026",
            coordinate_start=100,
            coordinate_end=50,
            strand="+",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "coordinate_invalid" in result["reason_codes"]


def test_batch_effect_ignored_in_replication_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.batch-effect",
            claim_type="experimental_replication",
            statistics={"batch_covariates": False, "replicates": 3},
            description="三个重复均来自同一批次",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "batch_effect_ignored" in result["reason_codes"]


def test_pseudoreplication_is_blocked() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.pseudoreplication",
            claim_type="experimental_replication",
            statistics={"independent_samples": 1, "technical_replicates": 6},
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "pseudoreplication" in result["reason_codes"]


def test_stale_or_superseded_accession_blocks_new_publish() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.stale-accession",
            database_release="superseded",
            accession_status="superseded",
        ),
        [],
    )

    assert result["status"] == "blocked"
    assert "source_stale" in result["reason_codes"]


def test_unreviewed_annotation_presented_as_fact_requires_human_or_block() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.unreviewed-annotation",
            annotation_status="unreviewed",
            description="根据注释条目，该蛋白的功能是确定的",
            presented_as_fact=True,
        ),
        [],
    )

    assert result["status"] in {"blocked", "needs_human"}
    assert "unreviewed_annotation_as_fact" in result["reason_codes"]


def test_pathogen_enhancement_or_human_genetic_privacy_enters_human_gate() -> None:
    pack, _ = _loaded_pack()

    result = pack.validate_claim(
        _annotation_claim(
            claim_id="claim.biosafety",
            claim_type="mechanism",
            description="构建增强传播能力的病原体株系以验证致病机制",
            biosafety_flags=["pathogen_enhancement"],
        ),
        [],
    )

    assert result["status"] in {"blocked", "needs_human"}
    assert "dangerous_bio_hazard" in result["reason_codes"]
    assert result["details"]["validation_report"]["safety_handoff_required"] is True
