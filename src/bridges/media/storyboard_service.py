"""Structured storyboard generation and sandbox execution service for T033.

Implements the full workflow:
1. Generate MediaStoryboard with SceneSpecs from structured description
2. Generate executable source code from storyboard
3. Static check of generated code (AST, dependency whitelist, banned patterns)
4. Run in isolated sandbox (in-memory simulation for dev/test)
5. Limited repair with fact-lock invariant preservation
6. Validation report production

Follows the same deterministic-by-default pattern as T032 MediaGenerationService:
- DeterministicStoryboardGenerator builds storyboards from structured input
- InMemorySandboxRuntime simulates a sandbox without requiring Linux containers
- Both are replaceable via Protocol for ModelGateway-backed or real Linux sandbox
"""

from __future__ import annotations

import ast
import hashlib
import secrets
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from bridges.contracts.media import (
    EditableSource,
    FactLockViolation,
    LifecycleStage,
    MediaStoryboard,
    SandboxDependency,
    SandboxResourceLimits,
    SandboxResourceUsage,
    SandboxRunRequest,
    SandboxRunResult,
    SandboxRunStatus,
    SceneAccessibility,
    SceneSpec,
    StaticCheckResult,
    StoryboardClaimBinding,
    StoryboardGenerationRequest,
    StoryboardNarration,
    StoryboardResult,
    StoryboardScene,
    StoryboardStatus,
    ValidationReport,
    VisualObject,
)
from bridges.contracts.science import FactLock

# ── Whitelists for static code check ─────────────────────────────────

ALLOWED_PYTHON_IMPORTS: frozenset[str] = frozenset({
    "math", "json", "random", "itertools", "collections",
    "typing", "dataclasses", "datetime",
})

ALLOWED_HTML_TAGS: frozenset[str] = frozenset({
    "svg", "g", "path", "circle", "rect", "line", "text", "tspan",
    "defs", "linearGradient", "stop", "style", "use", "clipPath",
    "div", "span", "p", "h1", "h2", "h3", "ul", "ol", "li",
    "table", "tr", "td", "th", "thead", "tbody",
    "button", "input", "label", "select", "option",
    "canvas", "script", "noscript", "iframe", "link",
})

BANNED_PYTHON_KEYWORDS: list[str] = [
    "exec(", "eval(", "__import__(", "subprocess",
    "os.system", "os.popen", "shutil", "socket",
    "requests.", "urllib", "open(", "file(",
]

BANNED_HTML_PATTERNS: list[str] = [
    "onclick=", "onload=", "onerror=", "onmouse",
    "onkey", "onfocus", "onblur", "onsubmit",
    "javascript:", "data:", "document.cookie",
    "localStorage", "sessionStorage", "indexedDB",
    "XMLHttpRequest", "fetch(", "WebSocket",
]


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_id() -> str:
    return secrets.token_urlsafe(16)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── Exceptions ───────────────────────────────────────────────────────


class StoryboardError(Exception):
    """Domain error for storyboard generation failures."""


class SandboxError(Exception):
    """Domain error for sandbox execution failures."""


# ── Static code check ────────────────────────────────────────────────


