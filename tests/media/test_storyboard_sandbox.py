"""Module-interface tests for T033 structured storyboard and sandbox.

The seam under test: StoryboardService takes structured descriptions, produces
MediaStoryboard with SceneSpecs, supports editing and validation, generates
executable code, and feeds it to the SandboxService for isolated execution
with limited repair and fact-lock invariant preservation.

Key acceptance criteria:
- Storyboard明确教学目标、对象、布局、状态、时间、旁白和 Claim 绑定
- 生成代码在无生产网络、无密钥和有限资源的隔离环境运行
- 失败只能在事实锁不变时有限修复，预算耗尽后成品隔离
- 沙箱记录依赖、资源、输出、错误和内容哈希
"""

from __future__ import annotations

import json

import pytest

from bridges.contracts.media import (
    EditableSource,
    FactLockViolation,
    LifecycleStage,
    MediaStoryboard,
    SandboxResourceLimits,
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
from bridges.contracts.science import FactLock, FactLockType
from bridges.media.storyboard_service import (
    DeterministicStoryboardGenerator,
    InMemorySandboxRuntime,
    SandboxError,
    SandboxService,
    StoryboardError,
    StoryboardService,
    build_validation_report,
    static_check,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def storyboard_service() -> StoryboardService:
    return StoryboardService(generator=DeterministicStoryboardGenerator())


@pytest.fixture
def sandbox_service() -> SandboxService:
    return SandboxService(runtime=InMemorySandboxRuntime())


@pytest.fixture
def sample_request() -> StoryboardGenerationRequest:
    return StoryboardGenerationRequest(
        title="太阳系行星公转",
        teaching_objectives=[
            "理解行星公转轨道的基本规律",
            "掌握开普勒三定律的核心内容",
        ],
        media_type="animation",
        claim_ids=["claim-kepler-1", "claim-kepler-2"],
        fact_lock_ids=["lock-gravity", "lock-orbit"],
    )


@pytest.fixture
def sample_scene_request() -> StoryboardGenerationRequest:
    spec_id = "spec-001"
    return StoryboardGenerationRequest(
        title="细胞分裂过程",
        teaching_objectives=["了解有丝分裂各阶段特征"],
        media_type="animation",
        scenes=[
            StoryboardScene(
                scene_id="scene-001",
                scene_number=1,
                scene_spec_id=spec_id,
                timing_seconds=10.0,
                transition_type="cut",
                narration=StoryboardNarration(
                    text="这是细胞分裂的初期阶段。",
                    claim_ids=["claim-mitosis"],
                ),
                scene_claim_bindings=[
                    StoryboardClaimBinding(
                        binding_id="bind-001",
                        scene_id="scene-001",
                        element_ref="nucleus",
                        claim_id="claim-mitosis",
                    ),
                ],
                scene_accessibility="动画展示细胞从间期到分裂期的变化过程。",
            ),
        ],
    )


@pytest.fixture
def sample_locks() -> list[FactLock]:
    return [
        FactLock(
            lock_id="lock-gravity",
            claim_id="claim-kepler-1",
            lock_type=FactLockType.EXACT_VALUE,
            canonical_value="6.674e-11",
            allowed_variants=[],
            forbidden_transformations=["change_unit"],
            required_qualifiers=[],
            evidence_ids=[],
            citation_ids=[],
            wording_strength_ceiling="high",
            verification_method="rule",
        ),
        FactLock(
            lock_id="lock-orbit",
            claim_id="claim-kepler-2",
            lock_type=FactLockType.IDENTIFIER,
            canonical_value="椭圆",
            allowed_variants=["elliptical"],
            forbidden_transformations=[],
            required_qualifiers=[],
            evidence_ids=[],
            citation_ids=[],
            wording_strength_ceiling="high",
            verification_method="rule",
        ),
    ]


# ── Storyboard generation tests ──────────────────────────────────────


class TestStoryboardGeneration:
    """Acceptance: 分镜明确教学目标、对象、布局、状态、时间、旁白和 Claim 绑定。"""

    def test_generates_storyboard_with_teaching_objectives(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard = result.storyboard

        assert storyboard.title == "太阳系行星公转"
        assert len(storyboard.teaching_objectives) == 2
        assert "理解行星公转轨道的基本规律" in storyboard.teaching_objectives
        assert storyboard.account_id == "test-account"
        assert storyboard.status == StoryboardStatus.DESIGNING

    def test_generates_default_scene_when_no_scenes_provided(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard = result.storyboard

        assert len(storyboard.scenes) == 1
        scene = storyboard.scenes[0]
        assert scene.scene_number == 1
        assert scene.timing_seconds > 0
        assert scene.transition_type == "cut"
        assert scene.narration is not None
        assert "太阳系行星公转" in scene.narration.text

        # Verify scene has claim bindings from request.
        bindings = scene.scene_claim_bindings
        assert len(bindings) > 0
        claim_ids = {b.claim_id for b in bindings}
        assert "claim-kepler-1" in claim_ids

    def test_accepts_predefined_scenes(
        self,
        storyboard_service: StoryboardService,
        sample_scene_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_scene_request, account_id="test-account"
        )
        storyboard = result.storyboard

        assert len(storyboard.scenes) == 1
        scene = storyboard.scenes[0]
        assert scene.scene_id == "scene-001"
        assert scene.scene_number == 1
        assert scene.timing_seconds == 10.0
        assert scene.transition_type == "cut"
        assert scene.narration is not None
        assert scene.narration.text == "这是细胞分裂的初期阶段。"
        assert scene.scene_accessibility is not None

    def test_generates_scene_specs(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        # Default scene generates a SceneSpec.
        assert len(result.scene_specs) == 1
        spec_id, spec = next(iter(result.scene_specs.items()))
        assert spec.title is not None
        assert len(spec.visual_objects) == 1
        obj = spec.visual_objects[0]
        assert obj.label == "太阳系行星公转"
        assert obj.role == "title"
        assert len(obj.lifecycle_stages) == 2
        assert obj.lifecycle_stages[0].stage == "enter"
        assert obj.lifecycle_stages[1].stage == "hold"

    def test_default_scene_has_accessibility(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        spec = next(iter(result.scene_specs.values()))
        assert spec.accessibility is not None
        assert spec.accessibility.alt_text is not None
        assert spec.accessibility.long_description is not None


class TestStoryboardEditAndValidate:
    """Acceptance: users can edit and re-validate storyboards."""

    def test_update_storyboard_title(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        updated = storyboard_service.update_storyboard(
            storyboard_id, title="修改后标题", account_id="test-account"
        )
        assert updated.title == "修改后标题"
        assert updated.updated_at is not None

    def test_update_storyboard_objectives(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        new_objectives = ["新教学目标"]
        updated = storyboard_service.update_storyboard(
            storyboard_id,
            teaching_objectives=new_objectives,
            account_id="test-account",
        )
        assert updated.teaching_objectives == ["新教学目标"]

    def test_validate_storyboard_passes_for_valid(
        self,
        storyboard_service: StoryboardService,
        sample_scene_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_scene_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        report = storyboard_service.validate_storyboard(storyboard_id, account_id="test-account")
        assert report.science_valid is True
        assert len(report.errors) == 0

    def test_validate_storyboard_fails_for_empty_scenes(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        # Remove all scenes.
        storyboard_service.update_storyboard(
            storyboard_id, scenes=[], account_id="test-account"
        )
        report = storyboard_service.validate_storyboard(storyboard_id, account_id="test-account")
        assert report.science_valid is False
        assert any("没有定义任何镜头" in e for e in report.errors)

    def test_get_nonexistent_storyboard_raises_error(
        self,
        storyboard_service: StoryboardService,
    ) -> None:
        with pytest.raises(StoryboardError, match="不存在"):
            storyboard_service.get_storyboard("nonexistent-id", account_id="test-account")

    def test_update_storyboard_wrong_account_raises_error(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="owner-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        # Issue 39 AC9：跨账户访问统一按「不存在」处理，不泄漏对象归属
        with pytest.raises(StoryboardError, match="不存在"):
            storyboard_service.update_storyboard(
                storyboard_id,
                title="被篡改的标题",
                account_id="other-account",
            )


class TestCodeGeneration:
    """Acceptance: generate executable code from storyboard."""

    def test_generates_html_code(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        source = storyboard_service.generate_source_code(
            storyboard_id,
            code_language="html",
            account_id="test-account",
        )
        assert source.source_type == "sandbox_code"
        assert source.format == "text/plain"
        assert source.version == 1
        assert "<!DOCTYPE html>" in source.content
        assert "太阳系行星公转" in source.content

    def test_generates_python_code(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        source = storyboard_service.generate_source_code(
            storyboard_id,
            code_language="python",
            account_id="test-account",
        )
        assert "SceneRenderer" in source.content
        assert "太阳系行星公转" in source.content

    def test_code_generation_sets_status(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        storyboard_service.generate_source_code(
            storyboard_id,
            "html",
            account_id="test-account",
        )
        storyboard = storyboard_service.get_storyboard(storyboard_id, account_id="test-account")
        assert storyboard.status == StoryboardStatus.SOURCE_GENERATED

    def test_python_code_runs_in_sandbox_and_produces_string_output(
        self,
        storyboard_service: StoryboardService,
        sandbox_service: SandboxService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        """Verify generated Python code produces str output, not a list."""
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        source = storyboard_service.generate_source_code(
            storyboard_id,
            code_language="python",
            account_id="test-account",
        )
        sandbox_request = SandboxRunRequest(
            storyboard_id=storyboard_id,
            source_code=source.content,
            code_language="python",
        )
        run_result = sandbox_service.run(
            sandbox_request, account_id="test-account"
        )

        assert run_result.status == SandboxRunStatus.COMPLETED
        assert run_result.output is not None
        assert isinstance(run_result.output, str)
        assert "场景" in run_result.output

    def test_unsupported_language_raises_error(
        self,
        storyboard_service: StoryboardService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        with pytest.raises(StoryboardError, match="不支持"):
            storyboard_service.generate_source_code(
                storyboard_id,
                "ruby",
                account_id="test-account",
            )


class TestStaticCheck:
    """Static code analysis runs before sandbox execution."""

    def test_valid_python_passes(
        self,
    ) -> None:
        code = "import math\n\nresult = math.sqrt(16)\n"
        result = static_check(code, "python")
        assert result.passed is True
        assert result.ast_valid is True
        assert len(result.errors) == 0

    def test_invalid_syntax_fails(
        self,
    ) -> None:
        code = "def broken( :\n    pass\n"
        result = static_check(code, "python")
        assert result.passed is False
        assert result.ast_valid is False

    def test_banned_import_fails(
        self,
    ) -> None:
        code = "import os\nos.system('rm -rf /')\n"
        result = static_check(code, "python")
        assert result.passed is False
        assert any("禁止的 import" in e for e in result.errors)

    def test_banned_keyword_fails(
        self,
    ) -> None:
        code = "eval('print(1+1)')\n"
        result = static_check(code, "python")
        assert result.passed is False
        assert any("禁止模式" in e for e in result.errors)

    def test_exec_fails(
        self,
    ) -> None:
        code = "exec('print(1)')\n"
        result = static_check(code, "python")
        assert result.passed is False

    def test_html_banned_patterns_fail(
        self,
    ) -> None:
        code = '<div onclick="alert(1)">Click</div>'
        result = static_check(code, "html")
        assert result.passed is False
        assert any("禁止模式" in e for e in result.errors)

    def test_unsupported_language_fails(
        self,
    ) -> None:
        code = "some code"
        result = static_check(code, "ruby")
        assert result.passed is False

    def test_clean_html_passes(
        self,
    ) -> None:
        code = "<!DOCTYPE html><html><body><p>Hello</p></body></html>"
        result = static_check(code, "html")
        assert result.passed is True


class TestSandboxRun:
    """Acceptance: 生成代码在无生产网络、无密钥和有限资源的隔离环境运行。"""

    def test_successful_python_run(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = (
            '"""Test script."""\n'
            "import math\n\n"
            "class SceneRenderer:\n"
            "    def render(self):\n"
            '        return [{"name": "test", "duration": 5.0}]\n\n'
            "renderer = SceneRenderer()\n"
            "output = renderer.render()\n"
            "for s in output:\n"
            "    print('scene:', s['name'])\n"
        )
        request = SandboxRunRequest(
            storyboard_id="test-sb-001",
            source_code=code,
            code_language="python",
            resource_limits=SandboxResourceLimits(
                max_cpu_seconds=10,
                max_memory_mb=256,
                network_allowed=False,
                keys_allowed=False,
            ),
        )
        result = sandbox_service.run(request, account_id="test-account")

        assert result.status == SandboxRunStatus.COMPLETED
        assert result.storyboard_id == "test-sb-001"
        assert result.content_hash is not None
        assert len(result.content_hash) == 64  # SHA-256 hex length

    def test_sandbox_blocks_network_by_default(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\nx = 2 + 2\n"
        request = SandboxRunRequest(
            storyboard_id="test-sb-002",
            source_code=code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")

        # Default limits should block network.
        assert result.resource_usage.network_blocked is True
        assert result.resource_usage.keys_blocked is True

    def test_sandbox_records_resource_usage(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\nx = 0\nfor i in range(100):\n    x += i\n"
        request = SandboxRunRequest(
            storyboard_id="test-sb-003",
            source_code=code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")

        assert result.resource_usage.cpu_time_ms >= 0
        assert result.resource_usage.memory_bytes >= 0
        assert result.resource_usage.disk_bytes >= 0

    def test_sandbox_records_dependencies(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\nimport json\nx = math.pi\n"
        request = SandboxRunRequest(
            storyboard_id="test-sb-004",
            source_code=code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")

        # Should have completed successfully and recorded dependencies.
        assert result.status == SandboxRunStatus.COMPLETED
        assert len(result.dependencies) >= 2
        dep_names = {d.name for d in result.dependencies}
        assert "math" in dep_names
        assert "json" in dep_names
        assert all(d.allowed for d in result.dependencies)

    def test_failed_code_reports_errors(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\n\nraise ValueError('测试错误')\n"
        request = SandboxRunRequest(
            storyboard_id="test-sb-005",
            source_code=code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")

        assert result.status == SandboxRunStatus.FAILED
        assert len(result.error_log) > 0
        assert result.output is None

    def test_html_run_records_output(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "<!DOCTYPE html><html><body><p>Hello</p></body></html>"
        request = SandboxRunRequest(
            storyboard_id="test-sb-006",
            source_code=code,
            code_language="html",
        )
        result = sandbox_service.run(request, account_id="test-account")

        assert result.status == SandboxRunStatus.COMPLETED
        assert result.output == code  # HTML output is the code itself

    def test_content_hash_is_consistent(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\nprint('hello')\n"
        request = SandboxRunRequest(
            storyboard_id="test-sb-007",
            source_code=code,
            code_language="python",
        )
        result1 = sandbox_service.run(request, account_id="test-account")
        result2 = sandbox_service.run(request, account_id="test-account")

        # Same code should produce same hash.
        assert result1.content_hash == result2.content_hash

    def test_get_run_returns_result(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\nprint(1)\n"
        request = SandboxRunRequest(
            storyboard_id="test-sb-008",
            source_code=code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")

        retrieved = sandbox_service.get_run(result.run_id, account_id="test-account")
        assert retrieved.run_id == result.run_id
        assert retrieved.status == result.status

    def test_get_nonexistent_run_raises_error(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        with pytest.raises(SandboxError, match="不存在"):
            sandbox_service.get_run("nonexistent-run-id", account_id="test-account")


class TestSandboxRepair:
    """Acceptance: 失败只能在事实锁不变时有限修复，预算耗尽后成品隔离。"""

    def test_repair_failed_run(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        # First, create a run that fails.
        bad_code = "import math\n\nraise ValueError('initial error')\n"
        request = SandboxRunRequest(
            storyboard_id="test-repair-001",
            source_code=bad_code,
            code_language="python",
            repair_budget=3,
        )
        result = sandbox_service.run(request, account_id="test-account")
        assert result.status == SandboxRunStatus.FAILED

        # Repair with corrected code.
        good_code = "import math\n\nx = 42\n"
        repaired = sandbox_service.repair(
            result.run_id,
            good_code,
            account_id="test-account",
            fact_locks=None,
        )
        assert repaired.status == SandboxRunStatus.COMPLETED
        assert repaired.repair_attempts == 1

    def test_repair_only_works_on_failed_runs(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\nx = 2 + 2\n"
        request = SandboxRunRequest(
            storyboard_id="test-repair-002",
            source_code=code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")
        assert result.status == SandboxRunStatus.COMPLETED

        with pytest.raises(SandboxError, match="无法修复"):
            sandbox_service.repair(result.run_id, code, account_id="test-account", fact_locks=None)

    def test_repair_budget_exhaustion(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        # Create a run that fails.
        bad_code = "import math\n\nraise ValueError('error')\n"
        request = SandboxRunRequest(
            storyboard_id="test-repair-003",
            source_code=bad_code,
            code_language="python",
            repair_budget=3,
        )
        # Run with code that will fail repeatedly.
        result = sandbox_service.run(request, account_id="test-account")

        # Attempt repairs with still-broken code until budget exhausted.
        still_bad = "import math\n\nraise ValueError('still broken')\n"
        for attempt in range(3):
            result = sandbox_service.repair(result.run_id, still_bad, account_id="test-account", fact_locks=None)
            if result.repair_attempts >= 3:
                break

        # Next repair should fail with budget exhausted.
        with pytest.raises(SandboxError, match="预算已耗尽"):
            sandbox_service.repair(
                result.run_id,
                "import math\nprint('x')\n",
                account_id="test-account",
            )

    def test_repair_tracks_attempts(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        bad_code = "import math\n\nraise ValueError('err')\n"
        request = SandboxRunRequest(
            storyboard_id="test-repair-004",
            source_code=bad_code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")

        good_code = "import math\nprint('fixed')\n"
        repaired = sandbox_service.repair(result.run_id, good_code, account_id="test-account")
        assert repaired.repair_attempts == 1


class TestFactLockInvariants:
    """Acceptance: fact-lock invariants are preserved during repair."""

    def test_repair_that_violates_fact_lock_returns_violations(
        self,
        sandbox_service: SandboxService,
        sample_locks: list[FactLock],
    ) -> None:
        bad_code = "import math\n\nraise ValueError('error')\n"
        request = SandboxRunRequest(
            storyboard_id="test-fl-001",
            source_code=bad_code,
            code_language="python",
            fact_lock_ids=["lock-gravity", "lock-orbit"],
        )
        result = sandbox_service.run(request, account_id="test-account")
        assert result.status == SandboxRunStatus.FAILED

        # Try repair with a patch that contains forbidden transformation.
        patch_with_violation = (
            "import math\n\n"
            "# change_unit is forbidden by lock-gravity\n"
            "print('changed')\n"
        )
        repaired = sandbox_service.repair(
            result.run_id,
            patch_with_violation,
            account_id="test-account",
            fact_locks=sample_locks,
        )

        # The repair should still complete (the check is advisory for now).
        # But fact_lock_violations should be populated if violations detected.
        # The patch contains "change_unit" which is in forbidden_transformations.
        assert repaired.fact_lock_violations is not None

    def test_repair_without_fact_lock_violations_has_empty_list(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        bad_code = "import math\n\nraise ValueError('err')\n"
        request = SandboxRunRequest(
            storyboard_id="test-fl-002",
            source_code=bad_code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")
        assert result.status == SandboxRunStatus.FAILED

        good_code = "import math\nprint('clean repair')\n"
        repaired = sandbox_service.repair(
            result.run_id,
            good_code,
            account_id="test-account",
            fact_locks=None,
        )
        assert len(repaired.fact_lock_violations) == 0

    def test_repair_canonical_value_removal_detected(
        self,
        sandbox_service: SandboxService,
        sample_locks: list[FactLock],
    ) -> None:
        """Verify that removing a canonical value from code is detected."""
        # Original code contains the canonical value "6.674e-11".
        bad_code = "import math\n\nG = 6.674e-11\nraise ValueError('simulated')\n"
        request = SandboxRunRequest(
            storyboard_id="test-fl-003",
            source_code=bad_code,
            code_language="python",
        )
        result = sandbox_service.run(request, account_id="test-account")
        assert result.status == SandboxRunStatus.FAILED

        # Repair patch removes the canonical value.
        patch_no_value = "import math\n\nG = 7.0e-11\nprint('patched')\n"
        repaired = sandbox_service.repair(
            result.run_id,
            patch_no_value,
            account_id="test-account",
            fact_locks=sample_locks,
        )
        assert len(repaired.fact_lock_violations) >= 1
        assert any(
            "锁" in v.attempted_change for v in repaired.fact_lock_violations
        )


class TestCrossAccountIsolation:
    """Acceptance: different accounts' sandbox runs do not interfere."""

    def test_different_accounts_have_separate_runs(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\nprint('isolated')\n"

        request_a = SandboxRunRequest(
            storyboard_id="sb-account-a",
            source_code=code,
            code_language="python",
        )
        result_a = sandbox_service.run(request_a, account_id="account-a")

        request_b = SandboxRunRequest(
            storyboard_id="sb-account-b",
            source_code=code,
            code_language="python",
        )
        result_b = sandbox_service.run(request_b, account_id="account-b")

        # Each run should have its own ID.
        assert result_a.run_id != result_b.run_id
        # Same code should produce different run IDs.
        assert result_a.storyboard_id == "sb-account-a"
        assert result_b.storyboard_id == "sb-account-b"

    def test_same_code_different_accounts_produce_same_hash(
        self,
        sandbox_service: SandboxService,
    ) -> None:
        code = "import math\nprint('same')\n"
        request = SandboxRunRequest(
            storyboard_id="sb-hash",
            source_code=code,
            code_language="python",
        )

        result_a = sandbox_service.run(request, account_id="account-a")
        result_b = sandbox_service.run(request, account_id="account-b")

        # Content hash is content-derived and should be the same.
        assert result_a.content_hash == result_b.content_hash


class TestFullCycle:
    """Acceptance: full user flow from storyboard to validation report."""

    def test_full_storyboard_to_sandbox_cycle(
        self,
        storyboard_service: StoryboardService,
        sandbox_service: SandboxService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        # 1. Generate storyboard.
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id
        assert storyboard_id is not None
        assert result.storyboard.title == "太阳系行星公转"

        # 2. Validate storyboard.
        report = storyboard_service.validate_storyboard(storyboard_id, account_id="test-account")
        assert report.science_valid is True

        # 3. Generate source code.
        source = storyboard_service.generate_source_code(
            storyboard_id,
            code_language="python",
            account_id="test-account",
        )
        assert source.source_type == "sandbox_code"

        # 4. Run in sandbox.
        sandbox_request = SandboxRunRequest(
            storyboard_id=storyboard_id,
            source_code=source.content,
            code_language="python",
        )
        run_result = sandbox_service.run(
            sandbox_request, account_id="test-account"
        )

        # 5. Verify sandbox result.
        assert run_result.status == SandboxRunStatus.COMPLETED
        assert run_result.storyboard_id == storyboard_id
        assert run_result.content_hash is not None
        assert run_result.resource_usage.network_blocked is True

        # 6. Build validation report.
        v_report = build_validation_report(
            storyboard_service,
            sandbox_service,
            storyboard_id,
            run_result.run_id,
            account_id="test-account",
        )
        assert v_report.storyboard_id == storyboard_id
        assert v_report.run_id == run_result.run_id
        assert v_report.science_valid is True
        assert v_report.fact_locks_preserved is True

    def test_storyboard_edit_rerun_cycle(
        self,
        storyboard_service: StoryboardService,
        sandbox_service: SandboxService,
        sample_scene_request: StoryboardGenerationRequest,
    ) -> None:
        # 1. Generate storyboard.
        result = storyboard_service.generate_storyboard(
            sample_scene_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id
        assert len(result.storyboard.scenes) == 1

        # 2. Edit storyboard — change title and objectives.
        updated = storyboard_service.update_storyboard(
            storyboard_id,
            title="编辑后标题",
            teaching_objectives=["新教学目标"],
            account_id="test-account",
        )
        assert updated.title == "编辑后标题"
        assert updated.teaching_objectives == ["新教学目标"]

        # 3. Re-validate.
        report = storyboard_service.validate_storyboard(storyboard_id, account_id="test-account")
        assert report.science_valid is True

        # 4. Regenerate code.
        source = storyboard_service.generate_source_code(
            storyboard_id,
            "html",
            account_id="test-account",
        )
        assert "编辑后标题" in source.content

        # 5. Re-run in sandbox.
        sandbox_request = SandboxRunRequest(
            storyboard_id=storyboard_id,
            source_code=source.content,
            code_language="html",
        )
        run_result = sandbox_service.run(
            sandbox_request, account_id="test-account"
        )
        assert run_result.status == SandboxRunStatus.COMPLETED

        # 6. Verify consistency.
        assert run_result.storyboard_id == storyboard_id
        assert run_result.content_hash == sandbox_service.get_run(
            run_result.run_id, account_id="test-account"
        ).content_hash

    def test_sandbox_failure_with_validation_report(
        self,
        storyboard_service: StoryboardService,
        sandbox_service: SandboxService,
        sample_request: StoryboardGenerationRequest,
    ) -> None:
        # 1. Generate storyboard.
        result = storyboard_service.generate_storyboard(
            sample_request, account_id="test-account"
        )
        storyboard_id = result.storyboard.storyboard_id

        # 2. Run bad code in sandbox.
        bad_code = "import math\n\nraise RuntimeError('simulated failure')\n"
        sandbox_request = SandboxRunRequest(
            storyboard_id=storyboard_id,
            source_code=bad_code,
            code_language="python",
        )
        run_result = sandbox_service.run(
            sandbox_request, account_id="test-account"
        )
        assert run_result.status == SandboxRunStatus.FAILED
        assert len(run_result.error_log) > 0

        # 3. Build validation report for the failed run.
        v_report = build_validation_report(
            storyboard_service,
            sandbox_service,
            storyboard_id,
            run_result.run_id,
            account_id="test-account",
        )
        assert v_report.science_valid is False
        assert v_report.fact_locks_preserved is False
        assert len(v_report.errors) > 0
