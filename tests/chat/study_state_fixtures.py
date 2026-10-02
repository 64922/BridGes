"""合法学习阶段夹具：为学习管线测试提供已识别书页与阶段证据。

前置书页门（``study_pages_required``）要求学习会话先有 ``awaiting_pages``、
``tutoring``、``review`` 或 ``summary`` 阶段的真实状态。这里写入的阶段证据
代表「照片已完成识别」，供不经过 StudyWorkflow（直接调用
``ChatService.stream_generation``）的日常学习管线测试使用。
"""

from __future__ import annotations

from typing import Any

from bridges.contracts.study import StudyFragment, StudyPage, StudyState, StudyUnit
from bridges.study.service import StudyRepository


def seed_recognized_study_state(
    service: Any,
    account_id: str,
    conversation_id: str,
    *,
    object_id: str = "study-photo-page-1",
    text: str = "线性函数 y=ax+b，斜率 a 表示变化率。",
    page_number: int = 12,
) -> StudyState:
    """写入一张已识别书页的 tutoring 阶段状态，返回同一份状态。"""
    fragment_id = f"{object_id}-f1"
    state = StudyState(
        subsection_id=conversation_id,
        stage="tutoring",
        pages=[
            StudyPage(
                object_id=object_id,
                ordinal=1,
                content_hash=f"hash-{object_id}",
                model_id="qwen3-vl-plus-2026-05-26",
                page_number=page_number,
                fragments=[
                    StudyFragment(
                        fragment_id=fragment_id,
                        kind="text",
                        position="顶部",
                        text=text,
                        confidence=0.95,
                    )
                ],
            )
        ],
        units=[StudyUnit(title="线性函数", fragment_ids=[fragment_id])],
    )
    StudyRepository(service._repo.database).save(account_id, conversation_id, state)
    return state
