"""学习图执行版本与只读历史可解释版本。"""

STUDY_GRAPH_VERSION = "study-tutoring-review-v6"

# 历史投影只按明确存在过的学习图版本关联状态；未知版本不套用当前学习状态。
KNOWN_STUDY_GRAPH_VERSIONS = (
    STUDY_GRAPH_VERSION,
    "study-pages-v1",
    "study-scope-v2",
    "study-tutoring-v3",
    "study-tutoring-review-v4",
    "study-tutoring-review-v5",
)
KNOWN_STUDY_GRAPH_VERSION_SET = frozenset(KNOWN_STUDY_GRAPH_VERSIONS)
