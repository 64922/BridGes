"""工单 43 覆盖对账：人味化 19 项（H-D1–H-D6、H-07–H-19）。

来源：``docs/人味化/`` 讨论记录、实施方案、审查与复核脚本；验收落点分别
指向工单 21/22/23 的确定性接线测试与工单 39 的真实配对/盲评记录。人味
表达收益因人工盲评票数不足保持 ``not_released``，不因合并改为通过。
"""

from __future__ import annotations

from bridges.evaluation.integration_coverage_types import CoverageItem, CoverageStatus

_39_ACCEPTANCE = ".scratch/2/validation/39-independent/acceptance.md"

EXPRESSION_COVERAGE: tuple[CoverageItem, ...] = (
    CoverageItem(
        "H-D1",
        "有分寸伙伴：自然交流、按语境调整主动度",
        (21, 39),
        ("tests/chat/test_improvement21_contextual_expression.py",
         "tests/evaluation/test_issue39_human_expression.py"),
        CoverageStatus.MECHANISM,
        "接线与非劣硬门有机制证据；真人收益因 39 未放行不宣称提升。",
    ),
    CoverageItem(
        "H-D2",
        "所有用户可见自然语言（模块/固定/澄清/错误/进度）纳入策略或模板",
        (23, 24, 25, 26, 27, 28, 30, 31, 32, 33, 34, 36, 38),
        ("tests/state_copy/test_issue23_state_copy_registry.py"
         "::test_registry_validates_and_lists_every_required_category",
         "tests/state_copy/test_issue23_state_copy_wiring.py",
         "tests/evaluation/test_issue43_natural_language_adoption.py",
         "tests/evaluation/test_issue39_evidence_completion.py"
         "::test_fixed_copy_receipt_renders_all_templates"),
        CoverageStatus.MECHANISM,
        "注册表 216 条覆盖方向与六模块/学习状态（43 审计接缝测试复核）；"
        "图片/视频/语音与离线邮件等非生成来源未登记，作为已记录缺口保留"
        "（43 报告 R3）。",
    ),
    CoverageItem(
        "H-D3",
        "原生成调用接入，零人味专属追加；确定性模板不全文模型重写",
        (21, 23, 39),
        ("tests/evaluation/test_issue39_evidence_completion.py",
         "tests/evaluation/test_issue39_human_expression.py"),
        CoverageStatus.MECHANISM,
        "39 部署限额已按基线实测通过（新增调用 0）；自然度结论仍不放行。",
    ),
    CoverageItem(
        "H-D4",
        "相关时自然采用明确偏好与背景，不刻意展示记忆、不推断人格/情绪",
        (19, 22, 39),
        ("tests/chat/test_improvement22_unified_profile_expression.py",
         "tests/chat/test_improvement19_purpose_aware_slice.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-D5",
        "倾诉先具体承接、可陪聊/轻问、不强制建议，不固定选择题",
        (21, 39),
        ("tests/chat/test_improvement21_contextual_expression.py",),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-D6",
        "帮助与分寸不退化，再验证自然度；聊天时长/温暖词不算收益",
        (39,),
        (_39_ACCEPTANCE,),
        CoverageStatus.NOT_RELEASED,
        "人工硬门 4 项失败、帮助/分寸有效票 6/3 与 7/4（<10），不开放。",
    ),
    CoverageItem(
        "H-07",
        "弱形态路由处理否定、引语、话题、混合任务、长文和“继续”",
        (11, 21, 39),
        ("tests/chat/test_improvement11_reference_resolution.py",
         "tests/chat/test_improvement21_contextual_expression.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-08",
        "去掉解释必问与情绪必行动；允许有依据不同意见、不迎合错误/自贬",
        (21, 39),
        ("tests/chat/test_improvement21_contextual_expression.py",),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-09",
        "dimension 空值、旧白名单、无画像矛盾及通用偏好无词面匹配",
        (19, 22),
        ("tests/chat/test_improvement19_purpose_aware_slice.py",
         "tests/profiles/test_chat_slice_compiler.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-10",
        "真实工具结果/失败/策略拒答进入正式链，未发生拒答不可假定",
        (21, 23, 38),
        ("tests/chat/test_improvement21_contextual_expression.py",
         "tests/state_copy/test_issue23_state_copy_wiring.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-11",
        "逐条核对正式图分派；内部排名/分析不强行加人格；学习复用完整快照",
        (22, 24, 26, 30, 31, 32, 33, 34, 36, 38),
        ("tests/chat/test_improvement22_unified_profile_expression.py",
         "tests/paper/test_paper_issue24.py",
         "tests/github/test_github_acceptance_insights.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-12",
        "逐一对象绑定、授权纠错、合法链接、非必需遗漏不补尾",
        (5,),
        ("tests/chat/test_issue05_intent_bound_fact_protection.py",
         ".scratch/2/acceptance/05-intent-bound-fact-protection.md"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-13",
        "裸数字/中文单位、否定/条件/因果/结论强度有语义参考",
        (5, 37, 39),
        ("tests/chat/test_issue05_intent_bound_fact_protection.py",
         "tests/orchestration/test_issue37_verified_synthesis.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-14",
        "delta 追加语义、片段跨 chunk、替换/缓冲选一种，UI/事件/落库一致",
        (6, 38),
        ("tests/chat/test_issue06_stream_replay_consistency.py",
         "tests/chat/test_issue38_turn_result.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-15",
        "学习保真夹具在合法书页阶段正式回归",
        (30, 32),
        ("tests/chat/test_improvement30_study_pages_recognition.py",
         "tests/chat/test_improvement32_study_tutoring_evidence.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-16",
        "策略版本/规则/快照/重试/降级；提示回滚与确定性修复解耦",
        (3, 5, 6, 21, 22, 43),
        ("tests/chat/test_global_writing_policy.py",
         "tests/chat/test_chat_lightweight_policy.py",
         "src/bridges/evaluation/rollback_rehearsal.py"),
        CoverageStatus.MECHANISM,
        "回滚演练由本票补齐：安全基线回退不删除画像数据，确定性保护不在"
        "提示策略回滚范围内。",
    ),
    CoverageItem(
        "H-17",
        "输出 max_tokens 与真实长度任务/额度联动，不无界扩大或机械短答",
        (3, 4, 21),
        ("tests/chat/test_improvement03_model_quota.py",
         "tests/chat/test_improvement04_payload_budget.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "H-18",
        "旧/新/简洁三基线，多轮随机盲评、平局/拒选、五量表、消融、成本",
        (39,),
        (_39_ACCEPTANCE,
         "tests/evaluation/test_issue39_blind_review.py"),
        CoverageStatus.NOT_RELEASED,
        "机制与三项真实消融完成；人工有效票不足，不放行也不宣称提升。",
    ),
    CoverageItem(
        "H-19",
        "原创净室、退役文章能力不恢复、授权/原创评测数据与日志保护",
        (1, 5, 21, 39, 43),
        ("tests/evaluation/test_issue39_human_expression.py",
         "tests/integration/test_expression_retirement.py",
         "tests/retirement/test_legacy_generation_exit.py"),
        CoverageStatus.MECHANISM,
    ),
)

__all__ = ["EXPRESSION_COVERAGE"]
