"""构造仅含论文改动的验收副本；不覆盖混合工作区或自动提交。"""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / ".scratch" / "paper-search-redesign" / "acceptance-final"
BASE = "65e5b63720a05520f7721a4e81b2959f0432edb2"


def baseline(path: str) -> str:
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT).decode("utf-8")


def write(path: str, content: str) -> None:
    target = SNAPSHOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def update_frontend_fixtures() -> None:
    for path in (
        "apps/web/src/components/bridges/chat/CareerPlanCard.test.tsx",
        "apps/web/src/components/bridges/chat/LearningResourcesCard.test.tsx",
        "apps/web/src/lib/chat-modules.test.ts",
    ):
        write(path, (ROOT / path).read_text(encoding="utf-8"))
    path = "apps/web/src/components/bridges/MessageList.test.tsx"
    content = baseline(path).replace(
        '  context_label: "机器学习中的 Transformer 结构",',
        '  context_label: "机器学习中的 Transformer 结构",\n'
        '  research_question: "Transformer 的研究问题",')
    content = content.replace('  requested_videos: 3,',
        '  requested_videos: 3,\n  needs_practice_project: false,\n'
        '  path_verified: false,\n  parallel_limit: 2,')
    write(path, content)
    path = "apps/web/src/components/bridges/chat/TiebaResearchCard.test.tsx"
    content = baseline(path).replace('    place_or_event: [],',
        '    place_or_event: [],\n    question_kind: "experience",\n    parallel_evidence: false,')
    write(path, content)


if __name__ == "__main__":
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    archive_path = ROOT / ".tmp" / "paper-acceptance-baseline.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--output={archive_path}", BASE],
                   cwd=ROOT, check=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(SNAPSHOT)
    paths = [
        *(path.relative_to(ROOT).as_posix() for path in (ROOT / "src/bridges/paper").glob("*.py")),
        *(path.relative_to(ROOT).as_posix() for path in (ROOT / "tests/paper").glob("*.py")),
        "src/bridges/ai/payload_budget.py", "src/bridges/contracts/observability.py",
        "apps/web/src/components/bridges/chat/PaperSearchCard.tsx",
        "apps/web/src/components/bridges/chat/PaperSearchCard.test.tsx",
        "apps/web/src/components/bridges/chat/PaperCitationGraph.tsx",
        "apps/web/src/components/bridges/chat/PaperCitationGraph.test.tsx",
        "apps/web/src/components/bridges/chat/CareerPlanCard.test.tsx",
        "apps/web/src/components/bridges/chat/LearningResourcesCard.test.tsx",
        "apps/web/src/lib/chat-modules.test.ts",
        "docs/adr/0039-research-question-driven-paper-discovery.md",
        "docs/v2/workflows.md", "docs/workflow/daily-workflows.md",
        "tests/orchestration/issue37_chains.py",
    ]
    for path in paths:
        write(path, (ROOT / path).read_text(encoding="utf-8"))
    # 共享装配文件只取论文区段，保留main的贴吧装配。
    path = "src/bridges/api/main.py"
    before, current = baseline(path), (ROOT / path).read_text(encoding="utf-8")
    before = before.replace("import httpx\n", "import httpx\n\n"
        "from bridges.paper.discovery import CrossrefPaperSource, OpenAlexPaperSource\n"
        "from bridges.paper.intent import PaperResearchIntentAnalyzer\n")
    start = "        # 改进工单 10：论文内核的提交守卫"
    end = '        app.router.add_event_handler("shutdown", app.state.paper_search_service.close)'
    left, right = before.index(start), before.index(end) + len(end)
    work_left = current.index("        paper_sources: dict[str, Any]")
    work_right = current.index(end) + len(end)
    write(path, before[:left] + current[work_left:work_right] + before[right:])
    path = "apps/web/src/lib/api.ts"
    content = baseline(path)
    anchor = 'export type PaperRecommendation = components["schemas"]["PaperRecommendation"];'
    lines = [f'export type {name} = components["schemas"]["{name}"];' for name in
             ("PaperRequirementEvidence", "PaperConditionSource", "PaperCandidateProjection", "PaperCitationEdge")]
    write(path, content.replace(anchor, anchor + "\n" + "\n".join(lines)))
    update_frontend_fixtures()
    # OpenAPI只取论文及此次新增枚举，不夹带未验收的贴吧路由/模型。
    spec = json.loads(baseline("openapi.json"))
    current_spec = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    selected = []
    for name, schema in current_spec["components"]["schemas"].items():
        if name.startswith("Paper") or name == "AuditAction":
            spec["components"]["schemas"][name] = schema
            selected.append(name)
    write("openapi.json", json.dumps(spec, ensure_ascii=False, indent=2))
    (SNAPSHOT / ".tmp").mkdir(exist_ok=True)
    print("论文独立副本：", SNAPSHOT)
    print("路径：", len(spec["paths"]), "，论文schema：", len(selected))
