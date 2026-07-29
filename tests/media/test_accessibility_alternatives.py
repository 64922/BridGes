"""Module-interface tests for T034 accessibility alternatives.

The seam under test: AccessibilityService takes a storyboard, generated media
object or ingested audio/video asset and produces a complete accessibility
bundle — narration audio, transcript, captions, alt text, keyboard paths,
reduced-motion variant and sequential reading view. All alternatives share the
same scientific claims and source version, pass the same scientific validation,
and time-based content supports pause, seeking and reduced motion.

Key acceptance criteria:
- 音频、字幕和文字稿共享同一科学 Claim 与版本
- 交互和媒体核心任务可由键盘与屏幕阅读器完成
- 用户可以暂停、控制时间内容并启用减少动画
- 无障碍替代经过相同科学校验，不形成另一个未经验证的答案
"""

from __future__ import annotations

import base64

import pytest

from science_companion.contracts.media import (
    AccessibilityBundle,
    AccessibilityBundleRequest,
    AccessibilityTargetKind,
    ChartDataColumn,
    ChartDataPoint,
    ChartDataTable,
    ChartGenerationRequest,
    ChartMark,
    MediaUploadRequest,
    NarrationSynthesisStatus,
    PlaybackControlRequest,
    PronunciationNoteKind,
    StoryboardClaimBinding,
    StoryboardGenerationRequest,
    StoryboardNarration,
    StoryboardScene,
)
from science_companion.contracts.science import (
    FactLock,
    FactLockType,
    LicenseState,
    MediaType,
)
from science_companion.media import MediaGenerationService, MediaIngestionService
from science_companion.media.accessibility_service import (
    CORE_MEDIA_TASKS,
    AccessibilityError,
    AccessibilityService,
)
from science_companion.media.storyboard_service import StoryboardService

ACCOUNT = "account-alice"
OTHER_ACCOUNT = "account-bob"


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def storyboard_service() -> StoryboardService:
    return StoryboardService()


@pytest.fixture
def generation_service() -> MediaGenerationService:
    return MediaGenerationService()


@pytest.fixture
def ingestion_service() -> MediaIngestionService:
    return MediaIngestionService()


@pytest.fixture
def service(
    storyboard_service: StoryboardService,
    generation_service: MediaGenerationService,
    ingestion_service: MediaIngestionService,
) -> AccessibilityService:
    return AccessibilityService(
        storyboard_service=storyboard_service,
        generation_service=generation_service,
        media_ingestion_service=ingestion_service,
    )


@pytest.fixture
def storyboard_id(storyboard_service: StoryboardService) -> str:
    """A two-scene storyboard whose narration carries a locked value."""
    request = StoryboardGenerationRequest(
        title="自由落体运动",
        teaching_objectives=["理解重力加速度的物理意义"],
        media_type="animation",
        scenes=[
            StoryboardScene(
                scene_id="scene-001",
                scene_number=1,
                scene_spec_id="spec-001",
                timing_seconds=6.0,
                transition_type="cut",
                narration=StoryboardNarration(
                    text="在地球表面附近，重力加速度约为 9.8 m/s²。",
                    claim_ids=["claim-gravity"],
                ),
                scene_claim_bindings=[
                    StoryboardClaimBinding(
                        binding_id="bind-001",
                        scene_id="scene-001",
                        element_ref="ball",
                        claim_id="claim-gravity",
                        fact_lock_id="lock-gravity",
                    ),
                ],
                scene_accessibility="小球从静止开始下落，速度随时间线性增大。",
            ),
            StoryboardScene(
                scene_id="scene-002",
                scene_number=2,
                scene_spec_id="spec-002",
                timing_seconds=4.0,
                transition_type="dissolve",
                narration=StoryboardNarration(
                    text="下落距离与时间的平方成正比。",
                    claim_ids=["claim-distance"],
                ),
                scene_accessibility="曲线图展示距离随时间平方增长。",
            ),
        ],
    )
    result = storyboard_service.generate_storyboard(request, account_id=ACCOUNT)
    return result.storyboard.storyboard_id


@pytest.fixture
def gravity_lock() -> FactLock:
    return FactLock(
        lock_id="lock-gravity",
        claim_id="claim-gravity",
        lock_type=FactLockType.EXACT_VALUE,
        canonical_value="9.8 m/s²",
        forbidden_transformations=["必然证明"],
    )


