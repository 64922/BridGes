"""工单 42：39 个固定工作流场景的可执行证据清单。

数据来源：``docs/workflow/delivery-and-validation.md`` §3 的 A01–A18、
L01–L13、R01–R08。每个场景必须绑定至少一条**可执行**的确定性 pytest
节点；跨外部服务的能力另绑定 :class:`ExternalGate`，由最小真实探针报告
（``external_probes``）给出实际可得层次，未通过的能力保持如实降级。

本模块只登记证据，不执行测试；执行与报告分别由
``scripts/run_issue42_workflow_evidence.py`` 与 ``workflow_evidence.py`` 完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExternalGate(StrEnum):
    """需要真实外部服务探测的上线门（工单 42 任务 6）。"""

    ARXIV_FULL_TEXT = "arxiv.full_text"
    AMAP_CAMPUS_ROUTES = "amap.campus_routes"
    TIEBA_REPLIES = "tieba.replies"
    PUBLIC_JOBS = "jobs.public_detail"
    VIDEO_INTRO = "video.intro"
    GITHUB_FILES = "github.files"
    MODEL_CAPABILITIES = "model.configured_capabilities"
    WEB_SEARCH = "web_search.tavily"


class EvidenceLayer(StrEnum):
    """场景证据的类型；真实模型与外部探针分开报告。"""

    DETERMINISTIC = "deterministic"
    FAULT_INJECTION = "fault_injection"
    REAL_MODEL = "real_model"
    EXTERNAL_PROBE = "external_probe"


class ZeroTolerance(StrEnum):
    """验收标准中零容忍的缺陷类型。"""

    CROSS_ACCOUNT = "cross_account"
    HARD_CONDITION_BYPASS = "hard_condition_bypass"
    SYSTEM_FAILURE_AS_ERROR = "system_failure_as_student_error"
    DUPLICATE_JUDGEMENT = "duplicate_judgement"
    WRITE_AFTER_STOP = "write_after_stop"


@dataclass(frozen=True)
class Scenario:
    """一个固定场景及其证据绑定。"""

    scenario_id: str
    title: str
    expectation: str
    deterministic_tests: tuple[str, ...] = ()
    fault_injection_tests: tuple[str, ...] = ()
    real_model: bool = False
    external_gates: tuple[ExternalGate, ...] = ()
    zero_tolerance: tuple[ZeroTolerance, ...] = ()
    availability_note: str = ""

    @property
    def tests(self) -> tuple[str, ...]:
        return self.deterministic_tests + self.fault_injection_tests

    @property
    def kind(self) -> str:
        return self.scenario_id[0]

    @property
    def layers(self) -> tuple[EvidenceLayer, ...]:
        found: list[EvidenceLayer] = []
        if self.deterministic_tests:
            found.append(EvidenceLayer.DETERMINISTIC)
        if self.fault_injection_tests:
            found.append(EvidenceLayer.FAULT_INJECTION)
        if self.real_model:
            found.append(EvidenceLayer.REAL_MODEL)
        if self.external_gates:
            found.append(EvidenceLayer.EXTERNAL_PROBE)
        return tuple(found)


@dataclass(frozen=True)
class ZeroToleranceGuard:
    """零容忍项的独立守卫：必须由真实执行过的测试证明。"""

    kind: ZeroTolerance
    description: str
    tests: tuple[str, ...]


_F = "tests/chat/test_improvement35_study_pause_and_appended_versions.py"

WORKFLOW_SCENARIOS: tuple[Scenario, ...] = (
    # -- A：日常编排与模块 ------------------------------------------------
    Scenario(
        scenario_id="A01",
        title="普通问候/陪伴",
        expectation="轻量回答，无不必要专业调用和复杂任务",
        deterministic_tests=(
            "tests/chat/test_improvement09_run_budget_ledger.py::test_budget_class_derivation",
            "tests/retrieval/test_decision.py::test_companion_greeting_skips_global_knowledge_base",
            "tests/chat/test_improvement12_hybrid_entry.py::test_ordinary_chat_creates_no_task_relation",
            "tests/chat/test_chat_lightweight_policy.py::test_ordinary_generation_uses_exactly_one_model_call",
        ),
        real_model=True,
    ),
    Scenario(
        scenario_id="A02",
        title="保留论文提示，正文明确问校内通勤",
        expectation="按通勤路由；只问真正缺少的地点/方式，历史标识不改",
        deterministic_tests=(
            "tests/evaluation/test_issue42_scenario_gaps.py::test_a02_paper_hint_with_commute_body_routes_commute",
            "tests/evaluation/test_issue42_scenario_gaps.py::test_a02_service_keeps_requested_hint_and_uses_actual_route",
            "tests/chat/test_improvement12_hybrid_entry.py::test_body_intent_overrides_module_hint",
        ),
        real_model=True,
    ),
    Scenario(
        scenario_id="A03",
        title="孤立歧义主题，前文无法消歧",
        expectation="问一个必要问题，不替换主题、不先查猜测领域",
        deterministic_tests=(
            "tests/paper/test_paper_module_core.py::test_isolated_transformer_asks_one_clarification",
            "tests/paper/test_paper_module_flow.py::test_body_intent_ambiguous_term_dispatches_and_asks_one_question",
            "tests/chat/test_natural_language_paper_route.py::test_ambiguous_paper_request_asks_before_any_side_effect",
        ),
        real_model=True,
    ),
    Scenario(
        scenario_id="A04",
        title="查入门论文和学习资料",
        expectation="共享目标，独立部分可并行；一个总预算",
        deterministic_tests=(
            "tests/orchestration/test_issue37_composite_engine.py::test_paper_resources_run_in_parallel_with_one_shared_budget",
            "tests/chat/test_improvement12_hybrid_entry.py::test_registered_composite_freezes_one_normal_budget",
            "tests/chat/test_issue37_composite_dispatch.py::test_composite_dispatch_commits_all_projections_once",
        ),
        real_model=True,
        external_gates=(ExternalGate.ARXIV_FULL_TEXT, ExternalGate.VIDEO_INTRO),
        availability_note="论文按已验证读取层交付；视频仅交付核对过的公开元数据",
    ),
    Scenario(
        scenario_id="A05",
        title="查选中论文的对应实现",
        expectation="以论文标识为依赖；身份未确认时不声称仓库对应",
        deterministic_tests=(
            "tests/orchestration/test_issue37_composite_engine.py::test_github_cannot_claim_correspondence_when_paper_identity_unconfirmed",
            "tests/github/test_github_module_flow.py::test_reference_to_prior_paper_turn_uses_its_original_phrase",
        ),
        real_model=True,
        external_gates=(ExternalGate.GITHUB_FILES,),
        availability_note="身份未确认时只交付限定条件下的线索，不声称对应",
    ),
    Scenario(
        scenario_id="A06",
        title="求职城市从上海改杭州",
        expectation="新版本使旧城市样本/统计失效，背景和无关有效产物复用",
        deterministic_tests=(
            "tests/career_plan/test_career_plan_module_flow.py::test_city_change_starts_a_new_revision_without_reusing_old_city_stats",
            "tests/orchestration/test_issue37_composite_engine.py::test_city_change_recomputes_career_but_reuses_resources",
            "tests/chat/test_issue37_composite_reuse.py::test_city_change_reruns_career_and_reuses_resources",
            "tests/chat/test_improvement12_hybrid_entry.py::test_city_revision_updates_task_version_and_condition",
        ),
        real_model=True,
    ),
    Scenario(
        scenario_id="A07",
        title="澄清未答时用户换话题，稍后说“好的”",
        expectation="不把新消息填进旧澄清；需要唯一任务归属",
        deterministic_tests=(
            "tests/tasks/test_task_state.py::test_topic_switch_does_not_fill_old_wait_and_cancel_releases",
            "tests/chat/test_improvement12_hybrid_entry.py::test_meta_reply_does_not_fill_open_wait",
            "tests/chat/test_improvement12_task_relations.py::test_unrelated_or_stale_wait_does_not_consume_answer",
            "tests/chat/test_improvement12_task_relations.py::test_profile_or_new_topic_does_not_answer_wait",
        ),
        real_model=True,
    ),
    Scenario(
        scenario_id="A08",
        title="多个历史任务都可能被“继续那个”指代",
        expectation="澄清一次，不仅凭最近模块强行恢复",
        deterministic_tests=(
            "tests/evaluation/test_issue42_scenario_gaps.py::test_a08_generic_continue_with_multiple_tasks_asks_one_clarification",
            "tests/evaluation/test_issue42_scenario_gaps.py::test_a08_unique_task_term_resolves_continue_without_clarification",
        ),
        real_model=True,
    ),
    Scenario(
        scenario_id="A09",
        title="资料视频来源失败，书目仍可核验",
        expectation="交付有效书目及范围；不凑视频、不整体抹掉书目",
        deterministic_tests=(
            "tests/evaluation/test_issue42_scenario_gaps.py::test_a09_video_timeout_keeps_books_and_reports_real_gap",
            "tests/resources/test_resources_module_core.py::test_organize_keeps_one_side_when_other_has_gap",
            "tests/resources/test_resources_module_flow.py::test_book_gap_is_limited_to_its_own_source",
        ),
        external_gates=(ExternalGate.VIDEO_INTRO, ExternalGate.WEB_SEARCH),
        availability_note="视频发现/元数据未通过时只交付书目与真实缺口",
    ),
    Scenario(
        scenario_id="A10",
        title="候选使用不同术语，但证据支持需求；另一个只命中词",
        expectation="前者可通过语义匹配，后者因无覆盖证据被排除",
        deterministic_tests=(
            "tests/paper/test_paper_issue24.py::test_synonym_evidence_passes_without_literal_primary_term",
            "tests/paper/test_paper_issue24.py::test_primary_keyword_only_false_positive_is_excluded",
            "tests/paper/test_paper_issue24_acceptance.py::test_synonym_evidence_survives_evaluate",
            "tests/paper/test_paper_issue24_acceptance.py::test_unrelated_survey_is_not_topic_evidence",
        ),
        real_model=True,
        external_gates=(ExternalGate.ARXIV_FULL_TEXT,),
        availability_note="未读全文时匹配结论只基于已读证据，不声称深读",
    ),
    Scenario(
        scenario_id="A11",
        title="用户限定年份/城市/不联网",
        expectation="语义扩展和补证不放宽这些条件",
        deterministic_tests=(
            "tests/paper/test_paper_issue24.py::test_year_hard_condition_blocks_instead_of_widening",
            "tests/paper/test_paper_issue24_acceptance.py::test_excluded_arxiv_source_blocks_before_any_search",
            "tests/chat/test_improvement12_acceptance.py::test_direct_paper_stream_does_not_call_network_when_forbidden",
            "tests/resources/test_issue25_lifecycle_acceptance.py::test_explicit_network_prohibition_blocks_all_resources_sources",
        ),
        real_model=True,
        zero_tolerance=(ZeroTolerance.HARD_CONDITION_BYPASS,),
    ),
    Scenario(
        scenario_id="A12",
        title="通勤某方式无真实路线",
        expectation="不用其他方式耗时替代，不生成猜测地图",
        deterministic_tests=(
            "tests/commute/test_commute_sources.py::test_route_without_any_plan_is_honest_failure_for_that_mode",
            "tests/commute/test_commute_sources.py::test_each_mode_uses_its_own_endpoint_and_its_own_duration",
            "tests/commute/test_commute_module_flow.py::test_missing_path_points_marks_unverified_and_draws_nothing",
        ),
        external_gates=(ExternalGate.AMAP_CAMPUS_ROUTES,),
        availability_note="某方式无路线时按方式如实失败，不拿别的耗时或猜测地图替代",
    ),
    Scenario(
        scenario_id="A13",
        title="高峰前后 10 分钟边界",
        expectation="按 Asia/Shanghai 一致计算，5 分钟缓冲标为规则",
        deterministic_tests=(
            "tests/commute/test_commute_buffer.py::test_ten_minute_boundaries_are_inclusive",
            "tests/commute/test_commute_buffer.py::test_checked_at_is_reported_in_shanghai_time",
            "tests/commute/test_commute_buffer.py::test_notes_say_this_is_a_rule_not_realtime_crowd_data",
        ),
        external_gates=(ExternalGate.AMAP_CAMPUS_ROUTES,),
        availability_note="高峰缓冲按规则展示，不冒充实时人流数据",
    ),
    Scenario(
        scenario_id="A14",
        title="贴吧只有标题/搜索摘要",
        expectation="只交付线索，未读回复不总结，不称普遍共识",
        deterministic_tests=(
            "tests/tieba/test_improvement27_question_driven.py::test_links_only_never_summarizes_unread_replies",
            "tests/tieba/test_tieba_core.py::test_sections_only_use_read_replies_and_cite_floor_and_time",
            "tests/tieba/test_tieba_module_flow.py::test_snippet_claiming_target_forum_still_needs_a_real_read",
        ),
        real_model=True,
        external_gates=(ExternalGate.TIEBA_REPLIES, ExternalGate.WEB_SEARCH),
        availability_note="实际回复读取受限时只交付帖链，不总结未读回复",
    ),
    Scenario(
        scenario_id="A15",
        title="官方规定与帖子经历冲突",
        expectation="按日期/范围核对，仍冲突时分别呈现",
        deterministic_tests=(
            "tests/tieba/test_improvement27_question_driven.py::test_conflict_with_newer_official_rule_uses_time_and_scope_basis",
            "tests/tieba/test_improvement27_question_driven.py::test_conflict_without_supersession_keeps_both_sides",
            "tests/tieba/test_issue27_acceptance_regressions.py::test_no_proven_supersession_keeps_both",
        ),
        external_gates=(ExternalGate.TIEBA_REPLIES, ExternalGate.WEB_SEARCH),
        availability_note="官方页与帖子回复都读不到时不裁决冲突，如实分列",
    ),
    Scenario(
        scenario_id="A16",
        title="无用户技能背景的岗位查询",
        expectation="正常交付岗位分析，未知技能不判为不足",
        deterministic_tests=(
            "tests/career_plan/test_issue29_personal_career_gap.py::test_personal_request_without_profile_delivers_job_part_and_boundary",
            "tests/career_plan/test_issue29_personal_career_gap.py::test_job_only_request_never_touches_profile_source",
            "tests/career_plan/test_career_plan_module_flow.py::test_plain_chat_explicit_job_query_starts_career_without_profile",
        ),
        real_model=True,
        external_gates=(ExternalGate.PUBLIC_JOBS, ExternalGate.WEB_SEARCH),
        availability_note="岗位页不可读时只给未核实链接，不推断要求",
    ),
    Scenario(
        scenario_id="A17",
        title="GitHub 可选功能很多，必要功能未支持",
        expectation="不因覆盖比例或 stars 判为整体适配",
        deterministic_tests=(
            "tests/github/test_github_requirement_matrix.py::test_required_unsupported_is_not_whole_even_with_ratio_stars_and_optionals",
            "tests/github/test_github_requirement_matrix.py::test_optional_rows_are_listed_but_do_not_offset_required_gaps",
            "tests/github/test_github_core.py::test_stars_are_only_a_tie_break_between_equal_matches",
        ),
        real_model=True,
        external_gates=(ExternalGate.GITHUB_FILES,),
        availability_note="GitHub 读取受限时只基于已读文件下结论，不整体背书",
    ),
    Scenario(
        scenario_id="A18",
        title="README 有架构说明但未读实现、未运行",
        expectation="区分文档自述、静态证据与运行未验证",
        deterministic_tests=(
            "tests/github/test_github_core.py::test_render_states_evidence_boundary_and_never_claims_architecture",
            "tests/github/test_github_acceptance_evidence.py::test_requested_implementation_is_not_satisfied_by_readme",
            "tests/github/test_github_requirement_matrix.py::test_runtime_requirement_is_never_reported_as_runnable",
        ),
        real_model=True,
        external_gates=(ExternalGate.GITHUB_FILES,),
        availability_note="README 只当自述；未运行的项目不标可运行",
    ),
    # -- L：学习复盘 -------------------------------------------------------
    Scenario(
        scenario_id="L01",
        title="书页关键负号/上标不清",
        expectation="定位补拍/补录，依赖该符号的出题被阻塞",
        deterministic_tests=(
            "tests/chat/test_improvement30_study_pages_recognition.py::test_critical_doubt_requests_exact_position_and_blocks_questions",
            "tests/chat/test_improvement30_study_pages_recognition.py::test_position_level_critical_doubt_is_not_downgraded",
            "tests/chat/test_improvement30_study_pages_recognition.py::test_dual_path_mismatch_is_critical_even_with_high_confidence",
            "tests/chat/test_improvement32_study_tutoring_evidence.py::test_targeted_unclear_page_asks_for_retake_without_external_replacement",
        ),
        external_gates=(ExternalGate.MODEL_CAPABILITIES,),
        availability_note="图片识别能力不足或疑点未清时要求补拍/补录，不出题",
    ),
    Scenario(
        scenario_id="L02",
        title="不同知识点同名、多个页含不同公式",
        expectation="用稳定 ID 与片段定位，不串题或评分依据",
        deterministic_tests=(
            "tests/chat/test_improvement31_study_scope_preview.py::test_same_title_on_different_pages_keeps_distinct_ids_and_evidence",
            "tests/chat/test_improvement33_preverified_questions.py::test_same_title_different_pages_keep_question_evidence_separate",
            "tests/chat/test_improvement31_study_scope_preview.py::test_formula_conflict_blocks_until_repaired_content",
        ),
    ),
    Scenario(
        scenario_id="L03",
        title="书页足以回答",
        expectation="不自动联网；必要上下文保留",
        deterministic_tests=(
            "tests/chat/test_improvement32_study_tutoring_evidence.py::test_sufficient_pages_do_not_search_knowledge_base_or_web",
        ),
    ),
    Scenario(
        scenario_id="L04",
        title="书页不足，知识库/公网其中一路失败",
        expectation="交付能支持的部分，缺口真实；不伪装外部来源",
        deterministic_tests=(
            "tests/chat/test_improvement32_study_tutoring_evidence.py::test_page_only_delivery_without_review_or_scope_expansion",
            "tests/chat/test_improvement32_study_tutoring_evidence.py::test_disabled_knowledge_base_and_no_network_keep_gaps_unverified",
            "tests/chat/test_improvement32_study_tutoring_evidence.py::test_conflicting_public_sources_are_listed_not_merged",
            "tests/chat/test_improvement32_study_tutoring_evidence.py::test_assessment_cannot_fabricate_support_sources",
        ),
        external_gates=(ExternalGate.WEB_SEARCH,),
        availability_note="公网一路失败只交付书页可支持部分，缺口标未核实",
    ),
    Scenario(
        scenario_id="L05",
        title="“还没学完”或引用“开始复盘”",
        expectation="不触发复盘；语义动作仍通过代码阶段门",
        deterministic_tests=(
            "tests/chat/test_improvement12_hybrid_entry.py::test_review_stage_gate_negation_quote_and_pause",
            "tests/chat/test_v2_19_study_review.py::test_only_explicit_request_starts_review",
            "tests/chat/test_improvement36_evidence_bound_summary.py::test_tutoring_without_review_never_generates_summary",
        ),
    ),
    Scenario(
        scenario_id="L06",
        title="学生使用等价表述/推导",
        expectation="按预先要点判定；争议复核，不因措辞不同判错",
        deterministic_tests=(
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_equivalent_answer_passes_recheck_and_frozen_standard_does_not_drift",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_disputed_recheck_keeps_question_without_student_error",
        ),
        real_model=True,
    ),
    Scenario(
        scenario_id="L07",
        title="批改调用失败或判定结构不合法",
        expectation="不推进题号、不将系统失败记为学生错误",
        fault_injection_tests=(
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_grade_failure_keeps_question_then_retry_commits_once",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_judgement_and_next_question_have_separate_commit_boundaries",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_late_lease_loss_rejects_grade_without_advancing",
        ),
        real_model=True,
        zero_tolerance=(ZeroTolerance.SYSTEM_FAILURE_AS_ERROR,),
    ),
    Scenario(
        scenario_id="L08",
        title="暂停复盘后要求讲解，随后继续",
        expectation="讲解不作为答案；已问题保留，默认继续未问题",
        deterministic_tests=(
            "tests/chat/test_improvement35_study_pause_and_appended_versions.py::test_pause_is_not_graded_and_resume_defaults_to_next_unasked",
            "tests/chat/test_v2_19_study_review.py::test_pause_tutor_and_resume_keep_asked_questions",
        ),
    ),
    Scenario(
        scenario_id="L09",
        title="本节追加书页识别失败后重试",
        expectation="原有效范围保留，复用未变页；有效更新后只重排未问题",
        deterministic_tests=(
            "tests/chat/test_improvement35_study_pause_and_appended_versions.py::test_append_after_summary_versions_history_and_preview_questions",
            "tests/chat/test_v2_19_study_review.py::test_append_pages_replans_only_unasked_and_preserves_grades",
        ),
        fault_injection_tests=(
            "tests/chat/test_improvement35_study_pause_and_appended_versions.py::test_append_failure_keeps_effective_scope_and_reuses_recognized_pages",
            "tests/chat/test_improvement35_study_pause_and_appended_versions.py::test_stop_during_append_keeps_effective_state_and_recovers",
        ),
    ),
    Scenario(
        scenario_id="L10",
        title="最后一题判定已提交，总结失败",
        expectation="判定/反馈保留，只重试总结，未完成总结不称全流程完成",
        fault_injection_tests=(
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_last_judgement_survives_summary_failure_and_retry_does_not_regrade",
            "tests/chat/test_improvement36_evidence_bound_summary.py::test_summary_failure_persists_pending_and_retries_only_summary",
        ),
    ),
    Scenario(
        scenario_id="L11",
        title="原题已判错后继续复盘",
        expectation="给反馈后继续基础计划，不自动追加补救题",
        deterministic_tests=(
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_wrong_feedback_continues_frozen_plan_without_remediation",
            "tests/chat/test_improvement36_evidence_bound_summary.py::test_summary_creates_no_followup_work_and_no_profile_fact_from_summary",
        ),
    ),
    Scenario(
        scenario_id="L12",
        title="直接读取会话/重放事件",
        expectation="未来题和未到反馈阶段的评分要点/标准答案不泄露",
        deterministic_tests=(
            "tests/chat/test_improvement33_preverified_questions.py::test_plan_freezes_private_rubric_and_reads_never_leak_it",
            "tests/chat/test_improvement33_acceptance.py::test_failed_verification_hides_private_details_in_api_and_replay",
            "tests/chat/test_improvement33_acceptance.py::test_plan_receipt_records_registered_calculation_without_replay_leak",
        ),
    ),
    Scenario(
        scenario_id="L13",
        title="总结存在未答题或未判定题",
        expectation="单独描述，不能计为掌握或错答",
        deterministic_tests=(
            "tests/chat/test_improvement36_evidence_bound_summary.py::test_summary_separates_mastery_gaps_and_unanswered_with_real_evidence",
            "tests/chat/test_improvement36_evidence_bound_summary.py::test_unanswered_must_be_listed_separately",
            _F + "::test_unanswered_question_never_counts_as_mastered",
            "tests/chat/test_improvement36_status_acceptance.py::test_pending_text_comes_from_actual_state",
        ),
    ),
    # -- R：韧性、恢复与权限 ------------------------------------------------
    Scenario(
        scenario_id="R01",
        title="同一请求重试、刷新/SSE 断线",
        expectation="不重复消息、附件或判定；事件按游标可重放",
        deterministic_tests=(
            "tests/chat/test_v2_02_resumable_runs.py::test_send_idempotency_replays_same_run_without_duplicates",
            "tests/chat/test_v2_02_resumable_runs.py::test_retry_idempotency_reuses_run",
            "tests/chat/test_issue06_stream_replay_consistency.py::test_disconnect_cursor_replay_reconstructs_body",
            "tests/chat/test_chat_attachments.py::test_upload_uses_content_sniffing_and_retries_idempotently",
        ),
        fault_injection_tests=(
            "tests/chat/test_v2_02_resumable_runs.py::test_send_idempotency_replays_same_run_without_duplicates",
        ),
        zero_tolerance=(ZeroTolerance.DUPLICATE_JUDGEMENT,),
    ),
    Scenario(
        scenario_id="R02",
        title="节点产物已提交，图检查点尚未保存时进程失败",
        expectation="从完成收据恢复，已完成本地效果不重复",
        fault_injection_tests=(
            "tests/chat/test_improvement30_study_pages_recognition.py::test_retry_recovers_committed_pages_from_receipts",
            "tests/chat/test_terminal_recovery_and_replay.py::test_message_commit_window_recovery_repairs_without_second_model_call",
            "tests/chat/test_study_recognition_failures.py::test_restart_after_partial_failure_recovers_without_duplicating_pages",
        ),
    ),
    Scenario(
        scenario_id="R03",
        title="旧租约执行者晚返回，或用户已经停止",
        expectation="拒绝旧尝试/取消后的提交，不覆盖新版本",
        fault_injection_tests=(
            "tests/chat/test_improvement30_study_pages_recognition.py::test_lease_transfer_rejects_stale_commit",
            "tests/chat/test_improvement30_study_pages_recognition.py::test_stop_during_recognition_does_not_commit_material",
            "tests/chat/test_improvement35_acceptance.py::test_append_final_transaction_rejects_late_authority_change",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_late_lease_loss_rejects_grade_without_advancing",
        ),
        zero_tolerance=(ZeroTolerance.WRITE_AFTER_STOP,),
    ),
    Scenario(
        scenario_id="R04",
        title="工具限流或多节点重试",
        expectation="预算/计数不重置，有限重试，真实恢复时刻才展示",
        fault_injection_tests=(
            "tests/chat/test_improvement09_run_budget_ledger.py::test_lease_recovery_reloads_ledger_without_reset",
            "tests/chat/test_improvement09_run_budget_ledger.py::test_transient_retry_capped_once_per_registered_call",
            "tests/web_search/test_issue03_retry_matrix.py::test_attempts_match_actual_http_calls_on_consecutive_failures",
            "tests/arxiv_mcp/test_retry_stale_warmup.py::test_stale_serve_after_persistent_timeout_with_annotation",
        ),
    ),
    Scenario(
        scenario_id="R05",
        title="材料/画像撤回后恢复旧运行",
        expectation="重新检查访问与依赖有效性，不重新召回被撤回切片",
        deterministic_tests=(
            "tests/invalidation/test_invalidation_integration.py::test_revoke_between_submit_and_confirm_blocks_start",
            "tests/retrieval/test_v2_07_knowledge_base_retrieval.py::test_deleted_material_is_not_recallable_but_citation_reports_deleted",
            "tests/chat/test_improvement14_material_reads.py::test_deleted_old_photo_yields_gap_not_description",
            "tests/chat/test_improvement11_reference_resolution.py::test_revoked_value_is_not_revived_and_reported_as_gap",
        ),
    ),
    Scenario(
        scenario_id="R06",
        title="不兼容配方版本的历史运行",
        expectation="安全结束或迁移到明确新运行，不盲目套新图继续",
        deterministic_tests=(
            "tests/resources/test_issue25_lifecycle_acceptance.py::test_resources_resume_without_calls_and_reject_old_recipe",
            "tests/chat/test_improvement33_acceptance.py::test_old_graph_run_is_rejected_then_explicit_retry_uses_new_verification",
            "tests/chat/test_improvement35_acceptance.py::test_previous_graph_cannot_replay_pre_versioned_update",
            "tests/kernel/test_node_kernel.py::test_contract_upgrade_reruns_nodes_and_preserves_old_receipts",
        ),
    ),
    Scenario(
        scenario_id="R07",
        title="用户停止、稍后明确继续",
        expectation="停止不自动续跑；继续创建新运行并复用有效产物",
        deterministic_tests=(
            "tests/evaluation/test_issue42_scenario_gaps.py::test_r07_stop_has_no_auto_continue_and_explicit_continue_reuses_task",
            "tests/chat/test_improvement09_run_budget_ledger.py::test_new_budget_run_for_explicit_continue",
        ),
        fault_injection_tests=(
            "tests/chat/test_improvement12_hybrid_entry.py::test_pause_continue_and_cancel_transitions",
            "tests/chat/test_improvement12_hybrid_entry.py::test_stop_generation_pauses_current_task",
        ),
        real_model=True,
        zero_tolerance=(ZeroTolerance.WRITE_AFTER_STOP,),
    ),
    Scenario(
        scenario_id="R08",
        title="模型建议调用未登记/退役能力或切换模式",
        expectation="代码拒绝，不能绕过 API/模式/能力边界",
        deterministic_tests=(
            "tests/workflows/test_workflow_capability_integration.py::test_unregistered_capability_blocks_run",
            "tests/ai/test_model_gateway.py::test_unregistered_capability_is_blocked",
            "tests/ai/test_model_gateway.py::test_disabled_capability_is_blocked",
            "tests/closeout/test_v2_21_horizontal_regression.py::test_first_turn_locks_mode_and_switching_is_retired",
        ),
    ),
)


ZERO_TOLERANCE_GUARDS: tuple[ZeroToleranceGuard, ...] = (
    ZeroToleranceGuard(
        kind=ZeroTolerance.CROSS_ACCOUNT,
        description="任何工作流对象在同一标识下不得跨账户读写",
        tests=(
            "tests/chat/test_chat_service.py::test_get_conversation_account_isolation",
            "tests/chat/test_improvement04_payload_budget.py::test_cross_account_material_is_not_recallable_through_the_gate",
            "tests/chat/test_improvement11_reference_resolution.py::test_cross_account_material_is_not_recallable",
            "tests/chat/test_improvement15_task_materials.py::test_task_queries_do_not_cross_account_scope",
            "tests/chat/test_v2_02_resumable_runs.py::test_checkpoint_run_event_isolation_across_accounts",
            "tests/chat/test_v2_03_conversation_context.py::test_cross_account_history_not_recallable",
            "tests/tasks/test_task_state.py::test_account_isolation_hides_other_account_tasks",
            "tests/kernel/test_node_kernel.py::test_account_isolation_for_artifacts",
        ),
    ),
    ZeroToleranceGuard(
        kind=ZeroTolerance.HARD_CONDITION_BYPASS,
        description="用户限定的年份/城市/不联网等硬条件不得被语义扩展或补证放宽",
        tests=(
            "tests/paper/test_paper_issue24.py::test_year_hard_condition_blocks_instead_of_widening",
            "tests/paper/test_paper_issue24_acceptance.py::test_excluded_arxiv_source_blocks_before_any_search",
            "tests/chat/test_improvement12_acceptance.py::test_direct_paper_stream_does_not_call_network_when_forbidden",
            "tests/resources/test_issue25_lifecycle_acceptance.py::test_explicit_network_prohibition_blocks_all_resources_sources",
        ),
    ),
    ZeroToleranceGuard(
        kind=ZeroTolerance.SYSTEM_FAILURE_AS_ERROR,
        description="批改/调用系统失败不得推进题号或记为学生错答",
        tests=(
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_grade_failure_keeps_question_then_retry_commits_once",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_judgement_and_next_question_have_separate_commit_boundaries",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_late_lease_loss_rejects_grade_without_advancing",
        ),
    ),
    ZeroToleranceGuard(
        kind=ZeroTolerance.DUPLICATE_JUDGEMENT,
        description="同一请求重试/断线不得重复消息、附件或判定",
        tests=(
            "tests/chat/test_v2_02_resumable_runs.py::test_send_idempotency_replays_same_run_without_duplicates",
            "tests/chat/test_v2_02_resumable_runs.py::test_retry_idempotency_reuses_run",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_grade_failure_keeps_question_then_retry_commits_once",
        ),
    ),
    ZeroToleranceGuard(
        kind=ZeroTolerance.WRITE_AFTER_STOP,
        description="停止或租约转移后迟到提交必须被拒绝，不得覆盖新版本",
        tests=(
            "tests/chat/test_improvement30_study_pages_recognition.py::test_stop_during_recognition_does_not_commit_material",
            "tests/chat/test_improvement30_study_pages_recognition.py::test_lease_transfer_rejects_stale_commit",
            "tests/chat/test_improvement35_acceptance.py::test_append_final_transaction_rejects_late_authority_change",
            "tests/chat/test_improvement34_verified_grading_feedback.py::test_late_lease_loss_rejects_grade_without_advancing",
            "tests/evaluation/test_issue42_scenario_gaps.py::test_r07_stop_has_no_auto_continue_and_explicit_continue_reuses_task",
            "tests/chat/test_improvement12_hybrid_entry.py::test_stop_generation_pauses_current_task",
        ),
    ),
)

SCENARIO_BY_ID: dict[str, Scenario] = {
    scenario.scenario_id: scenario for scenario in WORKFLOW_SCENARIOS
}

REAL_MODEL_PAIRING_COVERAGE: tuple[str, ...] = (
    "A01",
    "A02",
    "A03",
    "A11",
    "R07",
)


def expected_scenario_ids() -> tuple[str, ...]:
    """39 个固定场景编号（A18 + L13 + R8）。"""
    return tuple(
        [f"A{index:02d}" for index in range(1, 19)]
        + [f"L{index:02d}" for index in range(1, 14)]
        + [f"R{index:02d}" for index in range(1, 9)]
    )


def validate_workflow_scenarios() -> list[str]:
    """结构性校验；返回问题列表，空列表表示清单可用。"""
    problems: list[str] = []
    expected = set(expected_scenario_ids())
    actual = {scenario.scenario_id for scenario in WORKFLOW_SCENARIOS}
    for missing in sorted(expected - actual):
        problems.append(f"缺少场景：{missing}")
    for unknown in sorted(actual - expected):
        problems.append(f"未登记的场景编号：{unknown}")
    if len(WORKFLOW_SCENARIOS) != len(expected):
        problems.append(
            f"场景总数应为 {len(expected)}，实际 {len(WORKFLOW_SCENARIOS)}。"
        )
    if len(SCENARIO_BY_ID) != len(WORKFLOW_SCENARIOS):
        problems.append("场景编号存在重复。")

    for scenario in WORKFLOW_SCENARIOS:
        if not scenario.tests:
            problems.append(f"{scenario.scenario_id} 没有任何可执行测试证据。")
        for node in scenario.tests:
            if "::" not in node or not node.startswith("tests/"):
                problems.append(
                    f"{scenario.scenario_id} 的测试节点格式非法：{node}"
                )
        for flag in scenario.zero_tolerance:
            guard = _guard_for(flag)
            if guard is None:
                problems.append(
                    f"{scenario.scenario_id} 引用了未知零容忍项：{flag}"
                )
            elif not set(scenario.tests) & set(guard.tests):
                problems.append(
                    f"{scenario.scenario_id} 标记零容忍 {flag} 但没有对应守卫测试。"
                )
    for scenario_id in REAL_MODEL_PAIRING_COVERAGE:
        pairing_scenario = SCENARIO_BY_ID.get(scenario_id)
        if pairing_scenario is None:
            problems.append(f"真实模型配对覆盖引用了不存在的场景：{scenario_id}")
        elif not pairing_scenario.real_model:
            problems.append(f"真实模型配对场景 {scenario_id} 未标记 real_model。")
    return problems


def _guard_for(kind: ZeroTolerance) -> ZeroToleranceGuard | None:
    for guard in ZERO_TOLERANCE_GUARDS:
        if guard.kind is kind:
            return guard
    return None


__all__ = [
    "EvidenceLayer",
    "ExternalGate",
    "REAL_MODEL_PAIRING_COVERAGE",
    "SCENARIO_BY_ID",
    "Scenario",
    "WORKFLOW_SCENARIOS",
    "ZERO_TOLERANCE_GUARDS",
    "ZeroTolerance",
    "ZeroToleranceGuard",
    "expected_scenario_ids",
    "validate_workflow_scenarios",
]
