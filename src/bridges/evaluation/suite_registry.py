"""评测套件注册表（Issue 40）。

统一登记版本化评测包：套件按 ``(suite_id, version)`` 登记，校验内容摘要
（digest）与版本顺序。任何固定内容变化都必须产生新版本与新摘要；同一
标识下摘要不一致视为静默覆盖并拒绝登记；旧版本仍可追溯（不删除）。

套件失效（数据召回、许可证撤销、模型合同变化、评分逻辑变化）通过
:meth:`invalidate` 记录失效状态，不通过覆盖历史版本消除。
"""

from __future__ import annotations

from bridges.contracts.evaluation_suite import (
    SuiteDefinition,
    SuiteInvalidationTrigger,
    SuiteStatus,
    now_iso,
)


class SuiteRegistryError(Exception):
    """套件注册表领域错误：消息可直接展示给调用方。"""


class SuiteRegistry:
    """内存注册表：校验、登记、查询、失效版本化评测包。"""

    def __init__(self) -> None:
        self._suites: dict[tuple[str, str], SuiteDefinition] = {}

    def register(self, suite: SuiteDefinition) -> str:
        """登记一个套件；校验摘要一致性、引用完整性与版本顺序。

        返回套件摘要（digest）。
        """
        self._validate_references(suite)
        existing = self._suites.get((suite.suite_id, suite.version))
        if existing is not None:
            if existing.digest() != suite.digest():
                raise SuiteRegistryError(
                    f"套件 {suite.suite_id}@{suite.version} 内容摘要不一致，"
                    "拒绝静默覆盖：请发布新版本。"
                )
            return existing.digest()
        self._suites[(suite.suite_id, suite.version)] = suite
        return suite.digest()

    def _validate_references(self, suite: SuiteDefinition) -> None:
        """校验套件内部引用：数据集、量表、Schema、任务与许可证。"""
        license_ids = {record.asset_id for record in suite.licenses}
        dataset_ids = {entry.dataset_id for entry in suite.manifest}
        scale_ids = {scale.scale_id for scale in suite.scales}
        schema_ids = {schema.schema_id for schema in suite.artifact_schemas}
        task_ids = {task.task_id for task in suite.tasks}
        matrix_tasks = {entry.task_id for entry in suite.run_matrix.entries}

        for manifest_entry in suite.manifest:
            if manifest_entry.license_ref not in license_ids:
                raise SuiteRegistryError(
                    f"数据集 {manifest_entry.dataset_id} 引用的许可证记录不存在："
                    f"{manifest_entry.license_ref}"
                )
        for card in suite.data_cards:
            if card.dataset_id not in dataset_ids:
                raise SuiteRegistryError(
                    f"数据卡引用的数据集不存在：{card.dataset_id}"
                )
            if card.license_ref not in license_ids:
                raise SuiteRegistryError(
                    f"数据卡 {card.dataset_id} 引用的许可证记录不存在：{card.license_ref}"
                )
        for task in suite.tasks:
            if task.scale_id not in scale_ids:
                raise SuiteRegistryError(
                    f"任务 {task.task_id} 引用的评分量表不存在：{task.scale_id}"
                )
            if task.expected_artifact_schema_id not in schema_ids:
                raise SuiteRegistryError(
                    f"任务 {task.task_id} 引用的产物 Schema 不存在："
                    f"{task.expected_artifact_schema_id}"
                )
            for ref in task.dataset_refs:
                if ref not in dataset_ids:
                    raise SuiteRegistryError(
                        f"任务 {task.task_id} 引用的数据集不存在：{ref}"
                    )
        for matrix_entry in suite.run_matrix.entries:
            if matrix_entry.task_id not in task_ids:
                raise SuiteRegistryError(
                    f"运行矩阵引用的任务不存在：{matrix_entry.task_id}"
                )
        for missing in task_ids - matrix_tasks:
            raise SuiteRegistryError(
                f"任务 {missing} 未出现在运行矩阵中，套件不完整。"
            )

    def get(self, suite_id: str, version: str | None = None) -> SuiteDefinition:
        """按标识取套件；未指定版本时取已登记的最高版本。"""
        if version is None:
            candidates = [
                (ver, candidate)
                for (sid, ver), candidate in self._suites.items()
                if sid == suite_id
            ]
            if not candidates:
                raise SuiteRegistryError(f"评测套件不存在：{suite_id}")
            _, latest = max(candidates, key=lambda pair: self._version_key(pair[0]))
            return latest
        found = self._suites.get((suite_id, version))
        if found is None:
            raise SuiteRegistryError(f"评测套件不存在：{suite_id}@{version}")
        return found

    @staticmethod
    def _version_key(version: str) -> tuple[int, ...]:
        """语义版本排序键（数值段 + 剩余字符串）。"""
        parts: list[int] = []
        for segment in version.split("."):
            if segment.isdigit():
                parts.append(int(segment))
            else:
                break
        return tuple(parts)

    def list_suites(self, suite_id: str | None = None) -> list[SuiteDefinition]:
        """列出全部已登记套件（按套件标识与版本排序）。"""
        suites = [
            suite
            for (sid, _), suite in self._suites.items()
            if suite_id is None or sid == suite_id
        ]
        suites.sort(key=lambda suite: (suite.suite_id, self._version_key(suite.version)))
        return suites

    def invalidate(
        self,
        suite_id: str,
        version: str,
        trigger: SuiteInvalidationTrigger,
        reason: str,
    ) -> SuiteDefinition:
        """使一个套件失效：记录触发源与原因，不删除历史版本。"""
        suite = self.get(suite_id, version)
        if suite.status != SuiteStatus.ACTIVE:
            raise SuiteRegistryError(
                f"套件 {suite_id}@{version} 已处于 {suite.status.value}，无需重复失效。"
            )
        invalidated = suite.model_copy(
            update={
                "status": SuiteStatus.INVALIDATED,
                "invalidated_at": now_iso(),
                "invalidation_trigger": trigger,
                "invalidation_reason": reason,
            },
            deep=True,
        )
        self._suites[(suite_id, version)] = invalidated
        return invalidated


__all__ = ["SuiteRegistry", "SuiteRegistryError"]
