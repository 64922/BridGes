"""学习历史只读解码与公开投影。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from bridges.contracts.chat import StudyHistoryStatus, StudyHistoryStatusProjection
from bridges.contracts.study import (
    STUDY_STATE_VERSION,
    StudyState,
    upgrade_legacy_study_state,
)
from bridges.state_copy.registry import render_state_copy
from bridges.storage.database import BridgesDatabase


@dataclass(frozen=True, slots=True)
class StudyHistoryRead:
    """一次账户范围内的只读学习历史结果。"""

    study: StudyState | None
    status: StudyHistoryStatusProjection | None


class StudyHistoryReader:
    """独立于教学写模型的学习历史读取器。

    只读取指定账户和对话的状态行。已知旧版本只在内存中补齐当前读取字段，
    不保存升级结果；未来版本与损坏记录保留消息历史，但不伪造学习阶段。
    """

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    def read(self, account_id: str, conversation_id: str) -> StudyHistoryRead:
        row = (
            self._database.scoped(account_id)
            .execute(
                "SELECT state_json FROM study_states"
                " WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            )
            .fetchone()
        )
        if row is None:
            return StudyHistoryRead(study=None, status=None)

        try:
            payload = json.loads(row["state_json"])
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return self._unreadable()
        if not isinstance(payload, dict):
            return self._unreadable()

        version = payload.get("state_version", 1)
        if type(version) is not int or version < 1:
            return self._unreadable()
        if version > STUDY_STATE_VERSION:
            return StudyHistoryRead(
                study=None,
                status=StudyHistoryStatusProjection(
                    status=StudyHistoryStatus.UNSUPPORTED,
                    version=version,
                    message=render_state_copy(
                        "study.history.unsupported", version=version
                    ),
                ),
            )

        try:
            state = StudyState.model_validate(payload)
        except ValidationError:
            return self._unreadable(version=version)

        state = upgrade_legacy_study_state(state)
        status = None
        if version < STUDY_STATE_VERSION:
            status = StudyHistoryStatusProjection(
                status=StudyHistoryStatus.LEGACY,
                version=version,
                message=render_state_copy("study.history.legacy", version=version),
            )
        return StudyHistoryRead(study=state.public_view(), status=status)

    @staticmethod
    def _unreadable(version: int | None = None) -> StudyHistoryRead:
        return StudyHistoryRead(
            study=None,
            status=StudyHistoryStatusProjection(
                status=StudyHistoryStatus.UNREADABLE,
                version=version,
                message=render_state_copy("study.history.unreadable"),
            ),
        )
