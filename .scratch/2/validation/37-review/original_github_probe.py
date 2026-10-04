"""使用原交付提交的适配器复现真实 GitHub 合同不兼容。"""

import subprocess
import sys
import types

from tests.orchestration import test_issue37_review_regressions as regression

source = subprocess.run(
    ["git", "show", "8c1785a2:src/bridges/orchestration/production.py"],
    check=True, capture_output=True, encoding="utf-8",
).stdout
original = types.ModuleType("review_original_production")
sys.modules[original.__name__] = original
exec(compile(source, "production@8c1785a2", "exec"), original.__dict__)
regression.ModuleServiceStepRunner = original.ModuleServiceStepRunner
try:
    regression.test_real_github_delivery_is_accepted()
except AssertionError:
    print("已复现：原提交适配器拒绝真实 GithubDelivery。")
else:
    raise AssertionError("未能复现原提交缺陷。")
