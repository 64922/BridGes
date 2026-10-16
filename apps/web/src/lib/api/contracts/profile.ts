import type { components } from "@bridges/contracts";

/** Issue 14：展开期四维画像的最小用户投影。内部来源、哈希和迁移字段不在页面渲染。 */
export type FourDimension = components["schemas"]["FourDimension"];

export type FourDimensionRecordStatus = components["schemas"]["FourDimensionRecordStatus"];

export type FourDimensionConfidence = components["schemas"]["FourDimensionConfidence"];

export type FourDimensionProfileRecord =
  components["schemas"]["FourDimensionProfileProjection"];

export type FourDimensionProfileModifyRequest =
  components["schemas"]["FourDimensionProfileModifyRequest"];

export type FourDimensionProfileDeleteRequest =
  components["schemas"]["FourDimensionProfileDeleteRequest"];

// V2 Issue 08：无固定类别的原子长期信息列表；投影不含类别、去重键与账户字段。
export type AtomicProfileItemProjection =
  components["schemas"]["AtomicProfileItemProjection"];

export type AtomicProfileItemCreateRequest =
  components["schemas"]["AtomicProfileItemCreateRequest"];

export type AtomicProfileItemModifyRequest =
  components["schemas"]["AtomicProfileItemModifyRequest"];

export type AtomicProfileWriteOrigin =
  components["schemas"]["AtomicProfileWriteOrigin"];

// 改进工单 20：按需展开的依据/时效投影与四类反馈合同。
export type AtomicProfileItemEvidenceProjection =
  components["schemas"]["AtomicProfileItemEvidenceProjection"];

export type AtomicProfileEvidenceSource =
  components["schemas"]["AtomicProfileEvidenceSource"];

export type AtomicProfileEvidenceSourceStatus =
  components["schemas"]["AtomicProfileEvidenceSourceStatus"];

export type AtomicProfileValidityStatus =
  components["schemas"]["AtomicProfileValidityStatus"];

// 画像工单 07：明示周期安排（每周三晚上…）的结构化投影。
export type AtomicProfileRecurrence = components["schemas"]["AtomicProfileRecurrence"];

export type AtomicProfileFeedbackProjection =
  components["schemas"]["AtomicProfileFeedbackProjection"];

export type AtomicProfileFeedbackKind =
  components["schemas"]["AtomicProfileFeedbackKind"];

export type AtomicProfileItemFeedbackRequest =
  components["schemas"]["AtomicProfileItemFeedbackRequest"];

// 改进工单 07：账户级画像记录/使用控制（两个开关互相独立）。
export type ProfileAccountControlsProjection =
  components["schemas"]["ProfileAccountControlsProjection"];

// 画像工单 13：进行中事项投影与暂停/完成/继续、领域引用管理。
export type OngoingItemsProjection =
  components["schemas"]["OngoingItemsProjection"];

export type OngoingItemProjection =
  components["schemas"]["OngoingItemProjection"];

export type OngoingEntryProjection =
  components["schemas"]["OngoingEntryProjection"];

export type OngoingGoalAction = components["schemas"]["OngoingGoalAction"];

export type OngoingItemStateUpdateRequest =
  components["schemas"]["OngoingItemStateUpdateRequest"];

export type OngoingDomainReferenceUpdateRequest =
  components["schemas"]["OngoingDomainReferenceUpdateRequest"];

// 画像工单 14：记忆页学习引用区（学习领域持有、这里只读引用）。
export type LearningReferencesProjection =
  components["schemas"]["LearningReferencesProjection"];

export type LearningGoalReference =
  components["schemas"]["LearningGoalReference"];

export type LearningEvidenceReference =
  components["schemas"]["LearningEvidenceReference"];

// 画像工单 15：整体概览（逐段携带条目标识与版本，点击可定位依据）。
export type ProfileOverviewProjection =
  components["schemas"]["ProfileOverviewProjection"];

export type ProfileOverviewState =
  components["schemas"]["ProfileOverviewState"];

export type ProfileOverviewStatementProjection =
  components["schemas"]["ProfileOverviewStatementProjection"];

export type ProfileOverviewSegmentProjection =
  components["schemas"]["ProfileOverviewSegmentProjection"];
