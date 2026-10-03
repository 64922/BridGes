"""工单 25 独立验收：拒绝标题假阳性与超额主线。"""

import pytest

from bridges.resources.contracts import ResourceRole
from tests.resources.test_resources_module_core import _book, _evidence, _pipeline, _video


def test_unrelated_intro_does_not_verify_beginner_title() -> None:
    """标题入门和无关简介不能证明主题覆盖或先修。"""
    url = "https://openlibrary.org/works/OL1W"
    _, _, _, outcome = _pipeline(
        "零基础，快速了解机器学习",
        [_book("机器学习入门", url=url)],
        [_video("BV1", "机器学习入门", description="欢迎点赞关注")],
        evidence={url: _evidence(url, description="作者喜爱烹饪，感谢读者支持")},
    )
    assert not outcome.path_verified
    assert all(item.role is ResourceRole.SUPPLEMENT for item in outcome.items)


def test_systematic_main_selection_respects_one_of_each() -> None:
    """额外主线不能突破每种媒介的明确数量。"""
    urls = ["https://openlibrary.org/works/OL1W", "https://openlibrary.org/works/OL2W"]
    _, _, _, outcome = _pipeline(
        "有点基础，系统学习机器学习，要1本书和1个视频",
        [_book("机器学习基础", url=urls[0]), _book("机器学习进阶", url=urls[1])],
        [_video("BV1", "机器学习基础", description="机器学习基础知识")],
        evidence={url: _evidence(url, description="机器学习基础教材") for url in urls},
    )
    assert sum(item.kind.value == "book" for item in outcome.items) == 1
    assert sum(item.kind.value == "video" for item in outcome.items) == 1


@pytest.mark.parametrize("description", [
    "机器学习不适合入门，要求先掌握概率论",
    "机器学习入门教材，需要线性代数基础",
    "machine learning introduction requires prior knowledge of calculus",
])
def test_explicit_unconfirmed_prerequisites_stay_candidates(description: str) -> None:
    """否定适用性或明确先修要求不能被入门关键词覆盖。"""
    _, _, _, outcome = _pipeline(
        "零基础，快速了解机器学习", [],
        [_video("BV1", "机器学习入门", description=description)],
    )
    assert not outcome.path_verified
    assert all(item.role is ResourceRole.SUPPLEMENT for item in outcome.items)
