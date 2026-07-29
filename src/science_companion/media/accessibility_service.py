"""T034: 朗读、字幕与完整无障碍替代生成服务。

AccessibilityService 面向三类目标（分镜、生成媒体对象、摄入媒体资产）
生成完整的无障碍替代包：朗读、文字稿、字幕、替代文本、键盘路径、
减少动画变体和顺序阅读视图。所有替代共享同一 Claim 集合和源版本，
并经过与主内容相同的科学校验（事实锁）。

朗读合成通过 NarrationSynthesizer 协议注入——默认使用确定性合成器，
真实 Qwen TTS 适配器在 T062 接入。未通过科学校验的文本保持
PENDING_SYNTHESIS，不合成语音，避免形成另一个未经验证的答案。
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime
from typing import Protocol

from science_companion.contracts.media import (
    AccessibilityBundle,
    AccessibilityBundleRequest,
    AccessibilityTargetKind,
    AccessibilityValidationResult,
    AudioVideoDerivedData,
    Caption,
    CaptionTrack,
    KeyboardAccessPath,
    KeyboardPathStep,
    MediaStoryboard,
    NarrationAudio,
    NarrationSynthesisStatus,
    NarrationTimingEntry,
    PlaybackControlRequest,
    PlaybackControls,
    PlaybackState,
    PronunciationNote,
    PronunciationNoteKind,
    ReducedMotionFrame,
    ReducedMotionVariant,
    SequentialReadingBlock,
    SequentialReadingView,
    StoryboardScene,
    Transcript,
    TranscriptSegment,
)
from science_companion.contracts.science import FactLock
from science_companion.media.generation import (
    MediaGenerationError,
    MediaGenerationService,
)
from science_companion.media.service import MediaError, MediaIngestionService
from science_companion.media.storyboard_service import StoryboardError, StoryboardService


class AccessibilityError(Exception):
    """Raised when accessibility bundle operations fail."""


#: 必须可由键盘与屏幕阅读器完成的核心媒体任务。
CORE_MEDIA_TASKS: tuple[str, ...] = (
    "play_pause",
    "seek",
    "toggle_captions",
    "toggle_reduced_motion",
    "open_transcript",
)

#: 常见科学单位的口语化读法词典。
_UNIT_SPOKEN_FORMS: dict[str, str] = {
    "m/s²": "米每二次方秒",
    "m/s^2": "米每二次方秒",
    "m/s": "米每秒",
    "km/h": "千米每小时",
    "m": "米",
    "km": "千米",
    "s": "秒",
    "kg": "千克",
    "g": "克",
    "N": "牛顿",
    "J": "焦耳",
    "W": "瓦特",
    "Hz": "赫兹",
    "°C": "摄氏度",
    "K": "开尔文",
    "mol": "摩尔",
    "Pa": "帕斯卡",
    "%": "百分之",
}

_NUMBER_UNIT_PATTERN = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-zΩμ°%][A-Za-zΩμ°%²³^/·]*)?"
)


def _generate_id() -> str:
    return secrets.token_urlsafe(16)


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class NarrationSynthesizer(Protocol):
    """朗读合成协议——T062 用真实 Qwen TTS 适配器替换默认实现。

    合同要求：只接受已通过事实锁科学校验的朗读文本。
    """

    def synthesize(self, narration: NarrationAudio) -> NarrationAudio:
        """Synthesize audio for validated narration text."""
        ...


class DeterministicNarrationSynthesizer:
    """Deterministic narration synthesizer for testing and default operation.

    Produces a stable in-memory audio reference without any model dependency.
    """

    def synthesize(self, narration: NarrationAudio) -> NarrationAudio:
        return narration.model_copy(
            update={
                "status": NarrationSynthesisStatus.SYNTHESIZED,
                "audio_ref": f"memory://narration/{narration.narration_id}.wav",
            }
        )


def _pronunciation_notes(text: str) -> list[PronunciationNote]:
    """Extract number/unit pronunciation notes from narration text."""
    notes: list[PronunciationNote] = []
    seen: set[tuple[str, PronunciationNoteKind]] = set()
    for match in _NUMBER_UNIT_PATTERN.finditer(text):
        number = match.group("number")
        if (number, PronunciationNoteKind.NUMBER) not in seen:
            seen.add((number, PronunciationNoteKind.NUMBER))
            notes.append(
                PronunciationNote(
                    token=number,
                    kind=PronunciationNoteKind.NUMBER,
                    spoken_form=number,
                )
            )
        unit = match.group("unit")
        if unit and (unit, PronunciationNoteKind.UNIT) not in seen:
            seen.add((unit, PronunciationNoteKind.UNIT))
            spoken = _UNIT_SPOKEN_FORMS.get(unit)
            notes.append(
                PronunciationNote(
                    token=unit,
                    kind=PronunciationNoteKind.UNIT,
                    spoken_form=spoken,
                    degraded=spoken is None,
                )
            )
    return notes


def _default_keyboard_paths() -> list[KeyboardAccessPath]:
    """Keyboard/screen-reader paths covering every core media task."""
    definitions: dict[str, list[tuple[str, str, str]]] = {
        "play_pause": [
            ("聚焦播放器", "Tab", "科学媒体播放器，按空格键播放或暂停。"),
            ("播放或暂停", "Space", "已切换播放状态。"),
        ],
        "seek": [
            ("聚焦进度条", "Tab", "播放进度条，使用左右方向键调整时间。"),
            ("向后或向前跳转", "ArrowLeft / ArrowRight", "已跳转到新的时间位置。"),
        ],
        "toggle_captions": [
            ("聚焦字幕按钮", "Tab", "字幕开关按钮。"),
            ("切换字幕", "Enter", "字幕已切换。"),
        ],
        "toggle_reduced_motion": [
            ("聚焦减少动画按钮", "Tab", "减少动画开关按钮。"),
            ("切换减少动画", "Enter", "减少动画模式已切换，动画以静态帧序列展示。"),
        ],
        "open_transcript": [
            ("聚焦文字稿按钮", "Tab", "完整文字稿按钮。"),
            ("打开文字稿", "Enter", "文字稿已打开，可按阅读顺序浏览全部内容。"),
        ],
    }
    paths: list[KeyboardAccessPath] = []
    for task, steps in definitions.items():
        paths.append(
            KeyboardAccessPath(
                path_id=_generate_id(),
                task=task,
                steps=[
                    KeyboardPathStep(
                        step_number=index,
                        action=action,
                        keys=keys,
                        screen_reader_announcement=announcement,
                    )
                    for index, (action, keys, announcement) in enumerate(steps, start=1)
                ],
            )
        )
    return paths


def _check_fact_locks(texts: list[str], fact_locks: list[FactLock]) -> list[str]:
    """Run the same fact-lock validation the primary content goes through."""
    errors: list[str] = []
    for lock in fact_locks:
        for text in texts:
            for forbidden in lock.forbidden_transformations:
                if forbidden and forbidden in text:
                    errors.append(
                        f"无障碍替代包含被禁止的转换 '{forbidden}'（事实锁 {lock.lock_id}）。"
                    )
        if lock.canonical_value and not any(
            lock.canonical_value in text for text in texts
        ):
            errors.append(
                f"无障碍替代丢失了锁定值 '{lock.canonical_value}'（事实锁 {lock.lock_id}）。"
            )
    return errors


class AccessibilityService:
    """生成并管理科学媒体对象的完整无障碍替代包。"""

    def __init__(
        self,
        storyboard_service: StoryboardService | None = None,
        generation_service: MediaGenerationService | None = None,
        media_ingestion_service: MediaIngestionService | None = None,
        narration_synthesizer: NarrationSynthesizer | None = None,
    ) -> None:
        self._storyboard_service = storyboard_service
        self._generation_service = generation_service
        self._media_ingestion_service = media_ingestion_service
        self._synthesizer = narration_synthesizer or DeterministicNarrationSynthesizer()
        self._bundles: dict[str, AccessibilityBundle] = {}
        self._playback_states: dict[str, PlaybackState] = {}

    # ── Bundle generation ────────────────────────────────────────────

    def generate_bundle(
        self,
        request: AccessibilityBundleRequest,
        *,
        account_id: str,
        fact_locks: list[FactLock] | None = None,
    ) -> AccessibilityBundle:
        """Generate a complete accessibility bundle for the target."""
        if request.target_kind == AccessibilityTargetKind.STORYBOARD:
            bundle = self._bundle_from_storyboard(request, account_id)
        elif request.target_kind == AccessibilityTargetKind.MEDIA_OBJECT:
            bundle = self._bundle_from_media_object(request, account_id)
        else:
            bundle = self._bundle_from_media_asset(request, account_id)

        bundle = self._validate_and_synthesize_narration(bundle, fact_locks or [])
        self._bundles[bundle.bundle_id] = bundle
        return bundle

    def get_bundle(self, bundle_id: str, *, account_id: str) -> AccessibilityBundle:
        """Retrieve a bundle scoped to the owning account."""
        bundle = self._bundles.get(bundle_id)
        if bundle is None or bundle.account_id != account_id:
            raise AccessibilityError(f"无障碍包 {bundle_id} 不存在或无权访问。")
        return bundle

    # ── Validation ───────────────────────────────────────────────────

    def validate_bundle(
        self,
        bundle_id: str,
        *,
        account_id: str,
        fact_locks: list[FactLock] | None = None,
    ) -> AccessibilityValidationResult:
        """Validate claim/version sharing, operability and science checks."""
        bundle = self.get_bundle(bundle_id, account_id=account_id)
        errors: list[str] = []
        warnings: list[str] = []

        expected_claims = set(bundle.claim_ids)
        claims_consistent = (
            set(bundle.narration.claim_ids) == expected_claims
            and set(bundle.caption_track.claim_ids) == expected_claims
            and set(bundle.transcript.claim_ids) == expected_claims
        )
        if not claims_consistent:
            errors.append("音频、字幕和文字稿未共享同一科学 Claim 集合。")

        version = bundle.source_version
        version_consistent = all(
            part == version
            for part in (
                bundle.narration.source_version,
                bundle.caption_track.source_version,
                bundle.transcript.source_version,
                bundle.reduced_motion.source_version,
                bundle.sequential_view.source_version,
            )
        )
        if not version_consistent:
            errors.append("无障碍替代未绑定同一源内容版本。")

        covered_tasks = {path.task for path in bundle.keyboard_paths}
        keyboard_operable = set(CORE_MEDIA_TASKS) <= covered_tasks and all(
            path.steps
            and all(
                step.keys.strip() and step.screen_reader_announcement.strip()
                for step in path.steps
            )
            for path in bundle.keyboard_paths
        )
        if not keyboard_operable:
            errors.append("核心媒体任务缺少完整的键盘与屏幕阅读器路径。")

        controls = bundle.playback_controls
        playback_controllable = (
            controls.can_pause
            and controls.can_seek
            and controls.reduced_motion_available
        )
        if not playback_controllable:
            errors.append("时间内容不支持暂停、时间控制或减少动画。")

        if fact_locks:
            science_errors = _check_fact_locks(
                self._alternative_texts(bundle), fact_locks
            )
            science_validated = not science_errors
            errors.extend(science_errors)
        else:
            science_validated = bundle.science_validated
            errors.extend(bundle.validation_errors)

        return AccessibilityValidationResult(
            valid=(
                claims_consistent
                and version_consistent
                and keyboard_operable
                and playback_controllable
                and science_validated
            ),
            claims_consistent=claims_consistent,
            version_consistent=version_consistent,
            keyboard_operable=keyboard_operable,
            playback_controllable=playback_controllable,
            science_validated=science_validated,
            errors=errors,
            warnings=warnings,
        )

    # ── Playback control ─────────────────────────────────────────────

    def control_playback(
        self,
        bundle_id: str,
        request: PlaybackControlRequest,
        *,
        account_id: str,
    ) -> PlaybackState:
        """Pause, resume, seek and toggle reduced motion for timed content."""
        bundle = self.get_bundle(bundle_id, account_id=account_id)
        state = self._playback_states.get(bundle_id)

        if request.action == "start" or state is None:
            state = PlaybackState(
                state_id=_generate_id(),
                bundle_id=bundle_id,
                paused=False,
                position_seconds=0.0,
                duration_seconds=bundle.narration.duration_seconds,
                updated_at=_now(),
            )
        if request.action == "pause":
            state = state.model_copy(update={"paused": True})
        elif request.action == "resume":
            state = state.model_copy(update={"paused": False})
        elif request.action == "seek":
            position = request.position_seconds
            if position is None:
                raise AccessibilityError("seek 操作缺少 position_seconds。")
            clamped = min(max(position, 0.0), state.duration_seconds)
            state = state.model_copy(update={"position_seconds": clamped})
        elif request.action == "set_reduced_motion":
            if request.enabled is None:
                raise AccessibilityError("set_reduced_motion 操作缺少 enabled。")
            state = state.model_copy(update={"reduced_motion_enabled": request.enabled})

        state = state.model_copy(
            update={
                "active_caption_text": self._caption_at(bundle, state),
                "active_frame_description": self._frame_description_at(bundle, state),
                "updated_at": _now(),
            }
        )
        self._playback_states[bundle_id] = state
        return state

    # ── Target builders ──────────────────────────────────────────────

    def _bundle_from_storyboard(
        self, request: AccessibilityBundleRequest, account_id: str
    ) -> AccessibilityBundle:
        if self._storyboard_service is None:
            raise AccessibilityError("未配置分镜服务，无法处理分镜目标。")
        try:
            storyboard = self._storyboard_service.get_storyboard(request.target_id)
        except StoryboardError as exc:
            raise AccessibilityError(str(exc)) from exc
        if storyboard.account_id != account_id:
            raise AccessibilityError(f"分镜 {request.target_id} 不存在或无权访问。")

        source_version = _sha256(storyboard.model_dump_json())[:12]
        claim_ids = self._storyboard_claim_ids(storyboard)

        segments: list[TranscriptSegment] = []
        captions: list[Caption] = []
        frames: list[ReducedMotionFrame] = []
        cursor = 0.0
        for scene in storyboard.scenes:
            start = cursor
            end = cursor + scene.timing_seconds
            cursor = end
            narration_text = scene.narration.text if scene.narration else ""
            scene_claims = self._scene_claim_ids(scene)
            text = narration_text or scene.scene_accessibility or ""
            if text:
                segments.append(
                    TranscriptSegment(
                        segment_id=f"segment-{scene.scene_id}",
                        start_time=start,
                        end_time=end,
                        text=text,
                        language=request.language,
                    )
                )
                captions.append(
                    Caption(
                        caption_id=f"caption-{scene.scene_id}",
                        start_time=start,
                        end_time=end,
                        text=text,
                        language=request.language,
                    )
                )
            description_parts = [
                part
                for part in (narration_text, scene.scene_accessibility)
                if part
            ]
            frames.append(
                ReducedMotionFrame(
                    frame_id=f"frame-{scene.scene_id}",
                    order=scene.scene_number,
                    source_ref=scene.scene_id,
                    description=" ".join(description_parts) or f"镜头 {scene.scene_number}",
                    start_time=start,
                    end_time=end,
                    claim_ids=scene_claims,
                )
            )

        blocks: list[SequentialReadingBlock] = [
            SequentialReadingBlock(order=1, role="title", text=storyboard.title)
        ]
        order = 2
        for objective in storyboard.teaching_objectives:
            blocks.append(
                SequentialReadingBlock(order=order, role="objective", text=objective)
            )
            order += 1
        for scene in storyboard.scenes:
            if scene.narration and scene.narration.text:
                blocks.append(
                    SequentialReadingBlock(
                        order=order,
                        role="scene_narration",
                        text=scene.narration.text,
                        claim_ids=list(scene.narration.claim_ids),
                    )
                )
                order += 1
            if scene.scene_accessibility:
                blocks.append(
                    SequentialReadingBlock(
                        order=order,
                        role="description",
                        text=scene.scene_accessibility,
                    )
                )
                order += 1

        alt_text = f"{storyboard.title}——{len(storyboard.scenes)} 个镜头的科学演示。"
        long_description = "；".join(
            frame.description for frame in frames
        ) or alt_text
        return self._assemble_bundle(
            request=request,
            account_id=account_id,
            source_version=source_version,
            claim_ids=claim_ids,
            alt_text=alt_text,
            long_description=long_description,
            segments=segments,
            captions=captions,
            frames=frames,
            blocks=blocks,
            reduced_motion_note="动画内容以镜头静态帧序列展示，科学内容与动画版本一致。",
        )

    def _bundle_from_media_object(
        self, request: AccessibilityBundleRequest, account_id: str
    ) -> AccessibilityBundle:
        if self._generation_service is None:
            raise AccessibilityError("未配置媒体生成服务，无法处理媒体对象目标。")
        try:
            obj = self._generation_service.get_media_object(request.target_id)
        except MediaGenerationError as exc:
            raise AccessibilityError(str(exc)) from exc
        if obj.account_id != account_id:
            raise AccessibilityError(f"媒体对象 {request.target_id} 不存在或无权访问。")

        source_version = str(obj.editable_source.version)
        claim_ids = sorted(
            {
                binding.claim_id
                for binding in obj.claim_bindings
                if binding.claim_id is not None
            }
        )
        alt_text = obj.accessibility.alt_text
        long_description = obj.accessibility.long_description or alt_text

        blocks: list[SequentialReadingBlock] = [
            SequentialReadingBlock(order=1, role="title", text=alt_text),
            SequentialReadingBlock(order=2, role="description", text=long_description),
        ]
        order = 3
        data_table = obj.accessibility.data_table
        if data_table is not None:
            for row in data_table.rows:
                row_text = "，".join(
                    self._format_cell(column.name, row.values.get(column.name), column.unit)
                    for column in data_table.columns
                )
                blocks.append(
                    SequentialReadingBlock(order=order, role="data_row", text=row_text)
                )
                order += 1

        narration_text = long_description
        segments = [
            TranscriptSegment(
                segment_id=f"segment-{obj.media_object_id}",
                start_time=0.0,
                end_time=0.0,
                text=narration_text,
                language=request.language,
            )
        ]
        captions = [
            Caption(
                caption_id=f"caption-{obj.media_object_id}",
                start_time=0.0,
                end_time=0.0,
                text=narration_text,
                language=request.language,
            )
        ]
        frames = [
            ReducedMotionFrame(
                frame_id=f"frame-{obj.media_object_id}",
                order=1,
                source_ref=obj.media_object_id,
                description=long_description,
                claim_ids=claim_ids,
            )
        ]
        return self._assemble_bundle(
            request=request,
            account_id=account_id,
            source_version=source_version,
            claim_ids=claim_ids,
            alt_text=alt_text,
            long_description=long_description,
            segments=segments,
            captions=captions,
            frames=frames,
            blocks=blocks,
            reduced_motion_note="静态媒体对象无需动画降级；提供等价数据表与描述。",
            playback_controls=PlaybackControls(
                can_pause=False,
                can_seek=False,
                captions_available=False,
                reduced_motion_available=False,
            ),
        )

    def _bundle_from_media_asset(
        self, request: AccessibilityBundleRequest, account_id: str
    ) -> AccessibilityBundle:
        if self._media_ingestion_service is None:
            raise AccessibilityError("未配置媒体摄入服务，无法处理媒体资产目标。")
        try:
            projection = self._media_ingestion_service.get_asset(
                account_id, request.target_id
            )
        except MediaError as exc:
            raise AccessibilityError(str(exc)) from exc

        source_version = str(projection.manifest.version)
        claim_ids = sorted(
            {
                binding.claim_id
                for binding in projection.manifest.claim_bindings
                if binding.claim_id
            }
        )

        segments: list[TranscriptSegment] = []
        captions: list[Caption] = []
        for derived in projection.derived_assets:
            try:
                payload = AudioVideoDerivedData(**derived.payload)
            except (TypeError, ValueError):
                continue
            if payload.transcript_segments or payload.captions:
                segments = list(payload.transcript_segments)
                captions = list(payload.captions)
                break
        if not segments:
            raise AccessibilityError(
                f"媒体资产 {request.target_id} 没有可用的文字稿，无法生成无障碍替代。"
            )

        frames = [
            ReducedMotionFrame(
                frame_id=f"frame-{segment.segment_id}",
                order=index,
                source_ref=segment.segment_id,
                description=segment.text,
                start_time=segment.start_time,
                end_time=segment.end_time,
                claim_ids=list(claim_ids),
            )
            for index, segment in enumerate(segments, start=1)
        ]
        filename = projection.source_asset.original_filename
        blocks: list[SequentialReadingBlock] = [
            SequentialReadingBlock(order=1, role="title", text=filename)
        ]
        for index, segment in enumerate(segments, start=2):
            blocks.append(
                SequentialReadingBlock(
                    order=index, role="scene_narration", text=segment.text
                )
            )

        alt_text = f"{filename} 的无障碍替代内容。"
        long_description = "；".join(segment.text for segment in segments)
        return self._assemble_bundle(
            request=request,
            account_id=account_id,
            source_version=source_version,
            claim_ids=claim_ids,
            alt_text=alt_text,
            long_description=long_description,
            segments=segments,
            captions=captions,
            frames=frames,
            blocks=blocks,
            reduced_motion_note="时间内容以句段静态帧展示。",
        )

    # ── Assembly and helpers ─────────────────────────────────────────

    def _assemble_bundle(
        self,
        *,
        request: AccessibilityBundleRequest,
        account_id: str,
        source_version: str,
        claim_ids: list[str],
        alt_text: str,
        long_description: str,
        segments: list[TranscriptSegment],
        captions: list[Caption],
        frames: list[ReducedMotionFrame],
        blocks: list[SequentialReadingBlock],
        reduced_motion_note: str,
        playback_controls: PlaybackControls | None = None,
    ) -> AccessibilityBundle:
        now = _now()
        full_text = "\n".join(segment.text for segment in segments)
        duration = max((segment.end_time for segment in segments), default=0.0)

        transcript = Transcript(
            transcript_id=_generate_id(),
            language=request.language,
            segments=segments,
            full_text=full_text,
            claim_ids=list(claim_ids),
            source_version=source_version,
        )
        caption_track = CaptionTrack(
            track_id=_generate_id(),
            language=request.language,
            captions=captions,
            claim_ids=list(claim_ids),
            source_version=source_version,
        )
        narration = NarrationAudio(
            narration_id=_generate_id(),
            language=request.language,
            text=full_text,
            status=NarrationSynthesisStatus.PENDING_SYNTHESIS,
            duration_seconds=duration,
            timing=[
                NarrationTimingEntry(
                    segment_id=segment.segment_id,
                    text=segment.text,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                )
                for segment in segments
            ],
            pronunciation_notes=_pronunciation_notes(full_text),
            claim_ids=list(claim_ids),
            source_version=source_version,
        )
        reduced_motion = ReducedMotionVariant(
            variant_id=_generate_id(),
            frames=frames,
            note=reduced_motion_note,
            claim_ids=list(claim_ids),
            source_version=source_version,
        )
        sequential_view = SequentialReadingView(
            view_id=_generate_id(),
            blocks=blocks,
            claim_ids=list(claim_ids),
            source_version=source_version,
        )
        return AccessibilityBundle(
            bundle_id=_generate_id(),
            account_id=account_id,
            project_id=request.project_id,
            target_kind=request.target_kind,
            target_id=request.target_id,
            source_version=source_version,
            language=request.language,
            claim_ids=list(claim_ids),
            alt_text=alt_text,
            long_description=long_description,
            transcript=transcript,
            caption_track=caption_track,
            narration=narration,
            keyboard_paths=_default_keyboard_paths(),
            playback_controls=playback_controls or PlaybackControls(),
            reduced_motion=reduced_motion,
            sequential_view=sequential_view,
            created_at=now,
            updated_at=now,
        )

    def _validate_and_synthesize_narration(
        self, bundle: AccessibilityBundle, fact_locks: list[FactLock]
    ) -> AccessibilityBundle:
        """Run fact-lock validation and synthesise narration on success.

        Narration is only synthesized once the text passes validation — an
        unvalidated alternative must not become a synthesized answer.
        """
        errors = _check_fact_locks(self._alternative_texts(bundle), fact_locks)
        if errors:
            return bundle.model_copy(
                update={
                    "science_validated": False,
                    "validation_errors": errors,
                    "updated_at": _now(),
                }
            )
        narration = self._synthesizer.synthesize(bundle.narration)
        return bundle.model_copy(
            update={
                "science_validated": True,
                "validation_errors": [],
                "narration": narration,
                "updated_at": _now(),
            }
        )

    def _alternative_texts(self, bundle: AccessibilityBundle) -> list[str]:
        captions_text = "".join(c.text for c in bundle.caption_track.captions)
        sequential_text = "\n".join(b.text for b in bundle.sequential_view.blocks)
        frames_text = "\n".join(f.description for f in bundle.reduced_motion.frames)
        return [
            bundle.transcript.full_text,
            captions_text,
            bundle.narration.text,
            sequential_text,
            frames_text,
        ]

    def _caption_at(self, bundle: AccessibilityBundle, state: PlaybackState) -> str | None:
        captions = bundle.caption_track.captions
        position = state.position_seconds
        for caption in captions:
            if caption.start_time <= position < caption.end_time:
                return caption.text
        if captions and position >= state.duration_seconds > 0.0:
            return captions[-1].text
        return None

    def _frame_description_at(
        self, bundle: AccessibilityBundle, state: PlaybackState
    ) -> str | None:
        if not state.reduced_motion_enabled:
            return None
        frames = bundle.reduced_motion.frames
        position = state.position_seconds
        for frame in frames:
            if frame.start_time <= position < frame.end_time:
                return frame.description
        if frames:
            return frames[-1].description
        return None

    @staticmethod
    def _format_cell(name: str, value: object, unit: str | None) -> str:
        rendered = "" if value is None else str(value)
        return f"{name} {rendered}{f' {unit}' if unit else ''}"

    @staticmethod
    def _storyboard_claim_ids(storyboard: MediaStoryboard) -> list[str]:
        claims: set[str] = set()
        for scene in storyboard.scenes:
            claims.update(AccessibilityService._scene_claim_ids(scene))
        return sorted(claims)

    @staticmethod
    def _scene_claim_ids(scene: StoryboardScene) -> list[str]:
        claims: set[str] = set()
        if scene.narration is not None:
            claims.update(scene.narration.claim_ids)
        for binding in scene.scene_claim_bindings:
            if binding.claim_id:
                claims.add(binding.claim_id)
        return sorted(claims)


__all__ = [
    "CORE_MEDIA_TASKS",
    "AccessibilityError",
    "AccessibilityService",
    "DeterministicNarrationSynthesizer",
    "NarrationSynthesizer",
]