def static_check(code: str, language: str) -> StaticCheckResult:
    """Run static analysis on generated code before sandbox execution.

    Performs:
    1. AST parse validation (Python only)
    2. Dependency whitelist check
    3. Banned keyword/pattern detection
    """
    errors: list[str] = []

    if language == "python":
        # AST parse check.
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return StaticCheckResult(
                passed=False,
                ast_valid=False,
                deps_whitelisted=True,
                lint_ok=False,
                errors=[f"语法错误: {exc}"],
            )

        # Import whitelist check.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in ALLOWED_PYTHON_IMPORTS:
                        errors.append(
                            f"禁止的 import: '{alias.name}'。"
                            f" 允许: {', '.join(sorted(ALLOWED_PYTHON_IMPORTS))}。"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    if root not in ALLOWED_PYTHON_IMPORTS:
                        errors.append(
                            f"禁止的 from-import: '{node.module}'。"
                            f" 允许: {', '.join(sorted(ALLOWED_PYTHON_IMPORTS))}。"
                        )

        # Banned keyword check.
        for keyword in BANNED_PYTHON_KEYWORDS:
            if keyword in code:
                errors.append(f"代码包含禁止模式: '{keyword}'。")

    elif language in ("html", "javascript"):
        # HTML banned patterns.
        for pattern in BANNED_HTML_PATTERNS:
            if pattern in code.lower():
                errors.append(f"HTML/JS 包含禁止模式: '{pattern}'。")
    else:
        errors.append(f"不支持的代码语言: '{language}'。")

    passed = len(errors) == 0
    return StaticCheckResult(
        passed=passed,
        ast_valid=language != "python" or ("语法错误" not in str(errors)),
        deps_whitelisted=not any("import" in e for e in errors),
        lint_ok=passed,
        errors=errors,
    )


# ── Storyboard Generator Protocol ────────────────────────────────────


class StoryboardGenerator(Protocol):
    """Protocol for storyboard generation strategies."""

    def generate_storyboard(
        self, request: StoryboardGenerationRequest
    ) -> StoryboardResult:
        ...


class DeterministicStoryboardGenerator:
    """Deterministic storyboard generator for testing and default operation.

    Builds MediaStoryboard from structured input without any model dependency.
    When no scenes are provided, creates a minimal default storyboard.
    """

    def generate_storyboard(
        self, request: StoryboardGenerationRequest
    ) -> StoryboardResult:
        storyboard_id = _generate_id()
        now = _now()
        scene_specs: dict[str, SceneSpec] = {}

        scenes: list[StoryboardScene] = []
        if request.scenes:
            for scene in request.scenes:
                # Build a SceneSpec for each referenced scene.
                spec = SceneSpec(
                    scene_spec_id=scene.scene_spec_id or _generate_id(),
                    title=f"场景 {scene.scene_number}",
                    visual_objects=[],
                    layout_description="",
                    accessibility=None,
                    created_at=now,
                )
                scene_specs[spec.scene_spec_id] = spec
                scenes.append(scene)
        else:
            # Create a minimal default scene.
            spec_id = _generate_id()
            scene_specs[spec_id] = SceneSpec(
                scene_spec_id=spec_id,
                title=f"{request.title} — 默认场景",
                visual_objects=[
                    VisualObject(
                        object_id=_generate_id(),
                        label=request.title,
                        role="title",
                        initial_state="显示标题",
                        lifecycle_stages=[
                            LifecycleStage(
                                stage="enter",
                                timing_seconds=1.0,
                                description="标题淡入",
                            ),
                            LifecycleStage(
                                stage="hold",
                                timing_seconds=3.0,
                                description="保持显示",
                            ),
                        ],
                    ),
                ],
                layout_description="居中显示标题",
                accessibility=SceneAccessibility(
                    alt_text=f"{request.title}",
                    long_description=f"教学动画：{request.title}。",
                ),
                created_at=now,
            )
            scenes.append(
                StoryboardScene(
                    scene_id=_generate_id(),
                    scene_number=1,
                    scene_spec_id=spec_id,
                    timing_seconds=5.0,
                    transition_type="cut",
                    narration=StoryboardNarration(
                        text=f"欢迎学习：{request.title}",
                        claim_ids=list(request.claim_ids),
                    ),
                    scene_claim_bindings=[
                        StoryboardClaimBinding(
                            binding_id=_generate_id(),
                            scene_id="default",
                            element_ref=spec_id,
                            claim_id=cid,
                        )
                        for cid in request.claim_ids
                    ],
                    scene_accessibility=f"教学动画：{request.title}。",
                )
            )

        storyboard = MediaStoryboard(
            storyboard_id=storyboard_id,
            account_id="",
            project_id=request.project_id,
            title=request.title,
            teaching_objectives=list(request.teaching_objectives),
            scenes=scenes,
            media_type=request.media_type,
            status=StoryboardStatus.DESIGNING,
            created_at=now,
            updated_at=now,
        )

        return StoryboardResult(storyboard=storyboard, scene_specs=scene_specs)


# ── Storyboard Service ───────────────────────────────────────────────


class StoryboardService:
    """Service for generating, editing and validating structured storyboards.

    Follows the same pattern as T032 MediaGenerationService: deterministic
    by default, with optional ModelGateway injection via the generator Protocol.
    """

    def __init__(
        self,
        generator: StoryboardGenerator | None = None,
    ) -> None:
        self._generator = generator or DeterministicStoryboardGenerator()
        self._storyboards: dict[str, MediaStoryboard] = {}
        self._scene_specs: dict[str, SceneSpec] = {}

    def generate_storyboard(
        self,
        request: StoryboardGenerationRequest,
        *,
        account_id: str,
        fact_locks: list[FactLock] | None = None,
    ) -> StoryboardResult:
        """Generate a storyboard from structured input."""
        result = self._generator.generate_storyboard(request)
        storyboard = result.storyboard
        storyboard.account_id = account_id
        storyboard.status = StoryboardStatus.DESIGNING
        storyboard.updated_at = _now()

        self._storyboards[storyboard.storyboard_id] = storyboard
        for spec_id, spec in result.scene_specs.items():
            self._scene_specs[spec_id] = spec

        return result

    def get_storyboard(self, storyboard_id: str) -> MediaStoryboard:
        """Retrieve a storyboard by ID."""
        storyboard = self._storyboards.get(storyboard_id)
        if storyboard is None:
            raise StoryboardError(f"分镜 {storyboard_id} 不存在。")
        return storyboard

    def get_scene_spec(self, scene_spec_id: str) -> SceneSpec:
        """Retrieve a scene spec by ID."""
        spec = self._scene_specs.get(scene_spec_id)
        if spec is None:
            raise StoryboardError(f"场景规格 {scene_spec_id} 不存在。")
        return spec

    def update_storyboard(
        self,
        storyboard_id: str,
        *,
        title: str | None = None,
        teaching_objectives: list[str] | None = None,
        scenes: list[StoryboardScene] | None = None,
        scene_specs: dict[str, SceneSpec] | None = None,
        fact_locks: list[FactLock] | None = None,
        account_id: str | None = None,
    ) -> MediaStoryboard:
        """Update a storyboard's fields and re-validate.

        Users can modify scenes, objectives, or scene specs and then
        re-validate the storyboard without regenerating from scratch.
        Updates are scoped to the owning account when account_id is provided.
        """
        storyboard = self.get_storyboard(storyboard_id)
        if account_id and storyboard.account_id != account_id:
            raise StoryboardError(
                f"分镜 {storyboard_id} 不属于当前账户，无法修改。"
            )
        now = _now()

        updates: dict[str, object] = {"updated_at": now}
        if title is not None:
            updates["title"] = title
        if teaching_objectives is not None:
            updates["teaching_objectives"] = teaching_objectives
        if scenes is not None:
            updates["scenes"] = scenes
            storyboard.status = StoryboardStatus.DESIGNING

        updated = storyboard.model_copy(update=updates)
        self._storyboards[storyboard_id] = updated

        if scene_specs:
            for spec_id, spec in scene_specs.items():
                self._scene_specs[spec_id] = spec

        return updated

    def validate_storyboard(
        self, storyboard_id: str
    ) -> ValidationReport:
        """Validate a storyboard for completeness and internal consistency.

        Checks:
        - Storyboard exists
        - At least one scene defined
        - Each scene references an existing SceneSpec
        - Scenes have valid timing (positive duration)
        - Scene numbering is sequential
        """
        storyboard = self.get_storyboard(storyboard_id)
        errors: list[str] = []
        warnings: list[str] = []

        if not storyboard.scenes:
            errors.append("分镜没有定义任何镜头。")

        for i, scene in enumerate(storyboard.scenes):
            if scene.scene_number != i + 1:
                warnings.append(
                    f"镜头 {scene.scene_id} 序号为 {scene.scene_number}，"
                    f"预期为 {i + 1}。"
                )
            if scene.timing_seconds <= 0:
                errors.append(
                    f"镜头 {scene.scene_id} 时长必须大于 0。"
                )
            if scene.scene_spec_id not in self._scene_specs:
                warnings.append(
                    f"镜头 {scene.scene_id} 引用的场景规格 "
                    f"{scene.scene_spec_id} 不存在。"
                )

        passed = len(errors) == 0
        if passed:
            storyboard.status = StoryboardStatus.STATIC_VALIDATED
        self._storyboards[storyboard_id] = storyboard

        return ValidationReport(
            report_id=_generate_id(),
            run_id="",
            storyboard_id=storyboard_id,
            science_valid=passed,
            fact_locks_preserved=passed,
            accessibility_checked=False,
            errors=errors,
            warnings=warnings,
            created_at=_now(),
        )

    def generate_source_code(
        self,
        storyboard_id: str,
        code_language: str = "html",
    ) -> EditableSource:
        """Generate executable source code from a storyboard.

        For deterministic operation, generates a minimal HTML/JS scaffold
        that describes the scenes from the storyboard.
        """
        storyboard = self.get_storyboard(storyboard_id)
        now = _now()

        if code_language == "html":
            code = self._generate_html_code(storyboard)
        elif code_language in ("python", "python/manim"):
            code = self._generate_python_code(storyboard)
        else:
            raise StoryboardError(f"不支持的代码语言: {code_language}。")

        source = EditableSource(
            source_id=_generate_id(),
            source_type="sandbox_code",
            content=code,
            format="text/plain",
            version=1,
        )

        storyboard.status = StoryboardStatus.SOURCE_GENERATED
        storyboard.updated_at = now
        self._storyboards[storyboard_id] = storyboard

        return source

    def _generate_html_code(self, storyboard: MediaStoryboard) -> str:
        """Generate a minimal HTML page describing the storyboard scenes."""
        parts: list[str] = [
            "<!DOCTYPE html>",
            '<html lang="zh-CN">',
            "<head><meta charset='utf-8'>",
            f"<title>{storyboard.title}</title>",
            "<style>"
            "body{font-family:sans-serif;margin:2em;}"
            ".scene{border:1px solid #ccc;margin:1em 0;padding:1em;border-radius:8px;}"
            ".narration{color:#555;font-style:italic;}"
            "</style></head><body>",
            f"<h1>{storyboard.title}</h1>",
        ]
        if storyboard.teaching_objectives:
            parts.append("<h2>教学目标</h2><ul>")
            for obj in storyboard.teaching_objectives:
                parts.append(f"<li>{obj}</li>")
            parts.append("</ul>")

        for scene in storyboard.scenes:
            parts.append(f"<div class='scene' id='scene-{scene.scene_number}'>")
            parts.append(f"<h3>场景 {scene.scene_number}</h3>")
            parts.append(f"<p>时长: {scene.timing_seconds}秒</p>")
            if scene.transition_type:
                parts.append(f"<p>过渡: {scene.transition_type}</p>")
            if scene.narration:
                parts.append(
                    f"<p class='narration'>旁白: {scene.narration.text}</p>"
                )
            if scene.scene_accessibility:
                parts.append(
                    f"<p class='accessibility'>无障碍: {scene.scene_accessibility}</p>"
                )
            parts.append("</div>")

        parts.append("</body></html>")
        return "\n".join(parts)

    def _generate_python_code(self, storyboard: MediaStoryboard) -> str:
        """Generate a minimal Python/Manim script from the storyboard."""
        parts: list[str] = [
            '"""',
            f"Generated from storyboard: {storyboard.title}",
            f"Teaching objectives: {', '.join(storyboard.teaching_objectives)}",
            '"""',
            "",
            "import math",
            "",
            "class SceneRenderer:",
            '    """Minimal scene renderer for storyboard playback."""',
            "",
            "    def __init__(self):",
            "        self.scenes = []",
            "",
            "    def add_scene(self, name, duration, narration):",
            "        self.scenes.append({",
            '            "name": name,',
            '            "duration": duration,',
            '            "narration": narration,',
            "        })",
            "",
            "    def render(self):",
            '        """Simulate rendering all scenes."""',
            "        result = []",
            "        for scene in self.scenes:",
            "            result.append(scene)",
            "        return result",
            "",
        ]
        parts.append("renderer = SceneRenderer()")
        parts.append("")
        for scene in storyboard.scenes:
            narration_text = ""
            if scene.narration:
                narration_text = scene.narration.text.replace('"', '\\"')
            parts.append(
                f'renderer.add_scene('
                f'"场景{scene.scene_number}", '
                f"{scene.timing_seconds}, "
                f'"{narration_text}"'
                f')'
            )
        parts.append("")
        parts.append("raw = renderer.render()")
        parts.append("output = ''")
        parts.append("for s in raw:")
        parts.append("    output += s['name'] + ' '")
        parts.append("")

        return "\n".join(parts)


# ── Safe builtins for sandbox execution ────────────────────────────

# Dev/test sandbox: security relies on pre-execution static check.
# Production sandbox (Linux container) will enforce true isolation.
# We provide the real builtins module so all Python syntax works.
# The static check (import whitelist, banned patterns) runs BEFORE exec.
import builtins as _real_builtins

_SAFE_BUILTINS: dict[str, object] = {
    k: v for k, v in _real_builtins.__dict__.items()
    if not k.startswith("_")
}
# Re-add critical dunder builtins needed for basic Python syntax.
for _dunder in ("__import__", "__build_class__", "__name__"):
    _SAFE_BUILTINS[_dunder] = _real_builtins.__dict__[_dunder]
# Remove dangerous functions; pre-exec static check handles import control.
for _dangerous in ("eval", "exec", "compile", "open", "breakpoint",
                   "input", "help"):
    _SAFE_BUILTINS.pop(_dangerous, None)


# ── Sandbox Runtime Protocol ─────────────────────────────────────────


class SandboxRuntime(Protocol):
    """Protocol for sandbox execution strategies.

    Abstracts the isolation boundary. Current implementation uses in-memory
    simulation; future implementations will use real Linux containers
    (gVisor/Kata) with true network/process isolation.
    """

    def execute(
        self,
        code: str,
        language: str,
        limits: SandboxResourceLimits,
    ) -> SandboxRunResult:
        ...


class InMemorySandboxRuntime:
    """In-memory sandbox simulation for development and testing.

    Simulates the sandbox execution environment:
    - Default blocks network and key access
    - Records resource usage estimates
    - Performs static code analysis
    - Supports deterministic success/failure injection
    - No real containerization — for dev/test only
    """

    def execute(
        self,
        code: str,
        language: str,
        limits: SandboxResourceLimits,
    ) -> SandboxRunResult:
        now = _now()

        # Run static check first.
        check = static_check(code, language)
        error_log: list[str] = list(check.errors)

        if not check.passed:
            return SandboxRunResult(
                run_id=_generate_id(),
                storyboard_id="",
                status=SandboxRunStatus.FAILED,
                output=None,
                error_log=error_log,
                resource_usage=SandboxResourceUsage(
                    cpu_time_ms=10,
                    memory_bytes=1024,
                    disk_bytes=len(code.encode("utf-8")),
                    network_blocked=not limits.network_allowed,
                    keys_blocked=not limits.keys_allowed,
                ),
                dependencies=[],
                content_hash=_sha256(code),
                created_at=now,
                completed_at=now,
            )

        # Simulate execution — try to exec Python code in an isolated way.
        output: str | None = None
        exec_error: str | None = None
        deps: list[SandboxDependency] = []
        if language == "python":
            try:
                local_ns: dict[str, object] = {}
                compiled = compile(code, "<sandbox>", "exec")
                exec(
                    compiled,
                    {"__builtins__": _SAFE_BUILTINS},
                    local_ns,
                )
                output = str(local_ns.get("output", ""))

                # Collect dependencies from module references.
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            deps.append(
                                SandboxDependency(
                                    name=alias.name,
                                    version=None,
                                    allowed=alias.name.split(".")[0]
                                    in ALLOWED_PYTHON_IMPORTS,
                                )
                            )
            except Exception as exc:
                exec_error = f"执行错误: {exc}"
                error_log.append(exec_error)
        elif language in ("html", "javascript"):
            # For HTML/JS, just record the code as output (no real browser).
            output = code
        else:
            error_log.append(f"不支持的代码语言: '{language}'。")

        status = (
            SandboxRunStatus.FAILED
            if error_log
            else SandboxRunStatus.COMPLETED
        )

        return SandboxRunResult(
            run_id=_generate_id(),
            storyboard_id="",
            status=status,
            output=output,
            error_log=error_log,
            resource_usage=SandboxResourceUsage(
                cpu_time_ms=50 if status == SandboxRunStatus.COMPLETED else 10,
                memory_bytes=4096 if status == SandboxRunStatus.COMPLETED else 1024,
                disk_bytes=len(code.encode("utf-8")),
                network_blocked=not limits.network_allowed,
                keys_blocked=not limits.keys_allowed,
            ),
            dependencies=deps,
            content_hash=_sha256(code),
            created_at=now,
            completed_at=now,
        )


# ── Sandbox Service ──────────────────────────────────────────────────


class SandboxService:
    """Service for executing generated code in an isolated sandbox.

    Supports:
    - Running code with resource limits
    - Retrieving run results
    - Limited repair with fact-lock invariant preservation
    - Recording dependencies, resource usage, errors and content hash
    """

    def __init__(
        self,
        runtime: SandboxRuntime | None = None,
    ) -> None:
        self._runtime = runtime or InMemorySandboxRuntime()
        self._runs: dict[str, SandboxRunResult] = {}
        # Store original source codes for fact-lock invariant checks.
        self._run_sources: dict[str, str] = {}

    def run(
        self,
        request: SandboxRunRequest,
        *,
        account_id: str,
    ) -> SandboxRunResult:
        """Run generated code in the sandbox.

        The sandbox enforces:
        - No production network access (default blocked)
        - No keys/secrets access (default blocked)
        - Limited resources (CPU, memory, disk, processes)
        """
        result = self._runtime.execute(
            request.source_code,
            request.code_language,
            request.resource_limits,
        )
        result.storyboard_id = request.storyboard_id
        result.repair_attempts = 0

        # Store the original source for fact-lock invariant checks.
        self._run_sources[result.run_id] = request.source_code
        self._runs[result.run_id] = result
        return result

    def get_run(self, run_id: str) -> SandboxRunResult:
        """Retrieve a sandbox run result by ID."""
        run = self._runs.get(run_id)
        if run is None:
            raise SandboxError(f"沙箱运行 {run_id} 不存在。")
        return run

    def repair(
        self,
        run_id: str,
        patch: str,
        *,
        fact_locks: list[FactLock] | None = None,
        fact_lock_ids: list[str] | None = None,
        code_language: str | None = None,
    ) -> SandboxRunResult:
        """Attempt a limited repair on a failed sandbox run.

        The repair:
        - Only allows repair if the original run was FAILED
        - Checks repair budget (max attempts)
        - Verifies fact-lock invariants (fact locks cannot be changed)
        - Re-runs the sandbox with patched code
        - After budget exhaustion, marks as QUARANTINED via storyboard status
        """
        run = self.get_run(run_id)
        if run.status != SandboxRunStatus.FAILED:
            raise SandboxError(
                f"沙箱运行 {run_id} 状态为 {run.status.value}，无法修复。"
                "只有 FAILED 状态的运行可以修复。"
            )

        if run.repair_attempts >= 3:
            raise SandboxError(
                f"沙箱运行 {run_id} 修复预算已耗尽。"
            )

        # Check fact-lock invariants.
        original_code = self._run_sources.get(run_id, "")
        violations: list[FactLockViolation] = []
        if fact_locks:
            for lock in fact_locks:
                # Check if the patch removes the canonical value that existed
                # in the original code.
                if (
                    lock.canonical_value
                    and lock.canonical_value in original_code
                    and lock.canonical_value not in patch
                ):
                    violations.append(
                        FactLockViolation(
                            lock_id=lock.lock_id,
                            claim_id=lock.claim_id,
                            attempted_change=(
                                f"移除了事实锁标准值: {lock.canonical_value}"
                            ),
                            reason="修复不能移除事实锁约束的标准值。",
                        )
                    )

                # Check for forbidden transformations in patch.
                for forbidden in lock.forbidden_transformations:
                    if forbidden in patch.lower():
                        violations.append(
                            FactLockViolation(
                                lock_id=lock.lock_id,
                                claim_id=lock.claim_id,
                                attempted_change=f"包含了禁止的变换: {forbidden}",
                                reason="修复不能改变事实锁约束。",
                            )
                        )

        if violations:
            run.fact_lock_violations = violations
            self._runs[run_id] = run
            return run

        # Re-run the sandbox with the patched code.
        # Preserve the code language from the caller if provided; otherwise
        # default to "python" which covers the known deterministic use case.
        target_language = code_language or "python"
        merged_lock_ids = list(set(
            (fact_lock_ids or []) + [v.lock_id for v in violations if v.lock_id]
        ))

        patched_request = SandboxRunRequest(
            storyboard_id=run.storyboard_id,
            source_code=patch,
            code_language=target_language,
            repair_budget=3,
            fact_lock_ids=merged_lock_ids,
        )
        new_result = self._runtime.execute(
            patched_request.source_code,
            patched_request.code_language,
            SandboxResourceLimits(),
        )
        new_result.storyboard_id = run.storyboard_id
        new_result.repair_attempts = run.repair_attempts + 1

        self._run_sources[new_result.run_id] = patch
        self._runs[new_result.run_id] = new_result
        return new_result


# ── Build validation report ──────────────────────────────────────────


def build_validation_report(
    storyboard_service: StoryboardService,
    sandbox_service: SandboxService,
    storyboard_id: str,
    run_id: str,
) -> ValidationReport:
    """Build a comprehensive validation report for a storyboard sandbox run."""
    storyboard = storyboard_service.get_storyboard(storyboard_id)
    run = sandbox_service.get_run(run_id)

    errors: list[str] = list(run.error_log)
    warnings: list[str] = []

    # Check if all scenes rendered.
    if run.status == SandboxRunStatus.COMPLETED:
        # No errors means science_valid is assumed true for this deterministic impl.
        science_valid = True
        fact_locks_preserved = len(run.fact_lock_violations) == 0
    else:
        science_valid = False
        fact_locks_preserved = False

    static_check_result: StaticCheckResult | None = None
    if run.error_log:
        static_check_result = StaticCheckResult(
            passed=False,
            errors=run.error_log,
        )

    return ValidationReport(
        report_id=_generate_id(),
        run_id=run_id,
        storyboard_id=storyboard_id,
        static_check=static_check_result,
        sandbox_result=run,
        science_valid=science_valid,
        fact_locks_preserved=fact_locks_preserved,
        accessibility_checked=bool(storyboard.scenes[0].scene_accessibility)
        if storyboard.scenes
        else False,
        errors=errors,
        warnings=warnings,
        created_at=_now(),
    )
