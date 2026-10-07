"""详情优先消耗读取预算，已知列表页仅保留为入口链接。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def job_url_priority(url: str) -> int:
    """0=已知详情，1=待核实结构，2=已知列表／首页。"""
    parsed = urlsplit(url)
    path = parsed.path.lower().rstrip("/")
    if not path:
        return 2
    if parsed.hostname and parsed.hostname.endswith("liepin.com") and path.startswith("/s/"):
        return 2
    if (
        parsed.hostname and parsed.hostname.endswith("wondercv.com")
        and path.startswith("/xiaozhao/")
    ):
        return 2
    if (
        path.startswith(("/comp/", "/feed/", "/discuss/", "/bk_jobs/"))
        or path.endswith("/job/list")
    ):
        return 2
    if re.search(r"/(?:job_detail|detail|intern)/|/job[-/]\d|/jobs?/\d", path):
        return 0
    if parsed.hostname and parsed.hostname.endswith("zhipin.com") and path.startswith("/zhaopin"):
        return 2
    if re.search(r"/(?:hot-jobs|zhaopin|jobs?)$|/(?:major|zhaopin)/", path):
        return 2
    return 1
