"""工单 43 覆盖对账：上下文 22 项（C-D1–C-D9、C-10–C-22）。

来源：``docs/上下文工程/`` 讨论记录、改进方案、审查与复核脚本；验收落点
指向工单 03/04/08/11/13/14/15/19 的边界测试与工单 40 的真实配对报告。
40 的独立性/连续性量表为两侧同分（非劣方向），按同模型同预算配对记录。
"""

from __future__ import annotations

from bridges.evaluation.integration_coverage_types import CoverageItem, CoverageStatus

_40_ACCEPTANCE = ".scratch/2/validation/40-context-continuity/independent-acceptance.md"

CONTEXT_COVERAGE: tuple[CoverageItem, ...] = (
    CoverageItem(
        "C-D1",
        "同账户同会话连续性，保留底座，不扩跨会话记忆或新工作台",
        (1, 8, 40),
        ("tests/chat/test_improvement40_continuity_corpus.py", _40_ACCEPTANCE),
        CoverageStatus.VERIFIED,
        "工单 40 独立验收通过；真实多轮配对同模型同预算。",
    ),
    CoverageItem(
        "C-D2",
        "用户明示直接生效，助手草案/模型推测不晋升事实，工具事实独立来源",
        (8, 13),
        ("tests/chat/test_improvement12_task_relations.py",
         "tests/chat/test_improvement13_summary_cache.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-D3",
        "默认任务范围、明示扩会话、话题往返恢复最新值、模块切换非换任务",
        (8, 11, 12),
        ("tests/chat/test_improvement11_reference_resolution.py",
         "tests/chat/test_improvement12_task_relations.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-D4",
        "按需模型结构化摘要与缓存；近期原文和关键纠正来源，原文重建",
        (13,),
        ("tests/chat/test_improvement13_summary_cache.py",
         ".scratch/2/acceptance/13-bounded-summary-cache.md"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-D5",
        "后台为主/必要限时补齐，预算压力非固定轮数，最新请求/纠正不等待",
        (9, 13),
        ("tests/chat/test_improvement13_summary_cache.py",
         "tests/chat/test_improvement09_run_budget_ledger.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-D6",
        "唯一对象续接、实质歧义一个问题，不说用户从未讲过",
        (11, 12),
        ("tests/chat/test_improvement11_reference_resolution.py",
         "tests/chat/test_improvement12_hybrid_entry.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-D7",
        "完整条件优先、任务必要性裁剪、可核验有界分批否则明确缩小范围",
        (4, 9, 40),
        ("tests/chat/test_improvement04_payload_budget.py",
         "tests/chat/test_improvement40_context_boundaries.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-D8",
        "旧图/文件/模块按原始对象版本读取；摘要只定位，不自动刷新外部",
        (14, 15),
        ("tests/chat/test_improvement14_material_reads.py",
         "tests/chat/test_improvement15_module_acceptance.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-D9",
        "预算/隔离/来源/快照硬门，真实多轮同模型同预算比较与成本",
        (40,),
        (_40_ACCEPTANCE,),
        CoverageStatus.VERIFIED,
        "36/36 检查点、六维量表通过；调用/token/延迟配对已归档。",
    ),
    CoverageItem(
        "C-10",
        "最终载荷包含所有后插材料、角色/图片/工具与输出预留，每调用重查",
        (3, 4, 14, 15, 24, 26, 30, 32, 33, 34, 36),
        ("tests/chat/test_improvement04_payload_budget.py",
         "tests/paper/test_paper_issue24.py",
         "tests/chat/test_improvement40_context_boundaries.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-11",
        "121 条逐行摘要无法收敛、末尾约束缺失，状态/背景摘要分离",
        (8, 13, 40),
        ("tests/chat/test_improvement13_summary_cache.py",
         "tests/chat/test_improvement40_continuity_corpus.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-12",
        "无引号中文回指/“继续”、近期对象误报、回补相邻纠正轮",
        (11,),
        ("tests/chat/test_improvement11_reference_resolution.py",
         ".scratch/2/acceptance/11-reference-resolution-and-original-recovery.md"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-13",
        "照片绕过 null、历史原图按需读取、数量输入方式共同预算",
        (4, 14),
        ("tests/chat/test_improvement14_material_reads.py",
         "tests/chat/test_improvement04_payload_budget.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-14",
        "旧自定义模型快照完整，未知窗口不以任意 32,768 冒充已验证",
        (3,),
        ("tests/chat/test_improvement03_model_quota.py",
         ".scratch/2/acceptance/04-final-payload-budget-and-data-boundary.md"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-15",
        "模块不各取最近 6 条，理解/工作两阶段最小上下文，每调用自己额度与清单",
        (15, 24, 25, 26, 27, 28, 30, 32),
        ("tests/chat/test_improvement15_module_acceptance.py",
         "tests/chat/test_improvement15_manifest_acceptance.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-16",
        "知识库/附件/画像用解析主题，公开工具仅最小公共查询",
        (15, 19, 32, 37),
        ("tests/chat/test_improvement15_task_materials.py",
         "tests/retrieval/test_v2_07_knowledge_base_retrieval.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-17",
        "画像 1 token 首条例外与 80 字截断消除，必要排除项整条保留",
        (4, 19),
        ("tests/chat/test_improvement04_payload_budget.py",
         "tests/chat/test_improvement19_purpose_aware_slice.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-18",
        "数据与指令分层，固定渲染次序、新纠正权威，不仅靠提示词管权限",
        (4, 8, 12),
        ("tests/chat/test_improvement04_payload_budget.py",
         "tests/chat/test_improvement12_acceptance.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-19",
        "实际消息/片段/对象 ID、来源版本、摘要实例、采用/排除/预算与用量",
        (3, 4, 13, 15, 40),
        ("tests/chat/test_improvement15_manifest_acceptance.py",
         "tests/chat/test_improvement40_context_boundaries.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-20",
        "任务预算、估算余量、摘要阈值/长度/超时/重试、分批/召回/重复次数实测",
        (9, 13, 40),
        ("tests/chat/test_improvement09_run_budget_ledger.py",
         _40_ACCEPTANCE),
        CoverageStatus.VERIFIED,
        "40 真实配对记录调用/token/墙钟；摘要阈值保持编译内满足。",
    ),
    CoverageItem(
        "C-21",
        "中文/英文/代码/公式/长 URL/图片估算校准，窗口非填满目标",
        (4, 40),
        ("tests/chat/test_improvement40_context_boundaries.py",
         "tests/chat/test_improvement04_payload_budget.py"),
        CoverageStatus.MECHANISM,
    ),
    CoverageItem(
        "C-22",
        "来源删除/失效衍生缓存不用；画像删除不自行改聊天原文删除语义",
        (13, 14, 18, 43),
        ("tests/invalidation/test_invalidation_integration.py",
         "tests/profiles/test_issue18_profile_validity_and_revocation.py",
         "tests/retrieval/test_v2_07_knowledge_base_retrieval.py"),
        CoverageStatus.MECHANISM,
    ),
)

__all__ = ["CONTEXT_COVERAGE"]
