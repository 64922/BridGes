"""真实探针判据反例：不允许无关事实、重复事实和错误主体通过。"""

import runpy
from pathlib import Path
from types import SimpleNamespace

PROBES = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/run_issue41_profile_evaluation.py")
)


def item(text):
    return SimpleNamespace(fact_text=text, normalized_value=text)


def test_extraction_probes_reject_unrelated_and_duplicate_facts():
    assert not PROBES["_check_negated"]("我不喜欢长篇回答", [item("不学习摄影")])[0]
    assert not PROBES["_check_multi_fact"](
        "我喜欢跑步，也喜欢游泳", [item("喜欢跑步"), item("喜欢跑步")]
    )[0]
    assert not PROBES["_check_third_party"]("我朋友很喜欢摄影", [item("我朋友喜欢摄影")])[0]
    assert not PROBES["_check_self_report"]("我正在学习概率统计", [item("概率")])[0]
