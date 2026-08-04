"""SQLite 持久化画像仓库（Issue 26：重启后画像、许可与通知仍可追溯）。

实现 :class:`ProfileRepository` 端口；查询经 :meth:`BridgesDatabase.scoped`
账户作用域查询面执行，INSERT 必须带 ``account_id`` 列、其余语句必须带
``account_id`` 过滤，跨账户访问从"约定"升级为"不可能"。唯一例外是
:meth:`get_slice_by_id`（端口设计，调用方按 run 绑定校验，见该方法注释）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from bridges.contracts.profiles import (
    AssertionStatus,
    CandidateReviewStatus,
    CandidateStabilityState,
    HumanDecision,
    ObservationStatus,
    ProfileAssertion,
    ProfileAssertionVersion,
    ProfileCandidate,
    ProfileDimension,
    ProfileNotification,
    ProfileNotificationKind,
    ProfileObservation,
    ProfilePermission,
    ProfileSensitivityClass,
    ProfileSignalKind,
    ProfileSlice,
    ProfileSliceItem,
    ProfileSourceType,
    RejectedSliceItem,
    SliceStatus,
    UnusedSliceItem,
)
from bridges.profiles.adapters import ProfileError
from bridges.profiles.ports import ProfileRepository
from bridges.storage.database import BridgesDatabase

_PROFILE_COLUMNS = (
    "observation_id, account_id, project_id, source_type, source_ref, "
    "source_span_or_event, scene, purpose, observed_content, signal_kind, "
    "extractor_and_version, model_rationale, reliability_factors_json, "
    "sensitivity_class, retention_policy, authorization_version, content_hash, "
    "status, created_at, updated_at"
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _parse_json_list(value: str) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    return [str(item) for item in parsed]


def _parse_json_dict(value: str) -> dict[str, str]:
    if not value:
        return {}
    return {str(k): str(v) for k, v in json.loads(value).items()}


class SqliteProfileRepository(ProfileRepository):
    """bridges.db 之上的画像仓库：观察、候选、断言、切片、许可与通知。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database
        # 构造即保证数据模式可用；initialize 幂等，重复调用安全。
        self._db.initialize()

    # ------------------------------------------------------------------
    # 观察
    # ------------------------------------------------------------------

    def save_observation(self, observation: ProfileObservation) -> ProfileObservation:
        with self._db.transaction():
            self._db.scoped(observation.owner_account_id).execute(
                "INSERT INTO profile_observations (" + _PROFILE_COLUMNS + ") "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    observation.observation_id,
                    observation.owner_account_id,
                    observation.project_id,
                    observation.source_type.value,
                    observation.source_ref,
                    observation.source_span_or_event,
                    observation.scene,
                    observation.purpose,
                    observation.observed_content,
                    observation.signal_kind.value,
                    observation.extractor_and_version,
                    observation.model_rationale,
                    json.dumps(observation.reliability_factors, ensure_ascii=False),
                    observation.sensitivity_class.value,
                    observation.retention_policy,
                    observation.authorization_version,
                    observation.content_hash,
                    observation.status.value,
                    _iso(observation.created_at),
                    _iso(observation.updated_at),
                ),
            )
        return observation

    def get_observation(
        self, owner_id: str, observation_id: str
    ) -> ProfileObservation:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_observations "
            "WHERE account_id = ? AND observation_id = ?",
            (owner_id, observation_id),
        ).fetchone()
        if row is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return self._observation_from_row(row)

    def list_observations(self, owner_id: str) -> list[ProfileObservation]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_observations "
            "WHERE account_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
        return [self._observation_from_row(row) for row in rows]

    def find_discarded_observation(
        self, owner_id: str, content_hash: str
    ) -> ProfileObservation | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_observations "
            "WHERE account_id = ? AND content_hash = ? AND status = 'discarded' "
            "LIMIT 1",
            (owner_id, content_hash),
        ).fetchone()
        return self._observation_from_row(row) if row is not None else None

    def find_observation_by_source(
        self, owner_id: str, content_hash: str, source_ref: str
    ) -> ProfileObservation | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_observations "
            "WHERE account_id = ? AND content_hash = ? AND source_ref = ? "
            "LIMIT 1",
            (owner_id, content_hash, source_ref),
        ).fetchone()
        return self._observation_from_row(row) if row is not None else None

    @staticmethod
    def _observation_from_row(row: sqlite3.Row) -> ProfileObservation:
        return ProfileObservation(
            observation_id=str(row["observation_id"]),
            owner_account_id=str(row["account_id"]),
            project_id=row["project_id"],
            source_type=ProfileSourceType(str(row["source_type"])),
            source_ref=str(row["source_ref"]),
            source_span_or_event=str(row["source_span_or_event"]),
            scene=str(row["scene"]),
            purpose=str(row["purpose"]),
            observed_content=str(row["observed_content"]),
            signal_kind=ProfileSignalKind(str(row["signal_kind"])),
            extractor_and_version=str(row["extractor_and_version"]),
            model_rationale=row["model_rationale"],
            reliability_factors=_parse_json_list(str(row["reliability_factors_json"])),
            sensitivity_class=ProfileSensitivityClass(str(row["sensitivity_class"])),
            retention_policy=str(row["retention_policy"]),
            authorization_version=str(row["authorization_version"]),
            content_hash=str(row["content_hash"]),
            status=ObservationStatus(str(row["status"])),
            created_at=_parse_dt(str(row["created_at"])) or datetime.now(UTC),
            updated_at=_parse_dt(str(row["updated_at"])) or datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # 候选
    # ------------------------------------------------------------------

    def save_candidate(self, candidate: ProfileCandidate) -> ProfileCandidate:
        with self._db.transaction():
            existing = self._db.scoped(candidate.owner_account_id).execute(
                "SELECT 1 FROM profile_candidates "
                "WHERE account_id = ? AND candidate_id = ?",
                (candidate.owner_account_id, candidate.candidate_id),
            ).fetchone()
            if existing is None:
                self._db.scoped(candidate.owner_account_id).execute(
                    "INSERT INTO profile_candidates ("
                    "candidate_id, account_id, canonical_dimension, value_or_rule, "
                    "applicable_scenes_json, non_applicable_scenes_json, "
                    "supporting_observation_ids_json, contradicting_observation_ids_json, "
                    "evidence_summary, authorization_scope, promotion_policy_version, "
                    "review_status, stability_state, sensitivity_class, proposed_at, "
                    "updated_at, expires_at, human_decision_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        candidate.candidate_id,
                        candidate.owner_account_id,
                        candidate.canonical_dimension,
                        candidate.value_or_rule,
                        json.dumps(candidate.applicable_scenes, ensure_ascii=False),
                        json.dumps(candidate.non_applicable_scenes, ensure_ascii=False),
                        json.dumps(
                            candidate.supporting_observation_ids, ensure_ascii=False
                        ),
                        json.dumps(
                            candidate.contradicting_observation_ids, ensure_ascii=False
                        ),
                        candidate.evidence_summary,
                        candidate.authorization_scope,
                        candidate.promotion_policy_version,
                        candidate.review_status.value,
                        candidate.stability_state.value,
                        candidate.sensitivity_class.value,
                        _iso(candidate.proposed_at),
                        _iso(candidate.updated_at),
                        _iso(candidate.expires_at) if candidate.expires_at else None,
                        (
                            json.dumps(
                                candidate.human_decision.model_dump(mode="json"),
                                ensure_ascii=False,
                            )
                            if candidate.human_decision is not None
                            else None
                        ),
                    ),
                )
            else:
                self._db.scoped(candidate.owner_account_id).execute(
                    "UPDATE profile_candidates SET canonical_dimension = ?, "
                    "value_or_rule = ?, applicable_scenes_json = ?, "
                    "non_applicable_scenes_json = ?, supporting_observation_ids_json = ?, "
                    "contradicting_observation_ids_json = ?, evidence_summary = ?, "
                    "authorization_scope = ?, promotion_policy_version = ?, "
                    "review_status = ?, stability_state = ?, sensitivity_class = ?, "
                    "proposed_at = ?, updated_at = ?, expires_at = ?, "
                    "human_decision_json = ? WHERE account_id = ? AND candidate_id = ?",
                    (
                        candidate.canonical_dimension,
                        candidate.value_or_rule,
                        json.dumps(candidate.applicable_scenes, ensure_ascii=False),
                        json.dumps(candidate.non_applicable_scenes, ensure_ascii=False),
                        json.dumps(
                            candidate.supporting_observation_ids, ensure_ascii=False
                        ),
                        json.dumps(
                            candidate.contradicting_observation_ids, ensure_ascii=False
                        ),
                        candidate.evidence_summary,
                        candidate.authorization_scope,
                        candidate.promotion_policy_version,
                        candidate.review_status.value,
                        candidate.stability_state.value,
                        candidate.sensitivity_class.value,
                        _iso(candidate.proposed_at),
                        _iso(candidate.updated_at),
                        _iso(candidate.expires_at) if candidate.expires_at else None,
                        (
                            json.dumps(
                                candidate.human_decision.model_dump(mode="json"),
                                ensure_ascii=False,
                            )
                            if candidate.human_decision is not None
                            else None
                        ),
                        candidate.owner_account_id,
                        candidate.candidate_id,
                    ),
                )
        return candidate

    def get_candidate(self, owner_id: str, candidate_id: str) -> ProfileCandidate:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_candidates "
            "WHERE account_id = ? AND candidate_id = ?",
            (owner_id, candidate_id),
        ).fetchone()
        if row is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return self._candidate_from_row(row)

    def list_candidates(self, owner_id: str) -> list[ProfileCandidate]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_candidates "
            "WHERE account_id = ? ORDER BY proposed_at DESC",
            (owner_id,),
        ).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> ProfileCandidate:
        human_decision_raw = row["human_decision_json"]
        return ProfileCandidate(
            candidate_id=str(row["candidate_id"]),
            owner_account_id=str(row["account_id"]),
            canonical_dimension=str(row["canonical_dimension"]),
            value_or_rule=str(row["value_or_rule"]),
            applicable_scenes=_parse_json_list(str(row["applicable_scenes_json"])),
            non_applicable_scenes=_parse_json_list(
                str(row["non_applicable_scenes_json"])
            ),
            supporting_observation_ids=_parse_json_list(
                str(row["supporting_observation_ids_json"])
            ),
            contradicting_observation_ids=_parse_json_list(
                str(row["contradicting_observation_ids_json"])
            ),
            evidence_summary=str(row["evidence_summary"]),
            authorization_scope=str(row["authorization_scope"]),
            promotion_policy_version=str(row["promotion_policy_version"]),
            review_status=CandidateReviewStatus(str(row["review_status"])),
            stability_state=CandidateStabilityState(str(row["stability_state"])),
            sensitivity_class=ProfileSensitivityClass(str(row["sensitivity_class"])),
            proposed_at=_parse_dt(str(row["proposed_at"])) or datetime.now(UTC),
            updated_at=_parse_dt(str(row["updated_at"])) or datetime.now(UTC),
            expires_at=_parse_dt(row["expires_at"]),
            human_decision=(
                HumanDecision.model_validate(json.loads(human_decision_raw))
                if human_decision_raw
                else None
            ),
        )

    # ------------------------------------------------------------------
    # 断言与版本
    # ------------------------------------------------------------------

    def save_assertion(self, assertion: ProfileAssertion) -> ProfileAssertion:
        with self._db.transaction():
            existing = self._db.scoped(assertion.owner_account_id).execute(
                "SELECT 1 FROM profile_assertions "
                "WHERE account_id = ? AND assertion_id = ?",
                (assertion.owner_account_id, assertion.assertion_id),
            ).fetchone()
            if existing is None:
                self._db.scoped(assertion.owner_account_id).execute(
                    "INSERT INTO profile_assertions ("
                    "assertion_id, account_id, canonical_dimension, value_or_rule, "
                    "applicable_scenes_json, supporting_observation_ids_json, "
                    "contradicting_observation_ids_json, authorization_scope, status, "
                    "sensitivity_class, expires_at, promoted_from_candidate_id, version, "
                    "last_used_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        assertion.assertion_id,
                        assertion.owner_account_id,
                        assertion.canonical_dimension,
                        assertion.value_or_rule,
                        json.dumps(assertion.applicable_scenes, ensure_ascii=False),
                        json.dumps(
                            assertion.supporting_observation_ids, ensure_ascii=False
                        ),
                        json.dumps(
                            assertion.contradicting_observation_ids, ensure_ascii=False
                        ),
                        assertion.authorization_scope,
                        assertion.status.value,
                        assertion.sensitivity_class.value,
                        _iso(assertion.expires_at) if assertion.expires_at else None,
                        assertion.promoted_from_candidate_id,
                        assertion.version,
                        _iso(assertion.last_used_at) if assertion.last_used_at else None,
                        _iso(assertion.created_at),
                        _iso(assertion.updated_at),
                    ),
                )
            else:
                self._db.scoped(assertion.owner_account_id).execute(
                    "UPDATE profile_assertions SET canonical_dimension = ?, "
                    "value_or_rule = ?, applicable_scenes_json = ?, "
                    "supporting_observation_ids_json = ?, "
                    "contradicting_observation_ids_json = ?, authorization_scope = ?, "
                    "status = ?, sensitivity_class = ?, expires_at = ?, "
                    "promoted_from_candidate_id = ?, version = ?, last_used_at = ?, "
                    "updated_at = ? WHERE account_id = ? AND assertion_id = ?",
                    (
                        assertion.canonical_dimension,
                        assertion.value_or_rule,
                        json.dumps(assertion.applicable_scenes, ensure_ascii=False),
                        json.dumps(
                            assertion.supporting_observation_ids, ensure_ascii=False
                        ),
                        json.dumps(
                            assertion.contradicting_observation_ids, ensure_ascii=False
                        ),
                        assertion.authorization_scope,
                        assertion.status.value,
                        assertion.sensitivity_class.value,
                        _iso(assertion.expires_at) if assertion.expires_at else None,
                        assertion.promoted_from_candidate_id,
                        assertion.version,
                        (
                            _iso(assertion.last_used_at)
                            if assertion.last_used_at
                            else None
                        ),
                        _iso(assertion.updated_at),
                        assertion.owner_account_id,
                        assertion.assertion_id,
                    ),
                )
        return assertion

    def get_assertion(self, owner_id: str, assertion_id: str) -> ProfileAssertion:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_assertions "
            "WHERE account_id = ? AND assertion_id = ?",
            (owner_id, assertion_id),
        ).fetchone()
        if row is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return self._assertion_from_row(row)

    def list_assertions(self, owner_id: str) -> list[ProfileAssertion]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_assertions "
            "WHERE account_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
        return [self._assertion_from_row(row) for row in rows]

    def find_assertion_by_value(
        self, owner_id: str, dimension: str, value: str
    ) -> ProfileAssertion | None:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_assertions "
            "WHERE account_id = ? AND canonical_dimension = ? AND value_or_rule = ? "
            "AND status IN ('active', 'frozen', 'withdrawn') LIMIT 1",
            (owner_id, dimension, value),
        ).fetchone()
        return self._assertion_from_row(row) if row is not None else None

    @staticmethod
    def _assertion_from_row(row: sqlite3.Row) -> ProfileAssertion:
        return ProfileAssertion(
            assertion_id=str(row["assertion_id"]),
            owner_account_id=str(row["account_id"]),
            canonical_dimension=str(row["canonical_dimension"]),
            value_or_rule=str(row["value_or_rule"]),
            applicable_scenes=_parse_json_list(str(row["applicable_scenes_json"])),
            supporting_observation_ids=_parse_json_list(
                str(row["supporting_observation_ids_json"])
            ),
            contradicting_observation_ids=_parse_json_list(
                str(row["contradicting_observation_ids_json"])
            ),
            authorization_scope=str(row["authorization_scope"]),
            status=AssertionStatus(str(row["status"])),
            sensitivity_class=ProfileSensitivityClass(str(row["sensitivity_class"])),
            expires_at=_parse_dt(row["expires_at"]),
            promoted_from_candidate_id=row["promoted_from_candidate_id"],
            version=int(row["version"]),
            last_used_at=_parse_dt(row["last_used_at"]),
            created_at=_parse_dt(str(row["created_at"])) or datetime.now(UTC),
            updated_at=_parse_dt(str(row["updated_at"])) or datetime.now(UTC),
        )

    def save_assertion_version(
        self, version: ProfileAssertionVersion
    ) -> ProfileAssertionVersion:
        with self._db.transaction():
            self._db.scoped(version.owner_account_id).execute(
                "INSERT INTO profile_assertion_versions ("
                "version_id, assertion_id, account_id, version, canonical_dimension, "
                "value_or_rule, applicable_scenes_json, status, sensitivity_class, "
                "promoted_from_candidate_id, content_hash, changed_at, changed_by, "
                "change_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version.version_id,
                    version.assertion_id,
                    version.owner_account_id,
                    version.version,
                    version.canonical_dimension,
                    version.value_or_rule,
                    json.dumps(version.applicable_scenes, ensure_ascii=False),
                    version.status.value,
                    version.sensitivity_class.value,
                    version.promoted_from_candidate_id,
                    version.content_hash,
                    _iso(version.changed_at),
                    version.changed_by,
                    version.change_reason,
                ),
            )
        return version

    def list_assertion_versions(
        self, owner_id: str, assertion_id: str
    ) -> list[ProfileAssertionVersion]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_assertion_versions "
            "WHERE account_id = ? AND assertion_id = ? ORDER BY version ASC",
            (owner_id, assertion_id),
        ).fetchall()
        return [self._assertion_version_from_row(row) for row in rows]

    @staticmethod
    def _assertion_version_from_row(row: sqlite3.Row) -> ProfileAssertionVersion:
        return ProfileAssertionVersion(
            version_id=str(row["version_id"]),
            assertion_id=str(row["assertion_id"]),
            owner_account_id=str(row["account_id"]),
            version=int(row["version"]),
            canonical_dimension=str(row["canonical_dimension"]),
            value_or_rule=str(row["value_or_rule"]),
            applicable_scenes=_parse_json_list(str(row["applicable_scenes_json"])),
            status=AssertionStatus(str(row["status"])),
            sensitivity_class=ProfileSensitivityClass(str(row["sensitivity_class"])),
            promoted_from_candidate_id=row["promoted_from_candidate_id"],
            content_hash=str(row["content_hash"]),
            changed_at=_parse_dt(str(row["changed_at"])) or datetime.now(UTC),
            changed_by=str(row["changed_by"]),
            change_reason=str(row["change_reason"]),
        )

    # ------------------------------------------------------------------
    # 切片
    # ------------------------------------------------------------------

    def save_slice(self, slice_: ProfileSlice) -> ProfileSlice:
        with self._db.transaction():
            existing = self._db.scoped(slice_.owner_account_id).execute(
                "SELECT 1 FROM profile_slices WHERE account_id = ? AND slice_id = ?",
                (slice_.owner_account_id, slice_.slice_id),
            ).fetchone()
            if existing is None:
                self._db.scoped(slice_.owner_account_id).execute(
                    "INSERT INTO profile_slices ("
                    "slice_id, account_id, run_id, purpose, project_id, "
                    "included_items_json, unused_items_json, "
                    "excluded_candidate_ids_json, exclusion_reasons_json, "
                    "rejected_items_json, authorization_snapshot, key_epoch, "
                    "expires_at, sensitivity_classes_allowed_json, "
                    "compiled_policy_version, status, invalidated_at, "
                    "invalidation_reason, compiled_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        slice_.slice_id,
                        slice_.owner_account_id,
                        slice_.run_id,
                        slice_.purpose,
                        slice_.project_id,
                        json.dumps(
                            [item.model_dump(mode="json") for item in slice_.included_items],
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            [item.model_dump(mode="json") for item in slice_.unused_items],
                            ensure_ascii=False,
                        ),
                        json.dumps(slice_.excluded_candidate_ids, ensure_ascii=False),
                        json.dumps(slice_.exclusion_reasons, ensure_ascii=False),
                        json.dumps(
                            [item.model_dump(mode="json") for item in slice_.rejected_items],
                            ensure_ascii=False,
                        ),
                        slice_.authorization_snapshot,
                        slice_.key_epoch,
                        _iso(slice_.expires_at) if slice_.expires_at else None,
                        json.dumps(
                            [c.value for c in slice_.sensitivity_classes_allowed],
                            ensure_ascii=False,
                        ),
                        slice_.compiled_policy_version,
                        slice_.status.value,
                        _iso(slice_.invalidated_at) if slice_.invalidated_at else None,
                        slice_.invalidation_reason,
                        _iso(slice_.compiled_at),
                    ),
                )
            else:
                self._db.scoped(slice_.owner_account_id).execute(
                    "UPDATE profile_slices SET purpose = ?, project_id = ?, "
                    "included_items_json = ?, unused_items_json = ?, "
                    "excluded_candidate_ids_json = ?, exclusion_reasons_json = ?, "
                    "rejected_items_json = ?, authorization_snapshot = ?, "
                    "key_epoch = ?, expires_at = ?, sensitivity_classes_allowed_json = ?, "
                    "compiled_policy_version = ?, status = ?, invalidated_at = ?, "
                    "invalidation_reason = ?, compiled_at = ? "
                    "WHERE account_id = ? AND slice_id = ?",
                    (
                        slice_.purpose,
                        slice_.project_id,
                        json.dumps(
                            [item.model_dump(mode="json") for item in slice_.included_items],
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            [item.model_dump(mode="json") for item in slice_.unused_items],
                            ensure_ascii=False,
                        ),
                        json.dumps(slice_.excluded_candidate_ids, ensure_ascii=False),
                        json.dumps(slice_.exclusion_reasons, ensure_ascii=False),
                        json.dumps(
                            [item.model_dump(mode="json") for item in slice_.rejected_items],
                            ensure_ascii=False,
                        ),
                        slice_.authorization_snapshot,
                        slice_.key_epoch,
                        _iso(slice_.expires_at) if slice_.expires_at else None,
                        json.dumps(
                            [c.value for c in slice_.sensitivity_classes_allowed],
                            ensure_ascii=False,
                        ),
                        slice_.compiled_policy_version,
                        slice_.status.value,
                        _iso(slice_.invalidated_at) if slice_.invalidated_at else None,
                        slice_.invalidation_reason,
                        _iso(slice_.compiled_at),
                        slice_.owner_account_id,
                        slice_.slice_id,
                    ),
                )
        return slice_

    def get_slice(self, owner_id: str, slice_id: str) -> ProfileSlice:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_slices WHERE account_id = ? AND slice_id = ?",
            (owner_id, slice_id),
        ).fetchone()
        if row is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return self._slice_from_row(row)

    def list_slices_for_run(self, owner_id: str, run_id: str) -> list[ProfileSlice]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_slices "
            "WHERE account_id = ? AND run_id = ? ORDER BY compiled_at DESC",
            (owner_id, run_id),
        ).fetchall()
        return [self._slice_from_row(row) for row in rows]

    def get_slice_by_id(self, slice_id: str) -> ProfileSlice:
        """Return a slice by identifier without owner check.

        端口设计例外（与 InMemory 一致）：模型/工作节点按 run_id 授权
        （服务层 :meth:`require_slice_for_run` 校验绑定），slice_id 为
        不可枚举随机标识；调用方负责 run 绑定与作用域校验。
        """
        row = self._db.connection.execute(
            "SELECT * FROM profile_slices WHERE slice_id = ?", (slice_id,)
        ).fetchone()
        if row is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return self._slice_from_row(row)

    def list_slices_containing_assertion(
        self, owner_id: str, assertion_id: str
    ) -> list[ProfileSlice]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_slices WHERE account_id = ? AND status = 'active'",
            (owner_id,),
        ).fetchall()
        return [
            slice_
            for slice_ in (self._slice_from_row(row) for row in rows)
            if any(item.assertion_id == assertion_id for item in slice_.included_items)
        ]

    @staticmethod
    def _slice_from_row(row: sqlite3.Row) -> ProfileSlice:
        included_raw = (
            json.loads(str(row["included_items_json"]))
            if row["included_items_json"]
            else []
        )
        unused_raw = (
            json.loads(str(row["unused_items_json"]))
            if row["unused_items_json"]
            else []
        )
        rejected_raw = (
            json.loads(str(row["rejected_items_json"]))
            if row["rejected_items_json"]
            else []
        )
        sensitivity_raw = (
            json.loads(str(row["sensitivity_classes_allowed_json"]))
            if row["sensitivity_classes_allowed_json"]
            else []
        )
        return ProfileSlice(
            slice_id=str(row["slice_id"]),
            owner_account_id=str(row["account_id"]),
            run_id=str(row["run_id"]),
            purpose=str(row["purpose"]),
            project_id=row["project_id"],
            included_items=[
                ProfileSliceItem.model_validate(item) for item in included_raw
            ],
            unused_items=[UnusedSliceItem.model_validate(item) for item in unused_raw],
            excluded_candidate_ids=_parse_json_list(
                str(row["excluded_candidate_ids_json"])
            ),
            exclusion_reasons=_parse_json_dict(str(row["exclusion_reasons_json"])),
            rejected_items=[
                RejectedSliceItem.model_validate(item) for item in rejected_raw
            ],
            authorization_snapshot=str(row["authorization_snapshot"]),
            key_epoch=str(row["key_epoch"]),
            expires_at=_parse_dt(row["expires_at"]),
            sensitivity_classes_allowed=[
                ProfileSensitivityClass(v) for v in sensitivity_raw
            ],
            compiled_policy_version=str(row["compiled_policy_version"]),
            status=SliceStatus(str(row["status"])),
            invalidated_at=_parse_dt(row["invalidated_at"]),
            invalidation_reason=row["invalidation_reason"],
            compiled_at=_parse_dt(str(row["compiled_at"])) or datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # 许可
    # ------------------------------------------------------------------

    def list_permissions(self, owner_id: str) -> list[ProfilePermission]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_permissions WHERE account_id = ? "
            "ORDER BY dimension, scene",
            (owner_id,),
        ).fetchall()
        return [self._permission_from_row(row) for row in rows]

    def set_permission(
        self,
        owner_id: str,
        dimension: str,
        scene: str,
        enabled: bool,
        updated_at: datetime,
    ) -> ProfilePermission:
        with self._db.transaction():
            self._db.scoped(owner_id).execute(
                "INSERT INTO profile_permissions "
                "(account_id, dimension, scene, enabled, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, dimension, scene) "
                "DO UPDATE SET enabled = excluded.enabled, "
                "updated_at = excluded.updated_at",
                (owner_id, dimension, scene, 1 if enabled else 0, _iso(updated_at)),
            )
        return ProfilePermission(
            account_id=owner_id,
            dimension=ProfileDimension(dimension),
            scene=scene,
            enabled=enabled,
            updated_at=updated_at,
        )

    @staticmethod
    def _permission_from_row(row: sqlite3.Row) -> ProfilePermission:
        return ProfilePermission(
            account_id=str(row["account_id"]),
            dimension=ProfileDimension(str(row["dimension"])),
            scene=str(row["scene"]),
            enabled=bool(int(row["enabled"])),
            updated_at=_parse_dt(str(row["updated_at"])) or datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------

    def save_notification(self, notification: ProfileNotification) -> ProfileNotification:
        with self._db.transaction():
            self._db.scoped(notification.owner_account_id).execute(
                "INSERT INTO profile_notifications ("
                "notification_id, account_id, kind, title, message, source_ref, "
                "source_text, dimension, scene, assertion_id, candidate_id, "
                "recallable, recalled_at, read_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(notification_id) DO UPDATE SET "
                "kind = excluded.kind, title = excluded.title, message = excluded.message, "
                "source_ref = excluded.source_ref, source_text = excluded.source_text, "
                "dimension = excluded.dimension, scene = excluded.scene, "
                "assertion_id = excluded.assertion_id, candidate_id = excluded.candidate_id, "
                "recallable = excluded.recallable, recalled_at = excluded.recalled_at, "
                "read_at = excluded.read_at, created_at = excluded.created_at",
                (
                    notification.notification_id,
                    notification.owner_account_id,
                    notification.kind.value,
                    notification.title,
                    notification.message,
                    notification.source_ref,
                    notification.source_text,
                    (
                        notification.dimension.value
                        if notification.dimension is not None
                        else None
                    ),
                    notification.scene,
                    notification.assertion_id,
                    notification.candidate_id,
                    1 if notification.recallable else 0,
                    _iso(notification.recalled_at) if notification.recalled_at else None,
                    _iso(notification.read_at) if notification.read_at else None,
                    _iso(notification.created_at),
                ),
            )
        return notification

    def get_notification(
        self, owner_id: str, notification_id: str
    ) -> ProfileNotification:
        row = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_notifications "
            "WHERE account_id = ? AND notification_id = ?",
            (owner_id, notification_id),
        ).fetchone()
        if row is None:
            raise ProfileError("对象不存在或没有访问权限。")
        return self._notification_from_row(row)

    def list_notifications(self, owner_id: str) -> list[ProfileNotification]:
        rows = self._db.scoped(owner_id).execute(
            "SELECT * FROM profile_notifications "
            "WHERE account_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
        return [self._notification_from_row(row) for row in rows]

    def mark_notification_read(
        self, owner_id: str, notification_id: str, read_at: datetime
    ) -> ProfileNotification:
        with self._db.transaction():
            self._db.scoped(owner_id).execute(
                "UPDATE profile_notifications SET read_at = ? "
                "WHERE account_id = ? AND notification_id = ?",
                (_iso(read_at), owner_id, notification_id),
            )
        notification = self.get_notification(owner_id, notification_id)
        notification.read_at = read_at
        return notification

    @staticmethod
    def _notification_from_row(row: sqlite3.Row) -> ProfileNotification:
        return ProfileNotification(
            notification_id=str(row["notification_id"]),
            owner_account_id=str(row["account_id"]),
            kind=ProfileNotificationKind(str(row["kind"])),
            title=str(row["title"]),
            message=str(row["message"]),
            source_ref=str(row["source_ref"]),
            source_text=str(row["source_text"]),
            dimension=(
                ProfileDimension(str(row["dimension"])) if row["dimension"] else None
            ),
            scene=row["scene"],
            assertion_id=row["assertion_id"],
            candidate_id=row["candidate_id"],
            recallable=bool(int(row["recallable"])),
            recalled_at=_parse_dt(row["recalled_at"]),
            read_at=_parse_dt(row["read_at"]),
            created_at=_parse_dt(str(row["created_at"])) or datetime.now(UTC),
        )
