"""帖子属于目标贴吧之外，还须由已读内容证明与本轮主题相关。"""

from bridges.tieba.contracts import TiebaQuestionAnalysis, TiebaReadResult
from bridges.tieba.lexicon import relaxed_topic_terms


def post_matches_topic(result: TiebaReadResult, analysis: TiebaQuestionAnalysis) -> bool:
    text = "\n".join([result.title or "", *(reply.content for reply in result.replies)])
    terms = relaxed_topic_terms(analysis.topic_terms)
    for term in terms:
        variants = ("国庆节", "国庆", "十一") if term in {"国庆节", "国庆"} else (term,)
        if any(variant in text for variant in variants):
            return True
    return False