def _storyboard_bundle(
    service: AccessibilityService,
    storyboard_id: str,
    fact_locks: list[FactLock] | None = None,
) -> AccessibilityBundle:
    request = AccessibilityBundleRequest(
        target_kind=AccessibilityTargetKind.STORYBOARD,
        target_id=storyboard_id,
    )
    return service.generate_bundle(
        request, account_id=ACCOUNT, fact_locks=fact_locks
    )


# ── Bundle generation ────────────────────────────────────────────────


class TestBundleGeneration:
    def test_bundle_contains_all_alternatives(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)

        assert bundle.alt_text
        assert bundle.long_description
        assert bundle.transcript.segments
        assert bundle.transcript.full_text
        assert bundle.caption_track.captions
        assert bundle.narration.text
        assert bundle.keyboard_paths
        assert bundle.reduced_motion.frames
        assert bundle.sequential_view.blocks

    def test_transcript_timing_follows_scene_timing(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        segments = bundle.transcript.segments
        assert len(segments) == 2
        assert segments[0].start_time == 0.0
        assert segments[0].end_time == 6.0
        assert segments[1].start_time == 6.0
        assert segments[1].end_time == 10.0

    def test_captions_stay_within_asset_timeline(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        for caption in bundle.caption_track.captions:
            assert 0.0 <= caption.start_time < caption.end_time <= 10.0
            assert caption.text

    def test_narration_is_synthesized_and_timed(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        narration = bundle.narration
        assert narration.status == NarrationSynthesisStatus.SYNTHESIZED
        assert narration.audio_ref is not None
        assert narration.duration_seconds == 10.0
        assert [t.segment_id for t in narration.timing] == [
            s.segment_id for s in bundle.transcript.segments
        ]

    def test_pronunciation_notes_cover_numbers_and_units(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        kinds = {note.kind for note in bundle.narration.pronunciation_notes}
        assert PronunciationNoteKind.NUMBER in kinds
        assert PronunciationNoteKind.UNIT in kinds
        # The formula-like unit m/s² must be handled or explicitly degraded.
        unit_notes = [
            n for n in bundle.narration.pronunciation_notes
            if n.kind in (PronunciationNoteKind.UNIT, PronunciationNoteKind.FORMULA)
        ]
        assert all(n.spoken_form or n.degraded for n in unit_notes)

    def test_unknown_storyboard_rejected(
        self, service: AccessibilityService
    ) -> None:
        request = AccessibilityBundleRequest(
            target_kind=AccessibilityTargetKind.STORYBOARD,
            target_id="missing",
        )
        with pytest.raises(AccessibilityError):
            service.generate_bundle(request, account_id=ACCOUNT)

    def test_other_account_cannot_bundle_foreign_storyboard(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        request = AccessibilityBundleRequest(
            target_kind=AccessibilityTargetKind.STORYBOARD,
            target_id=storyboard_id,
        )
        with pytest.raises(AccessibilityError):
            service.generate_bundle(request, account_id=OTHER_ACCOUNT)

    def test_get_bundle_scoped_to_account(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        fetched = service.get_bundle(bundle.bundle_id, account_id=ACCOUNT)
        assert fetched.bundle_id == bundle.bundle_id
        with pytest.raises(AccessibilityError):
            service.get_bundle(bundle.bundle_id, account_id=OTHER_ACCOUNT)


# ── Acceptance 1: shared claims and version ──────────────────────────


class TestSharedClaimsAndVersion:
    def test_audio_captions_transcript_share_claims(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        expected = {"claim-gravity", "claim-distance"}
        assert set(bundle.claim_ids) == expected
        assert set(bundle.narration.claim_ids) == expected
        assert set(bundle.caption_track.claim_ids) == expected
        assert set(bundle.transcript.claim_ids) == expected

    def test_all_alternatives_share_source_version(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        version = bundle.source_version
        assert version
        assert bundle.narration.source_version == version
        assert bundle.caption_track.source_version == version
        assert bundle.transcript.source_version == version
        assert bundle.reduced_motion.source_version == version
        assert bundle.sequential_view.source_version == version

    def test_validation_reports_claim_and_version_consistency(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        result = service.validate_bundle(bundle.bundle_id, account_id=ACCOUNT)
        assert result.claims_consistent is True
        assert result.version_consistent is True
        assert result.valid is True


# ── Acceptance 2: keyboard and screen reader ─────────────────────────


class TestKeyboardAndScreenReader:
    def test_keyboard_paths_cover_core_media_tasks(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        covered = {path.task for path in bundle.keyboard_paths}
        assert covered >= set(CORE_MEDIA_TASKS)

    def test_every_step_has_screen_reader_announcement(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        for path in bundle.keyboard_paths:
            assert path.steps
            for step in path.steps:
                assert step.keys
                assert step.screen_reader_announcement.strip()

    def test_sequential_view_delivers_same_scientific_content(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        """A screen reader user reading the sequential view gets the narration
        content, including the locked value, in reading order."""
        bundle = _storyboard_bundle(service, storyboard_id)
        joined = "\n".join(block.text for block in bundle.sequential_view.blocks)
        assert "9.8 m/s²" in joined
        assert "自由落体运动" in joined
        orders = [block.order for block in bundle.sequential_view.blocks]
        assert orders == sorted(orders)

    def test_validation_flags_missing_keyboard_coverage(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        # Simulate a bundle stripped of keyboard paths.
        stripped = bundle.model_copy(update={"keyboard_paths": []})
        service._bundles[bundle.bundle_id] = stripped  # noqa: SLF001
        result = service.validate_bundle(bundle.bundle_id, account_id=ACCOUNT)
        assert result.keyboard_operable is False
        assert result.valid is False


# ── Acceptance 3: pause, time control and reduced motion ────────────


class TestPlaybackAndReducedMotion:
    def test_user_can_pause_and_resume(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        state = service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="start"),
            account_id=ACCOUNT,
        )
        assert state.paused is False
        assert state.duration_seconds == 10.0

        state = service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="pause"),
            account_id=ACCOUNT,
        )
        assert state.paused is True

        state = service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="resume"),
            account_id=ACCOUNT,
        )
        assert state.paused is False

    def test_seek_updates_active_caption(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="start"),
            account_id=ACCOUNT,
        )
        state = service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="seek", position_seconds=7.0),
            account_id=ACCOUNT,
        )
        assert state.position_seconds == 7.0
        assert state.active_caption_text is not None
        assert state.active_caption_text in "下落距离与时间的平方成正比。"

    def test_seek_clamped_to_duration(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="start"),
            account_id=ACCOUNT,
        )
        state = service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="seek", position_seconds=99.0),
            account_id=ACCOUNT,
        )
        assert state.position_seconds == 10.0

    def test_reduced_motion_shows_consistent_static_frame(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        """In reduced-motion mode the user gets the same scientific content
        as the animated version at the same point in time."""
        bundle = _storyboard_bundle(service, storyboard_id)
        service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="start"),
            account_id=ACCOUNT,
        )
        state = service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="set_reduced_motion", enabled=True),
            account_id=ACCOUNT,
        )
        assert state.reduced_motion_enabled is True
        assert state.active_frame_description is not None
        assert "9.8 m/s²" in state.active_frame_description

        state = service.control_playback(
            bundle.bundle_id,
            PlaybackControlRequest(action="seek", position_seconds=8.0),
            account_id=ACCOUNT,
        )
        assert state.active_frame_description is not None
        assert "平方成正比" in state.active_frame_description

    def test_reduced_motion_frames_align_with_scenes(
        self, service: AccessibilityService, storyboard_id: str
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        frames = bundle.reduced_motion.frames
        assert len(frames) == 2
        assert frames[0].start_time == 0.0
        assert frames[0].end_time == 6.0
        assert frames[1].start_time == 6.0
        assert frames[1].end_time == 10.0


# ── Acceptance 4: same scientific validation ─────────────────────────


class TestScienceValidation:
    def test_bundle_passing_fact_locks_is_science_validated(
        self,
        service: AccessibilityService,
        storyboard_id: str,
        gravity_lock: FactLock,
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id, [gravity_lock])
        assert bundle.science_validated is True
        assert bundle.validation_errors == []
        # Locked value is preserved in every textual alternative.
        assert gravity_lock.canonical_value in bundle.transcript.full_text
        captions_text = "".join(c.text for c in bundle.caption_track.captions)
        assert gravity_lock.canonical_value in captions_text
        assert gravity_lock.canonical_value in bundle.narration.text

    def test_forbidden_transformation_blocks_validation(
        self,
        service: AccessibilityService,
        storyboard_service: StoryboardService,
        gravity_lock: FactLock,
    ) -> None:
        request = StoryboardGenerationRequest(
            title="错误的因果叙事",
            media_type="animation",
            scenes=[
                StoryboardScene(
                    scene_id="scene-bad",
                    scene_number=1,
                    scene_spec_id="spec-bad",
                    timing_seconds=5.0,
                    narration=StoryboardNarration(
                        text="重力加速度 9.8 m/s² 必然证明所有物体同时落地。",
                        claim_ids=["claim-gravity"],
                    ),
                ),
            ],
        )
        result = storyboard_service.generate_storyboard(request, account_id=ACCOUNT)
        bundle = _storyboard_bundle(
            service, result.storyboard.storyboard_id, [gravity_lock]
        )
        assert bundle.science_validated is False
        assert bundle.validation_errors
        # An unvalidated alternative must not become a synthesized answer.
        assert bundle.narration.status == NarrationSynthesisStatus.PENDING_SYNTHESIS
        assert bundle.narration.audio_ref is None

        validation = service.validate_bundle(
            bundle.bundle_id, account_id=ACCOUNT, fact_locks=[gravity_lock]
        )
        assert validation.science_validated is False
        assert validation.valid is False

    def test_revalidation_with_fact_locks_checks_alternatives(
        self,
        service: AccessibilityService,
        storyboard_id: str,
        gravity_lock: FactLock,
    ) -> None:
        bundle = _storyboard_bundle(service, storyboard_id)
        result = service.validate_bundle(
            bundle.bundle_id, account_id=ACCOUNT, fact_locks=[gravity_lock]
        )
        assert result.science_validated is True
        assert result.valid is True


# ── Other target kinds ───────────────────────────────────────────────


class TestMediaObjectTarget:
    def test_chart_bundle_includes_data_rows_in_sequential_view(
        self,
        service: AccessibilityService,
        generation_service: MediaGenerationService,
    ) -> None:
        data = ChartDataTable(
            columns=[
                ChartDataColumn(name="时间", data_type="number", unit="s"),
                ChartDataColumn(name="距离", data_type="number", unit="m"),
            ],
            rows=[
                ChartDataPoint(values={"时间": 1.0, "距离": 4.9}),
                ChartDataPoint(values={"时间": 2.0, "距离": 19.6}),
            ],
        )
        generated = generation_service.generate_chart(
            ChartGenerationRequest(
                title="下落距离随时间变化",
                mark=ChartMark.LINE,
                data=data,
                x_field="时间",
                y_field="距离",
                claim_ids=["claim-distance"],
            ),
            account_id=ACCOUNT,
        )
        media_object_id = generated.media_object.media_object_id

        bundle = service.generate_bundle(
            AccessibilityBundleRequest(
                target_kind=AccessibilityTargetKind.MEDIA_OBJECT,
                target_id=media_object_id,
            ),
            account_id=ACCOUNT,
        )
        assert bundle.alt_text
        assert bundle.source_version == "1"
        joined = "\n".join(b.text for b in bundle.sequential_view.blocks)
        assert "19.6" in joined
        roles = {b.role for b in bundle.sequential_view.blocks}
        assert "data_row" in roles

    def test_foreign_media_object_rejected(
        self,
        service: AccessibilityService,
        generation_service: MediaGenerationService,
    ) -> None:
        generated = generation_service.generate_chart(
            ChartGenerationRequest(
                title="他人图表",
                mark=ChartMark.BAR,
                data=ChartDataTable(
                    columns=[ChartDataColumn(name="x"), ChartDataColumn(name="y")],
                    rows=[ChartDataPoint(values={"x": "a", "y": 1.0})],
                ),
                x_field="x",
                y_field="y",
            ),
            account_id=OTHER_ACCOUNT,
        )
        with pytest.raises(AccessibilityError):
            service.generate_bundle(
                AccessibilityBundleRequest(
                    target_kind=AccessibilityTargetKind.MEDIA_OBJECT,
                    target_id=generated.media_object.media_object_id,
                ),
                account_id=ACCOUNT,
            )


class TestMediaAssetTarget:
    def test_ingested_audio_gets_bundle_bound_to_manifest_version(
        self,
        service: AccessibilityService,
        ingestion_service: MediaIngestionService,
    ) -> None:
        content = b"ID3\x04\x00" + b"\x00" * 28
        run = ingestion_service.ingest_upload(
            ACCOUNT,
            None,
            MediaUploadRequest(
                filename="lecture.mp3",
                media_type=MediaType.AUDIO_MPEG,
                content=base64.b64encode(content).decode("ascii"),
                license_state=LicenseState.USER_OWNED,
            ),
        )
        assert run.asset_id is not None

        bundle = service.generate_bundle(
            AccessibilityBundleRequest(
                target_kind=AccessibilityTargetKind.MEDIA_ASSET,
                target_id=run.asset_id,
            ),
            account_id=ACCOUNT,
        )
        assert bundle.target_kind == AccessibilityTargetKind.MEDIA_ASSET
        assert bundle.transcript.segments
        assert bundle.caption_track.captions
        assert bundle.source_version == "1"
        assert bundle.narration.text == bundle.transcript.full_text
