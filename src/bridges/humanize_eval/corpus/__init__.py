"""分层语料包（Issue 09）。

chat-naturalness（45 条）与 article-humanization（48 条）两个语料面
分别建集、分别报告；案例为不可变定义，内容变化必须升版本。holdout
分区由 ``bridges.humanize_eval.holdout`` 冻结与审计。
"""

from bridges.humanize_eval.corpus.article_cases import ARTICLE_CASE_DEFS
from bridges.humanize_eval.corpus.chat_cases import CHAT_CASE_DEFS

__all__ = ["ARTICLE_CASE_DEFS", "CHAT_CASE_DEFS"]
