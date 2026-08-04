"""分层本地检索、融合排序与引用（Issue 20）。

对外暴露 :class:`LayeredRetrievalService`（每轮检索编排、轮次/引用投影
与证据详情授权校验）与检索领域错误。
"""

from bridges.retrieval.service import LayeredRetrievalService, RetrievalError

__all__ = ["LayeredRetrievalService", "RetrievalError"]
