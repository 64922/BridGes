"""同一筛选节点内复用已取得的语义裁决，避免耗尽共享模型次数。"""

from collections.abc import Mapping
from typing import Any

from bridges.paper.contracts import PaperTermAnalysis
from bridges.paper.semantic_evidence import concept_signature
from bridges.paper.sources import PaperCandidate


class NodeRelevanceCache:
    """仅活跃节点持有；标题、摘要或需求映射改变时重新核验，不跨账户/任务共享。"""

    def __init__(self, judge: Any) -> None:
        self._judge = judge
        self._results: dict[tuple[str, str, str, str], Mapping[str, Any] | None] = {}

    def judge_many(
        self, analysis: PaperTermAnalysis, candidates: list[PaperCandidate],
    ) -> Mapping[str, Mapping[str, Any]]:
        signature = concept_signature(analysis)
        missing = [
            item for item in candidates if self._key(signature, item) not in self._results
        ]
        if missing:
            try:
                output = self._judge.judge_many(analysis, missing) or {}
            except Exception:  # noqa: BLE001 - 失败裁决同样收敛，交给证据门拒绝推荐
                output = {}
            for item in missing:
                self._results[self._key(signature, item)] = (
                    output.get(item.candidate_id) or output.get(item.arxiv_id or "")
                )
        return {
            item.candidate_id: result for item in candidates
            if (result := self._results.get(self._key(signature, item))) is not None
        }

    @staticmethod
    def _key(signature: str, candidate: PaperCandidate) -> tuple[str, str, str, str]:
        return signature, candidate.candidate_id, candidate.title, candidate.abstract
