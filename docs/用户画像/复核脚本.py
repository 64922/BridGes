"""以合成数据复核画像审查结论；不连接数据库、不调用模型、不修改业务代码。

在仓库根目录执行：conda run -n agent python docs/用户画像/复核脚本.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bridges.chat.lightweight_policy import ChatLightweightPolicyCompiler
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.profiles.adapters import InMemoryProfileRepository
from bridges.profiles.atomic import AtomicProfileService, InMemoryAtomicProfileRepository
from bridges.profiles.automatic import AutomaticProfileService, InMemoryAutomaticProfileRepository
from bridges.profiles.four_dimensions import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.profiles.signals import ProfileSignalClassifier


def services():
    """每个场景使用独立内存仓库。"""
    four = FourDimensionProfileService(
        InMemoryProfileRepository(), InMemoryFourDimensionProfileRepository()
    )
    repository = InMemoryAtomicProfileRepository()
    atomic = AtomicProfileService(four, repository)
    automatic = AutomaticProfileService(
        four_dimension_service=four,
        repository=InMemoryAutomaticProfileRepository(),
        atomic_profile_service=atomic,
    )
    return four, repository, atomic, automatic


def ingest(automatic, text, number):
    return automatic.preprocess_message(
        "audit-user", conversation_id="audit-chat", message_id=f"m-{number}",
        content=text, run_id=f"r-{number}", mode="daily",
    )


def texts(atomic):
    return [item.text for item in atomic.list_items("audit-user")]


def recalled(atomic, question):
    return [item.value_or_rule for item in atomic.compile_chat_slice(
        "audit-user", run_id="audit-answer", current_question=question
    ).included_items]


results = {}
classifier = ProfileSignalClassifier()
classification_inputs = [
    "我不喜欢长篇回答", "请以后先给结论，再给解释",
    "我喜欢跑步，我的朋友喜欢摄影", "我喜欢跑步，但今天有点焦虑",
    "我对这个专业的研究很感兴趣",
]
results["消息分类"] = [
    {"原文": text, "分类": classifier.classify(text).category.value,
     "原因": classifier.classify(text).reason_code}
    for text in classification_inputs
]

_, _, atomic, automatic = services()
for number, text in enumerate(["我喜欢Python", "我正在学习Python"], 1):
    ingest(automatic, text, number)
results["关系丢失与合并"] = texts(atomic)

_, _, atomic, automatic = services()
ingest(automatic, "我是大二学生", 1)
ingest(automatic, "我是软件工程专业", 2)
results["不同学业属性覆盖"] = texts(atomic)

_, _, atomic, automatic = services()
ingest(automatic, "我计划考研", 1)
ingest(automatic, "我计划通过英语六级", 2)
results["并行目标覆盖"] = texts(atomic)

_, _, atomic, automatic = services()
ingest(automatic, "我喜欢跑步，也喜欢游泳", 1)
results["同类信息多项提取"] = texts(atomic)

_, _, atomic, _ = services()
atomic.remember("audit-user", "我是计算机专业的大二学生")
atomic.remember("audit-user", "我喜欢简短回答")
results["跨词面任务召回"] = recalled(atomic, "解释贝叶斯定理")

_, _, atomic, _ = services()
atomic.remember("audit-user", "我喜欢跑步")
atomic.remember("audit-user", "我爱慢跑")
results["近义去重"] = texts(atomic)

four, _, atomic, automatic = services()
ingest(automatic, "我喜欢跑步", 1)
item = atomic.list_items("audit-user")[0]
atomic.delete_item("audit-user", item.profile_item_id, item.version)
ingest(automatic, "我喜欢慢跑", 2)
results["删除后近义新表述"] = texts(atomic)

four, _, atomic, _ = services()
record = four.upsert_automatic_record(
    "audit-user", dimension=FourDimension.KNOWLEDGE_INTEREST,
    content="概率统计", action="create", confidence=FourDimensionConfidence.LOW,
    evidence_message_id="low-evidence",
)
atomic.mirror_record("audit-user", record)
results["低把握度原子召回"] = recalled(atomic, "概率统计")

_, repository, atomic, _ = services()
item = atomic.remember("audit-user", "我计划下周通过英语六级考试")
old_time = datetime.now(UTC) - timedelta(days=365)
repository.save_item(item.model_copy(update={"created_at": old_time, "updated_at": old_time}))
results["一年后相对时间召回"] = recalled(atomic, "英语六级考试")

_, _, atomic, _ = services()
prefix = "学习讲解时先给一个完整直观的例子并说明具体条件。" * 4
item = atomic.remember("audit-user", prefix + "但考试冲刺时不要使用这种方式。")
slice_ = atomic.compile_chat_slice("audit-user", run_id="truncation", current_question="学习讲解")
results["机械截断"] = {
    "完整长度": len(item.text), "切片": [entry.value_or_rule for entry in slice_.included_items],
}

_, _, atomic, _ = services()
atomic.remember("audit-user", "我喜欢简短回答")
slice_ = atomic.compile_chat_slice("audit-user", run_id="style", current_question="简短回答")
snapshot = ChatLightweightPolicyCompiler().compile("daily", profile_items=slice_.included_items)
results["表达策略接线"] = {
    "切片条数": len(slice_.included_items),
    "策略画像值": list(snapshot.profile_items),
}

_, _, atomic, automatic = services()
ingest(automatic, "记住我喜欢跑步", 1)
ingest(automatic, "不要记录", 2)
result = ingest(automatic, "忘掉跑步", 3)
results["停止记录后忘掉"] = {
    "指令结果": result.memory.model_dump(mode="json") if result.memory else None,
    "剩余条目": texts(atomic),
}

print(json.dumps(results, ensure_ascii=False, indent=2))
