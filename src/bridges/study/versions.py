"""学习图执行版本与只读历史可解释版本。"""

STUDY_GRAPH_VERSION = "study-tutoring-review-v6"
STUDY_TUTOR_GRAPH_VERSION = "study-local-tutor-v1"

# 历史投影只按明确存在过的学习图版本关联状态；未知版本不套用当前学习状态。
KNOWN_STUDY_GRAPH_VERSIONS = (
    STUDY_GRAPH_VERSION,
    STUDY_TUTOR_GRAPH_VERSION,
    "study-pages-v1",
    "study-scope-v2",
    "study-tutoring-v3",
    "study-tutoring-review-v4",
    "study-tutoring-review-v5",
)
KNOWN_STUDY_GRAPH_VERSION_SET = frozenset(KNOWN_STUDY_GRAPH_VERSIONS)

#: 改进工单 12：旧五阶段图（含无版本记录的更早运行）已退出执行，只保留
#: 历史读取；这些版本不得再启动、重试或恢复。当前导师版本不在其中。
LEGACY_STUDY_GRAPH_VERSION_SET = frozenset(
    version
    for version in KNOWN_STUDY_GRAPH_VERSIONS
    if version != STUDY_TUTOR_GRAPH_VERSION
)


def is_legacy_study_graph_version(version: str | None) -> bool:
    """旧五阶段执行版本（含无版本记录的旧运行）判定。

    ``None`` 表示运行记录早于图版本字段，无法证明是新导师路径；学习模式
    下按旧流程安全收尾，不冒险套用新节点。
    """
    return version is None or version in LEGACY_STUDY_GRAPH_VERSION_SET
