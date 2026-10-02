"""工单 15 验收：缺失额度闭锁与逐候选材料追溯。"""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from bridges.github.contracts import (
    GithubCoverage,
    GithubEvidenceKind,
    GithubLicenseCheck,
    GithubMaintenanceEvidence,
    GithubReadmeStatus,
    GithubRecommendation,
)
from bridges.github.presenting import GithubInsightGenerator
from bridges.paper.presenting import PaperSummaryGenerator
from tests.chat.test_improvement15_task_materials import (
    _CapturingGateway,
    _paper_recommendation,
    _quota,
)


def _invoke(module, quota):
    gateway = _CapturingGateway()
    if module == "paper":
        outcome = PaperSummaryGenerator(gateway).generate(
            {},
            [_paper_recommendation()],
            abstracts={"1706.03762": "不应进审计的摘要"},
            model_id="qwen-plus",
            model_quota=quota,
        )
        candidate_id = "arxiv:1706.03762"
    else:
        recommendation = GithubRecommendation(
            rank=1,
            full_name="owner/repo",
            html_url="https://github.com/owner/repo",
            coverage=GithubCoverage.WHOLE,
            coverage_note="整体覆盖",
            evidence_kinds=[GithubEvidenceKind.METADATA],
            readme_status=GithubReadmeStatus.READ,
            readme_excerpt="不应进审计的片段" * 200,
            maintenance=GithubMaintenanceEvidence(note="元数据"),
            license=GithubLicenseCheck(detected=False, note="未标注"),
            reason_zh="匹配",
            borrow_note="参考",
            retrieved_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
        outcome = GithubInsightGenerator(gateway).generate(
            {},
            [recommendation],
            model_id="qwen-plus",
            model_quota=quota,
        )
        candidate_id = "github:owner/repo"
    return gateway, outcome, candidate_id


@pytest.mark.parametrize("module", ["paper", "github"])
def test_missing_quota_blocks_and_preserves_manifest(module):
    gateway, outcome, _ = _invoke(module, None)
    assert not gateway.calls
    assert outcome.manifest is not None
    assert not outcome.manifest.gate.quota_verified
    assert not outcome.manifest.gate.within_budget
    assert "无法验证" in outcome.note


@pytest.mark.parametrize("module", ["paper", "github"])
def test_manifest_tracks_exact_candidate_slice_and_schema(module):
    gateway, outcome, candidate_id = _invoke(module, _quota(32000))
    assert len(gateway.calls) == 1
    entries = {entry.material_id: entry for entry in outcome.manifest.entries}
    candidate = entries[candidate_id]
    assert candidate.read_range
    assert candidate.source_version.startswith("sha256:")
    schema = entries[f"{module}.{'summary' if module == 'paper' else 'insight'}.schema"]
    serialized_schema = json.dumps(
        gateway.calls[0]["payload"]["json_schema"], ensure_ascii=False, sort_keys=True
    )
    assert (
        schema.source_version == "sha256:" + hashlib.sha256(serialized_schema.encode()).hexdigest()
    )
    record = json.dumps(outcome.manifest.to_record(), ensure_ascii=False)
    assert "不应进审计" not in record


@pytest.mark.parametrize("module", ["paper", "github"])
def test_candidate_version_hashes_only_the_sent_slice(module):
    gateway, outcome, candidate_id = _invoke(module, _quota(32000))
    text = gateway.calls[0]["payload"]["messages"][1]["content"]
    candidate_slice = "\n".join(text.splitlines()[1:-1])
    entry = next(entry for entry in outcome.manifest.entries if entry.material_id == candidate_id)
    assert entry.source_version == "sha256:" + hashlib.sha256(candidate_slice.encode()).hexdigest()
    if module == "github":
        assert "README字符[0:600]" in entry.read_range
