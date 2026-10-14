"""学习测试夹具：账户补齐与旧状态直接播种（工单 12 起旧执行退役）。

旧五阶段执行（``bridges.study`` 的识别/辅导/复盘/总结）已删除，测试不再
经旧图播种状态：账户外键用 :func:`ensure_test_account` 补齐，历史只读
样本用 :func:`seed_study_state` 直接写入 ``study_states``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.contracts.study import StudyState


def ensure_test_account(service: Any, account_id: str) -> None:
    """补齐 accounts 行，使学习目标/进度等账户外键在本进程内可写。"""

    database = service._repo.database
    now = datetime.now(UTC).isoformat()
    with database.transaction():
        database.connection.execute(
            "INSERT OR IGNORE INTO accounts(account_id, email, created_at)"
            " VALUES (?, ?, ?)",
            (account_id, f"{account_id}@study-fixture.example", now),
        )


def seed_study_state(
    service: Any,
    account_id: str,
    conversation_id: str,
    state: StudyState,
) -> None:
    """把已构造的学习状态写入 ``study_states``，作为历史只读样本。"""

    database = service._repo.database
    now = datetime.now(UTC).isoformat()
    with database.transaction():
        database.scoped(account_id).execute(
            "INSERT INTO study_states"
            " (account_id, conversation_id, state_json, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(account_id, conversation_id) DO UPDATE SET"
            " state_json = excluded.state_json, updated_at = excluded.updated_at",
            (account_id, conversation_id, state.model_dump_json(), now),
        )
