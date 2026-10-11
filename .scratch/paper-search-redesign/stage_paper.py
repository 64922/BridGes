"""仅暂存已验收论文副本，不覆盖共享工作区中的其他任务内容。"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "65e5b63720a05520f7721a4e81b2959f0432edb2"
SNAPSHOT = ROOT / ".scratch/paper-search-redesign/acceptance-final"


def git(*args: str, **kwargs: object) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT, **kwargs)


if __name__ == "__main__":
    if git("rev-parse", "HEAD").decode().strip() != BASE:
        raise SystemExit("HEAD已改变，请重新核对基准；未暂存。")
    if git("diff", "--cached", "--name-only").strip():
        raise SystemExit("暂存区已有内容，请先核对归属；未覆盖。")
    paths = [
        *(path.relative_to(SNAPSHOT).as_posix() for path in (SNAPSHOT / "src/bridges/paper").glob("*.py")),
        *(path.relative_to(SNAPSHOT).as_posix() for path in (SNAPSHOT / "tests/paper").glob("*.py")),
        "src/bridges/ai/payload_budget.py", "src/bridges/contracts/observability.py",
        "src/bridges/api/main.py", "openapi.json", "packages/contracts/src/generated.ts",
        "apps/web/src/lib/api.ts", "apps/web/src/lib/chat-modules.test.ts",
        "apps/web/src/components/bridges/MessageList.test.tsx",
        "apps/web/src/components/bridges/chat/PaperSearchCard.tsx",
        "apps/web/src/components/bridges/chat/PaperSearchCard.test.tsx",
        "apps/web/src/components/bridges/chat/PaperCitationGraph.tsx",
        "apps/web/src/components/bridges/chat/PaperCitationGraph.test.tsx",
        "apps/web/src/components/bridges/chat/CareerPlanCard.test.tsx",
        "apps/web/src/components/bridges/chat/LearningResourcesCard.test.tsx",
        "apps/web/src/components/bridges/chat/TiebaResearchCard.test.tsx",
        "tests/orchestration/issue37_chains.py",
        "docs/adr/0039-research-question-driven-paper-discovery.md",
        "docs/v2/workflows.md", "docs/workflow/daily-workflows.md",
    ]
    for path in paths:
        sha = git("hash-object", "-w", f"--path={path}", str(SNAPSHOT / path)).decode().strip()
        git("update-index", "--add", "--cacheinfo", "100644", sha, path)
    records = ["PRD.md", "HANDOFF.md", "STANDARDS-REVIEW.md", "SPEC-REVIEW.md",
               "ACCEPTANCE.md", "CHECK-COMPARISON.json", "probe_sources.py",
               "prepare_snapshot.py", "summarize_checks.py", "stage_paper.py",
               "acceptance-logs/sources-live.json"]
    git("add", "--", *(f".scratch/paper-search-redesign/{name}" for name in records))
    print(git("diff", "--cached", "--stat").decode())
