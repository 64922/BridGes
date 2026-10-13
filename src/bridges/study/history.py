"""学习历史只读解码与公开投影。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from bridges.contracts.study import (
    STUDY_STATE_VERSION,
    StudyState,
    upgrade_legacy_study_state,
)
from bridges.contracts.study_history import (
    StudyHistoryProjection,
    StudyHistoryStatus,
    StudyHistoryStatusProjection,
    StudyMaterialStatus,
    StudyMaterialStatusProjection,
)
from bridges.state_copy.registry import render_state_copy
from bridges.storage.database import BridgesDatabase


@dataclass(frozen=True, slots=True)
class StudyHistoryRead:
    """一次账户范围内的只读学习历史结果。"""

    study: StudyHistoryProjection | None
    status: StudyHistoryStatusProjection | None


class StudyHistoryReader:
    """独立于教学写模型的学习历史读取器。

    只读取指定账户和对话的状态行。已知旧版本只在内存中补齐当前读取字段，
    不保存升级结果；未来版本与损坏记录保留消息历史，但不伪造学习阶段。
    """

    def __init__(
        self,
        database: BridgesDatabase,
        material_status: Callable[[str, str], str | None] | None = None,
    ) -> None:
        self._database = database
        self._material_status = material_status

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
        materials = self._project_materials(account_id, state)
        public_state = state.public_view()
        payload = public_state.model_dump(mode="python")
        payload["materials"] = materials
        return StudyHistoryRead(
            study=StudyHistoryProjection.model_validate(
                payload
            ),
            status=status,
        )

    def _project_materials(
        self, account_id: str, state: StudyState
    ) -> list[StudyMaterialStatusProjection]:
        object_ids = {page.object_id for page in state.pages}
        if state.page_update is not None:
            object_ids.update(page.object_id for page in state.page_update.pages)
        projected = []
        for object_id in sorted(object_ids):
            if self._material_status is None:
                status: StudyMaterialStatus = "unknown"
            else:
                stored_status = self._material_status(account_id, object_id)
                if stored_status == "active":
                    status = "active"
                elif stored_status == "pending_cleanup":
                    status = "pending_cleanup"
                elif stored_status is None:
                    status = "deleted"
                else:
                    status = "unknown"
            projected.append(
                StudyMaterialStatusProjection(object_id=object_id, status=status)
            )
        return projected

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
