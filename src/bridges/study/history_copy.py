"""学习历史只读状态的固定中文说明。"""

from bridges.state_copy.types import CopyCategory, CopyEntry, CopyStrategy

STUDY_HISTORY_COPY_ENTRIES = (
    CopyEntry(
        "study.history.legacy",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.FIXED_TEMPLATE,
        ("legacy",),
        text="此学习记录采用旧格式（版本 {version}）；已按原记录只读展示。",
        note="旧状态的读取升级只在内存中完成，不写回学习状态。",
    ),
    CopyEntry(
        "study.history.unsupported",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.FIXED_TEMPLATE,
        ("unsupported",),
        text=(
            "此学习记录采用当前不支持的格式（版本 {version}）；"
            "消息历史仍可查看，学习阶段暂无法解读。"
        ),
        note="未知学习状态版本不套用当前学习流程或当前投影。",
    ),
    CopyEntry(
        "study.history.unreadable",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.FIXED_TEMPLATE,
        ("unreadable",),
        text="学习状态记录暂时无法解读；原始消息历史仍保留。",
        note="损坏或结构不兼容的状态不阻断消息历史读取。",
    ),
)
