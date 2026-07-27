---
name: t031-completed
description: T031 摄入并校正音频与视频材料完成
metadata:
  type: project
---

# T031 完成：摄入并校正音频与视频材料

## 工作内容

- **合同扩展：** 在 `MediaType` 中添加 `AUDIO_MPEG`/`AUDIO_WAV`/`AUDIO_OGG`/`VIDEO_MP4`/`VIDEO_WEBM`/`VIDEO_OGG`
- **合同扩展：** `MediaAssetKind` 添加 `audio_transcript`/`audio_segment`/`video_keyframe`/`caption_track`
- **合同扩展：** `MediaCorrectionType` 添加 `transcript_term`/`speaker_segment`/`caption_text`/`keyframe_interpretation`
- **数据模型：** `TranscriptWord` / `TranscriptSegment` / `SpeakerSegment` / `Caption` / `CaptionTrack` / `Keyframe` / `AudioVideoDerivedData`
- **提取器实现：** `AudioVideoExtractor` — 确定性音视频解析，含时间轴转录、说话段、字幕、关键帧、低置信科学术语注入、缺失音轨标记、多语言检测
- **能力注册：** `qwen_asr_short` / `qwen_asr_long` ASR 能力注册到能力注册表
- **人工修正：** `TRANSCRIPT_TERM` / `SPEAKER_SEGMENT` / `CAPTION_TEXT` / `KEYFRAME_INTERPRETATION` 四种修正类型，修正后清理词级对齐
- **失效集成：** 媒体资产撤权时通过 `build_media_impact_resolver` 传播失效事件
- **API 路由：** 上传（个人/项目）、获取投影、修正、撤权、ClaimGraph 生成
- **测试：** 11 个单元测试 + 4 个集成测试，覆盖上传、低置信注入、缺失音轨、视频关键帧、4 种修正、跨账户隔离和异常魔术数字门

## 阻塞项验证

- T013（文本与 PDF 科学来源）：已完成并提交，媒体输入质量门和 SourceAsset/DerivedAsset 模式已复用
- T009（Qwen 能力注册表与模型运行锁）：已完成并提交，ASR 能力记录和 ModelRunLock 已正确使用

## Bug 修复细节

1. **词级对齐未在转录术语修正后更新** (`service.py:532-544`)：`TRANSCRIPT_TERM` 修正后，`TranscriptSegment.words` 仍保留 ASR 错误拼写的词级对齐。修复：在修正段中清除 `words=[]`，因为人工修正后词级对齐不再有效。
2. **`_build_asr_run_lock` 未标记为 @staticmethod** (`extraction.py:786`)：方法不使用 `self`，标记为 `@staticmethod`。

## 代码审查结论

- 双轴审查通过（Standards：无硬性违规；Spec：4 项验收项全部实现）
- 映射实现与项目目标一致
- 529/530 测试通过（1 个预先存在的 flaky 时序测试）

**Why:** 音视频材料是科学证据链的重要组成部分，用户需要导入讲座录音、科学演讲等以支持学习使命。
**How to apply:** 后续 T034（无障碍替代）将消费音视频转录和字幕数据生成朗读/字幕/完整无障碍替代。
