"""评测套件持久化仓库（Issue 40）。

评测资产（套件、运行锁、案例结果、报告、盲评集）是评测域资产而非用户
数据：不进入账户作用域表（账户删除不影响评测记录），但随整库备份恢复
（bridges.db 快照自带）。结果与报告 append-only：同一运行锁的重放产生
新行，旧行永不覆盖或删除，保证「旧结果仍可追溯且不会被静默覆盖」。
"""

from __future__ import annotations

import json
from typing import Any

from bridges.contracts.evaluation_suite import (
    BlindReviewSet,
    CaseResult,
    EvaluationReport,
    SuiteDefinition,
    SuiteRunLock,
)
from bridges.storage.database import BridgesDatabase
from bridges.storage.errors import StorageError


def _as_legacy_view(review_set: BlindReviewSet) -> BlindReviewSet:
    """读取视图：含人工提交的旧盲评集只读标记为 legacy（Issue 10）。

    不修改存储内容；旧集以只读视图展示，不进入新的隔离多模型自动统计。
    """
    if review_set.legacy or not review_set.submissions:
        return review_set
    return review_set.model_copy(update={"legacy": True})


class EvaluationRepositoryError(Exception):
    """评测仓库领域错误。"""


class EvaluationRepository:
    """评测套件的 SQLite 仓库（JSON 列 + append-only 行）。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # 套件
    # ------------------------------------------------------------------

    def save_suite(self, suite: SuiteDefinition) -> None:
        try:
            with self._database.transaction():
                self._database.connection.execute(
                    "INSERT INTO eval_suites"
                    " (suite_id, version, digest, definition_json, status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(suite_id, version) DO NOTHING",
                    (
                        suite.suite_id,
                        suite.version,
                        suite.digest(),
                        suite.model_dump_json(),
                        suite.status.value,
                        suite.created_at,
                    ),
                )
        except StorageError as exc:
            raise EvaluationRepositoryError(str(exc)) from exc

    def get_suite(self, suite_id: str, version: str) -> SuiteDefinition | None:
        row = self._database.connection.execute(
            "SELECT definition_json FROM eval_suites"
            " WHERE suite_id = ? AND version = ?",
            (suite_id, version),
        ).fetchone()
        if row is None:
            return None
        return SuiteDefinition.model_validate_json(str(row["definition_json"]))

    def list_suite_versions(self, suite_id: str) -> list[str]:
        rows = self._database.connection.execute(
            "SELECT version FROM eval_suites WHERE suite_id = ? ORDER BY version",
            (suite_id,),
        ).fetchall()
        return [str(row["version"]) for row in rows]

    # ------------------------------------------------------------------
    # 运行锁
    # ------------------------------------------------------------------

    def save_lock(self, lock: SuiteRunLock) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "INSERT INTO eval_run_locks"
                " (lock_id, suite_id, suite_version, suite_digest, lock_json,"
                " lock_digest, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    lock.lock_id,
                    lock.suite_id,
                    lock.suite_version,
                    lock.suite_digest,
                    lock.model_dump_json(),
                    lock.digest(),
                    lock.created_at,
                ),
            )

    def get_lock(self, lock_id: str) -> SuiteRunLock | None:
        row = self._database.connection.execute(
            "SELECT lock_json FROM eval_run_locks WHERE lock_id = ?", (lock_id,)
        ).fetchone()
        if row is None:
            return None
        return SuiteRunLock.model_validate_json(str(row["lock_json"]))

    # ------------------------------------------------------------------
    # 案例结果（append-only）
    # ------------------------------------------------------------------

    def save_result(self, result: CaseResult) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "INSERT INTO eval_case_results"
                " (case_result_id, lock_id, sut_id, task_id, case_id, seed,"
                "  execution_index, result_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.case_result_id,
                    result.lock_id,
                    result.sut_id,
                    result.task_id,
                    result.case_id,
                    result.seed,
                    result.execution_index,
                    result.model_dump_json(),
                    result.created_at,
                ),
            )

    def get_result(self, case_result_id: str) -> CaseResult | None:
        row = self._database.connection.execute(
            "SELECT result_json FROM eval_case_results WHERE case_result_id = ?",
            (case_result_id,),
        ).fetchone()
        if row is None:
            return None
        return CaseResult.model_validate_json(str(row["result_json"]))

    def list_results(self, lock_id: str) -> list[CaseResult]:
        rows = self._database.connection.execute(
            "SELECT result_json FROM eval_case_results WHERE lock_id = ?"
            " ORDER BY created_at",
            (lock_id,),
        ).fetchall()
        return [CaseResult.model_validate_json(str(row["result_json"])) for row in rows]

    # ------------------------------------------------------------------
    # 报告（append-only，版本不覆盖）
    # ------------------------------------------------------------------

    def save_report(self, report: EvaluationReport) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "INSERT INTO eval_reports"
                " (report_id, report_version, lock_id, report_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    report.report_id,
                    report.report_version,
                    report.lock_id,
                    report.model_dump_json(),
                    report.generated_at,
                ),
            )

    def get_report(
        self, report_id: str, report_version: str | None = None
    ) -> EvaluationReport | None:
        if report_version is None:
            row = self._database.connection.execute(
                "SELECT report_json FROM eval_reports WHERE report_id = ?"
                " ORDER BY report_version DESC LIMIT 1",
                (report_id,),
            ).fetchone()
        else:
            row = self._database.connection.execute(
                "SELECT report_json FROM eval_reports"
                " WHERE report_id = ? AND report_version = ?",
                (report_id, report_version),
            ).fetchone()
        if row is None:
            return None
        return EvaluationReport.model_validate_json(str(row["report_json"]))

    def list_reports(self, lock_id: str) -> list[EvaluationReport]:
        rows = self._database.connection.execute(
            "SELECT report_json FROM eval_reports WHERE lock_id = ?"
            " ORDER BY report_version",
            (lock_id,),
        ).fetchall()
        return [EvaluationReport.model_validate_json(str(row["report_json"])) for row in rows]

    # ------------------------------------------------------------------
    # 盲评集与提交（append-only）
    # ------------------------------------------------------------------

    def save_review_set(self, review_set: BlindReviewSet) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "INSERT INTO eval_blind_reviews (review_set_id, lock_id, set_json, created_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(review_set_id) DO NOTHING",
                (
                    review_set.review_set_id,
                    review_set.lock_id,
                    review_set.model_dump_json(),
                    review_set.created_at,
                ),
            )

    def get_review_set(self, review_set_id: str) -> BlindReviewSet | None:
        row = self._database.connection.execute(
            "SELECT set_json FROM eval_blind_reviews WHERE review_set_id = ?",
            (review_set_id,),
        ).fetchone()
        if row is None:
            return None
        return _as_legacy_view(
            BlindReviewSet.model_validate_json(str(row["set_json"]))
        )

    def save_review_submission(
        self, review_set_id: str, set_json: dict[str, Any]
    ) -> None:
        with self._database.transaction():
            self._database.connection.execute(
                "UPDATE eval_blind_reviews SET set_json = ? WHERE review_set_id = ?",
                (json.dumps(set_json, ensure_ascii=False), review_set_id),
            )

    def list_review_sets(self, lock_id: str) -> list[BlindReviewSet]:
        rows = self._database.connection.execute(
            "SELECT set_json FROM eval_blind_reviews WHERE lock_id = ? ORDER BY created_at",
            (lock_id,),
        ).fetchall()
        return [
            _as_legacy_view(BlindReviewSet.model_validate_json(str(row["set_json"])))
            for row in rows
        ]


__all__ = ["EvaluationRepository", "EvaluationRepositoryError"]
