"""用户可见固定文案目录与错误模板（Issue 23）。

目录分为三部分：

- **公共状态文案**：本票直接渲染并接入的澄清、进度、等待/停止、空结果、
  部分成功/降级模板，由状态或真实结果确定性选择；
- **错误模板**：稳定错误码 → 固定中文模板 + 真实失败类别 + 真实恢复方式，
  未读取、不支持、未配置、限流/超时与不可核实分开，不伪装成功；
- **模块/学习清单**：六模块与学习流程的路径登记，指向各领域已有的确定性
  渲染器或既有生成调用中的适配点，领域票据 24–36 按实际状态在此复核。

本模块只承载文案与登记，不发起模型调用，也不改写机器字段（路线、引用、
价格、时间、岗位、题目状态仍由各自渲染器从真实结果生成）。
"""

from __future__ import annotations

from bridges.state_copy.types import (
    CopyCategory,
    CopyEntry,
    CopyStrategy,
    ErrorTemplate,
    FailureClass,
    RecoveryAction,
)

# ---------------------------------------------------------------------------
# 公共状态文案（本注册表可直接渲染）
# ---------------------------------------------------------------------------

CLARIFICATION_MULTIPLE_TASKS_TEMPLATE = (
    "这条消息包含多个任务（{names}）；本轮先执行哪一个？"
)
CLARIFICATION_TASK_AMBIGUITY_TEXT = "找到多个同样合理的任务，需要确认是哪一个。"
CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT = "找到多个历史任务，需要确认继续哪一个。"
CLARIFICATION_REFERENCE_AMBIGUITY_TEXT = "指代的对象不唯一，需要确认。"
ROUTE_CLARIFY_PAPER_TOPIC_TEXT = "你想查哪一主题的论文？"
ROUTE_CLARIFY_PAPER_QUERY_PARTS_TEXT = (
    "你想查哪一主题、作者、标题或 arXiv 标识符？"
)
ROUTE_CLARIFY_MULTIPLE_PAPER_OR_OTHER_TEXT = (
    "这条消息包含多个任务；你想先执行论文搜索，还是先完成其他任务？"
)
ROUTE_CLARIFY_MULTIPLE_TEXT_CAPABILITIES_TEXT = (
    "这条消息包含多个独立任务，本轮先做哪一项？"
)

GRAPH_NODE_FAILURE_TEMPLATE = "在「{label}」步骤失败：{message}{recovery}"
GRAPH_RECOVERY_RETRY_TEXT = "可点击重试。"
GRAPH_RECOVERY_ADJUST_TEXT = "请调整后重试。"
GRAPH_RECOVERY_RETRY_PLAIN_TEXT = "请重试。"
GRAPH_RECOVERY_WAIT_TEXT = "请稍后重试。"
GRAPH_RECOVERY_RECONFIGURE_TEXT = "请联系管理员。"

THINKING_DONE_QUALITY_TEXT = "回答已完整生成并保存"
THINKING_FAILED_FALLBACK_TEXT = "生成失败，已保留已完成部分。"
THINKING_STOPPED_QUALITY_TEXT = "已停止生成，保留已生成内容。"
THINKING_BUDGET_WARNING_TEXT = (
    "回答已交付（受时延预算限制，内容可能不完整）；重试可获得更完整回答。"
)

WAIT_GENERATION_IN_PROGRESS_TEXT = "上一轮回答仍在生成中，请先停止或等待完成。"
WAIT_DELETE_IN_PROGRESS_TEXT = "回答仍在生成中，请先停止后再删除。"

STREAM_INTERRUPTED_TEXT = "连接中断，已保留已接收内容，可点击重试。"
USER_STOPPED_TEXT = "生成已停止。"
STUDY_STOPPED_TEXT = "学习处理已停止。"
ROUTE_REJECTED_FALLBACK_TEXT = "请求未通过参数校验，请调整后重试。"

RETRIEVAL_ATTACHMENTS_PENDING_TEXT = "附件仍在处理中或暂无可检索内容。"
RETRIEVAL_KNOWLEDGE_BASE_NOT_READY_TEXT = "知识库暂无已就绪材料。"
RETRIEVAL_SUFFICIENT_TEXT = "已检索到足够的本地材料。"
RETRIEVAL_NO_HITS_TEXT = "没有找到与问题相关的本地材料。"
RETRIEVAL_CONFLICT_TEXT = "检索到的候选来源存在冲突，结果可能不确定。"
RETRIEVAL_INSUFFICIENT_COVERAGE_TEXT = "检索到的本地材料覆盖不足。"
RETRIEVAL_INDEX_UNAVAILABLE_TEXT = "本地索引不可用，暂无法检索本地材料，请稍后重试。"
RETRIEVAL_VECTOR_UNAVAILABLE_TEXT = "向量检索暂不可用，本轮仅使用关键词检索。"
RETRIEVAL_PROJECT_RETIRED_TEXT = "学习项目文件来源已退役。"

WEB_SEARCH_PROVIDER_UNREADY_TEXT = (
    "联网服务异常，正在尝试联网；失败将进入模型知识降级。"
)
WEB_SEARCH_CANCELLED_TEXT = "已取消本轮联网搜索。"
WEB_SEARCH_STAGE_TIMEOUT_TEXT = "公网搜索阶段超时，未形成有效投影，请重试。"
WEB_SEARCH_INTERNAL_TEXT = "公网搜索服务发生内部异常，请重试。"

#: 检索充足性枚举值 → 登记路径（文案在注册表中保持单源）。
RETRIEVAL_SUFFICIENCY_PATHS: dict[str, str] = {
    "sufficient": "retrieval.status.sufficient",
    "no_hits": "retrieval.empty.no_hits",
    "conflict": "retrieval.partial.conflict",
    "insufficient_coverage": "retrieval.partial.insufficient_coverage",
    "index_unavailable": "retrieval.degradation.index_unavailable",
}

# ---------------------------------------------------------------------------
# 错误模板：稳定码 → 固定中文模板 + 真实失败类别 + 真实恢复方式
# ---------------------------------------------------------------------------


def _error(
    code: str,
    text: str,
    failure_class: FailureClass,
    recovery: RecoveryAction,
    *,
    shared: bool = False,
    contextual: bool = False,
) -> ErrorTemplate:
    return ErrorTemplate(
        code=code,
        text=text,
        failure_class=failure_class,
        recovery=recovery,
        shared=shared,
        contextual=contextual,
    )


ERROR_TEMPLATES: tuple[ErrorTemplate, ...] = (
    _error("study_graph_version_changed", "学习流程版本已更新，历史已保留，请重试。",
           FailureClass.STATE_CONFLICT, RecoveryAction.RETRY, contextual=True),
    # 工单 31：保留领域核验详情，登记真实错误类别与恢复动作。
    _error("study_map_invalid", "知识范围映射结构不完整，请重试。",
           FailureClass.INTERNAL, RecoveryAction.RETRY, contextual=True),
    _error("study_scope_incomplete", "知识范围结构核验未通过，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_scope_content_conflict", "关键内容与书页原文冲突，未通过核验，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_scope_content_unverified", "关键内容无法核实，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_preview_incomplete", "预习问题未通过覆盖核验，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_preview_budget", "预习范围超出上下文预算，请缩小范围后重试。",
           FailureClass.UNSUPPORTED, RecoveryAction.ADJUST_REQUEST, contextual=True),
    _error("study_scope_missing", "有效知识范围缺失，请重试。",
           FailureClass.INTERNAL, RecoveryAction.RETRY, contextual=True),
    _error("study_preview_missing", "预习问题产物缺失，请重试。",
           FailureClass.INTERNAL, RecoveryAction.RETRY, contextual=True),
    _error("study_scope_failed", "知识范围核验未通过，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    # 工单 33：出题前冻结与核验的失败码；保留领域核验详情与真实恢复方式。
    _error("study_review_plan_incomplete", "复盘计划未覆盖或结构不完整，原阶段已保留，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_review_verify_conflict", "题目或评分要点与书页冲突，未通过出题前核验，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_review_verify_unverified", "题目或评分要点无法核实，未通过出题前核验，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_review_calculation", "题目数值结论与登记计算工具复算不一致，未出题，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_review_budget", "复盘计划超出上下文预算，请缩小本节范围后重试。",
           FailureClass.UNSUPPORTED, RecoveryAction.ADJUST_REQUEST, contextual=True),
    _error("study_review_invalid", "复盘判定或结果未通过核验，原题已保留，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    # 工单 34：作答判定的失败码；保留当前题，不推进游标、不记学生错答。
    _error("study_grade_invalid", "判定结果未通过核验，当前题已保留，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_review_disputed", "判定存在未解决的争议，当前题已保留，请重试或先继续辅导。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_recheck_failed", "必要复核未能完成，当前题已保留，请重试。",
           FailureClass.UNVERIFIABLE, RecoveryAction.RETRY, contextual=True),
    _error("study_grade_budget", "判定超出上下文预算，当前题已保留，请重试。",
           FailureClass.UNSUPPORTED, RecoveryAction.RETRY, contextual=True),
    _error("study_review_scope_changed", "题目版本与当前复盘范围不一致，请重新开始复盘。",
           FailureClass.STATE_CONFLICT, RecoveryAction.REFRESH_STATE, contextual=True),
    # -- 聊天领域码 --------------------------------------------------------
    _error(
        "generation_worker_lost",
        "生成进程意外退出，已保留已接收内容，可点击重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "stream_interrupted",
        STREAM_INTERRUPTED_TEXT,
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "unregistered_capability",
        "核心对话能力未就绪，请联系管理员。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "capability_not_verified",
        "核心对话能力未通过验证，请检查启动服务的全局百炼配置与权限。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "no_adapter",
        "核心对话能力未就绪（缺少适配器），请检查服务配置。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "internal_error",
        "生成过程出现内部错误，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_timeout",
        "联网搜索超时，请重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_rate_limit",
        "公网搜索请求过于频繁，请稍后重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.WAIT,
    ),
    _error(
        "web_search_offline",
        "当前无法连接公网搜索，请检查网络后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_connect",
        "当前无法连接公网搜索，请检查网络后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_dns",
        "无法解析公网搜索地址，请检查 DNS 或网络后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_provider",
        "公网搜索提供方暂时不可用，请稍后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.WAIT,
    ),
    _error(
        "web_search_permission",
        "当前网络未允许访问公网搜索，请检查网络权限后重试。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "web_search_parse",
        "搜索结果暂时无法解析，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_request",
        "公网搜索请求未完成，请重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_fallback_credentials",
        "备用公网搜索缺少部署凭据，请联系管理员。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "web_search_fallback_timeout",
        "备用公网搜索超时，请稍后重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.WAIT,
    ),
    _error(
        "web_search_fallback_rate_limit",
        "备用公网搜索请求过于频繁，请稍后重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.WAIT,
    ),
    _error(
        "web_search_fallback_dns",
        "当前无法连接备用公网搜索，请稍后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_fallback_offline",
        "当前无法连接备用公网搜索，请稍后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_fallback_connect",
        "当前无法连接备用公网搜索，请稍后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_fallback_permission",
        "备用公网搜索凭据或访问权限无效，请检查部署配置。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "web_search_fallback_parse",
        "备用公网搜索返回内容损坏，无法解析。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_fallback_request",
        "备用公网搜索请求未完成，请重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_fallback_provider",
        "备用公网搜索提供方暂时不可用，请稍后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.WAIT,
    ),
    _error(
        "web_search_fallback_redirect",
        "备用公网搜索发生了不受控重定向，已拒绝处理。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.NONE,
    ),
    _error(
        "web_search_fallback_response_too_large",
        "备用公网搜索响应过大，已拒绝处理。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.NONE,
    ),
    _error(
        "web_search_fallback_not_configured",
        "备用公网搜索尚未配置，请联系管理员。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "web_search_fallback_not_started",
        "本轮公网阶段预算不足，未启动备用搜索，请稍后重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.WAIT,
    ),
    _error(
        "web_search_all_providers_failed",
        "主用与备用公网搜索均未完成，请稍后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_provider_challenge",
        "搜索提供方（Tavily）暂时受阻，正在冷却；请稍后显式重试，"
        "系统不会在本轮自动重复请求。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.WAIT,
    ),
    _error(
        "web_search_configuration",
        "搜索凭据无效（Tavily API Key 未通过校验），"
        "请检查 Tavily API Key 配置。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "web_search_credentials",
        "未配置搜索凭据：请先运行 BridGes start 配置 Tavily API Key，"
        "或设置 BRIDGES_TAVILY_API_KEY。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "web_search_quota",
        "搜索用量已达上限（Tavily 套餐或按量额度），"
        "请在控制台调整用量后重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RECONFIGURE,
    ),
    _error(
        "web_search_contract",
        "搜索结果结构不符合已登记合同（Tavily），无法解析。",
        FailureClass.INTERNAL,
        RecoveryAction.NONE,
    ),
    _error(
        "web_search_redirect",
        "公网搜索发生了不受控重定向，已拒绝处理。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.NONE,
    ),
    _error(
        "web_search_response_too_large",
        "公网搜索响应过大，已拒绝处理。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.NONE,
    ),
    _error(
        "web_search_source_conflict",
        "多个公开来源对当前事实给出冲突信息，暂不能形成确定结论。",
        FailureClass.UNVERIFIABLE,
        RecoveryAction.ADJUST_REQUEST,
    ),
    _error(
        "web_search_cancelled",
        WEB_SEARCH_CANCELLED_TEXT,
        FailureClass.STOPPED,
        RecoveryAction.NONE,
    ),
    _error(
        "web_search_stage_timeout",
        WEB_SEARCH_STAGE_TIMEOUT_TEXT,
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_internal",
        WEB_SEARCH_INTERNAL_TEXT,
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "web_search_evidence_insufficient",
        "搜索页面包含结果节点，但没有可安全引用的公开来源。",
        FailureClass.UNVERIFIABLE,
        RecoveryAction.WAIT,
    ),
    _error(
        "web_search_no_results",
        "没有找到可核实的公开网页结果。",
        FailureClass.NOT_READ,
        RecoveryAction.ADJUST_REQUEST,
    ),
    _error(
        "web_search_citation_invalid",
        "联网回答缺少可核实引用，请重试。",
        FailureClass.UNVERIFIABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "fact_protection_inconsistent",
        "回答未能可靠保留要求的事实片段或引用，请重试。",
        FailureClass.UNVERIFIABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_timeout",
        "arXiv 搜索超时，请重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_rate_limit",
        "arXiv 请求过于频繁，请稍后重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.WAIT,
    ),
    _error(
        "arxiv_offline",
        "当前无法连接 arXiv，请检查网络后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_permission",
        "当前网络未允许访问 arXiv，请检查网络权限后重试。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.ADJUST_REQUEST,
    ),
    _error(
        "arxiv_parse",
        "arXiv 返回内容损坏，无法解析，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_request",
        "arXiv 搜索请求未完成，请重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_startup",
        "arXiv 搜索服务启动失败，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_handshake",
        "arXiv 搜索服务启动失败，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_worker_exit",
        "arXiv 搜索服务进程已退出，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_internal",
        "arXiv 搜索服务异常，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
    ),
    _error(
        "arxiv_cancelled",
        "已取消本轮论文搜索。",
        FailureClass.STOPPED,
        RecoveryAction.NONE,
    ),
    _error(
        "arxiv_no_results",
        "没有找到匹配的 arXiv 论文，请调整领域或约束后重试。",
        FailureClass.NOT_READ,
        RecoveryAction.ADJUST_REQUEST,
    ),
    _error(
        "arxiv_no_relevant_results",
        "没有找到与主题相关的 arXiv 论文，请调整领域或约束后重试。",
        FailureClass.NOT_READ,
        RecoveryAction.ADJUST_REQUEST,
    ),
    _error(
        "arxiv_citation_invalid",
        "论文回答缺少可核实的 arXiv 引用，请重试。",
        FailureClass.UNVERIFIABLE,
        RecoveryAction.RETRY,
    ),
    _error(
        "skill_unavailable",
        "SKILL 能力暂不可用，请稍后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.WAIT,
    ),
    _error(
        "budget_exceeded",
        "本次生成超过时延预算，已停止继续执行；请重试（输入已保留）。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    _error(
        "payload_budget_exceeded",
        "本轮需要的材料超出当前模型的输入额度，无法在不丢失关键条件的情况下"
        "完整作答。请缩小问题范围、减少附件或缩短材料后重试。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.ADJUST_REQUEST,
    ),
    _error(
        "run_budget_exhausted",
        "本轮运行预算不足以识别全部书页；已完成的书页已保存，"
        "未处理页仍待识别。请重试本条消息继续。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    _error(
        "run_budget_call_limit",
        "本轮模型调用已达预算上限，已完成内容已保存；请重试继续。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.RETRY,
    ),
    # -- 日常父图节点错误（显式拒绝，不悄悄降级；节点失败时由父图拼装
    #    位置与恢复方式，因此这里登记真实类别但不覆盖具体原因） ----------
    _error(
        "module_not_available",
        "该模块尚未开放，请使用普通对话。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.NONE,
        contextual=True,
    ),
    _error(
        "module_mode_conflict",
        "学习模式不能启动日常模块。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.NONE,
        contextual=True,
    ),
    _error(
        "network_not_allowed",
        "本轮要求不联网，不能启动需要外部检索的模块。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.NONE,
        contextual=True,
    ),
    _error(
        "source_not_allowed",
        "所选模块不符合本轮限定的资料来源。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.NONE,
        contextual=True,
    ),
    _error(
        "task_state_conflict",
        "任务状态或版本已变化，请基于最新任务重试。",
        FailureClass.STATE_CONFLICT,
        RecoveryAction.REFRESH_STATE,
        contextual=True,
    ),
    _error(
        "route_rejected",
        ROUTE_REJECTED_FALLBACK_TEXT,
        FailureClass.UNSUPPORTED,
        RecoveryAction.ADJUST_REQUEST,
        contextual=True,
    ),
    # -- 模型调用共享码（聊天、图片、视频、语音同源） ----------------------
    _error(
        "rate_limit",
        "请求过于频繁（已触发限流），请稍后重试。",
        FailureClass.RATE_LIMIT_TIMEOUT,
        RecoveryAction.WAIT,
        shared=True,
    ),
    _error(
        "transient",
        "连接中断或服务暂时不可用，请检查网络后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
        shared=True,
    ),
    _error(
        "region_error",
        "无法连接 Qwen 服务，请检查网络后重试。",
        FailureClass.UNAVAILABLE,
        RecoveryAction.RETRY,
        shared=True,
    ),
    _error(
        "region_dns",
        "无法解析 Qwen 服务域名，请检查 DNS 或代理设置。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
        shared=True,
    ),
    _error(
        "region_proxy",
        "连接被代理拒绝，请检查代理配置。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
        shared=True,
    ),
    _error(
        "region_tls",
        "安全证书校验失败，可能存在 SSL 审查软件，请检查网络环境。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
        shared=True,
    ),
    _error(
        "auth_error",
        "Qwen API Key 无效或已失效，请检查启动服务的全局百炼配置与权限。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
        shared=True,
    ),
    _error(
        "provider_rejected",
        "供应商拒绝了本次请求，请稍后重试。",
        FailureClass.INTERNAL,
        RecoveryAction.WAIT,
        shared=True,
    ),
    _error(
        "safety_refusal",
        "模型拒绝了本次请求，请调整内容后重试。",
        FailureClass.UNSUPPORTED,
        RecoveryAction.ADJUST_REQUEST,
        shared=True,
    ),
    _error(
        "empty_response",
        "模型返回内容为空，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
        shared=True,
    ),
    _error(
        "cassette_missing",
        "离线回放模式缺少请求录像，请检查配置。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
        shared=True,
    ),
    _error(
        "structured_output_parse_failed",
        "模型输出不是合法 JSON，请重试。",
        FailureClass.INTERNAL,
        RecoveryAction.RETRY,
        shared=True,
    ),
    _error(
        "unsupported_structured_output_format",
        "结构化输出格式不受支持，请检查任务配置。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
        shared=True,
    ),
    _error(
        "invalid_response_format",
        "结构化输出格式参数无效，请检查任务配置。",
        FailureClass.NOT_CONFIGURED,
        RecoveryAction.RECONFIGURE,
        shared=True,
    ),
)

#: 聊天链路领域码 → 中文模板（聊天、后台执行器与图共用；携带具体
#: 上下文的节点错误只登记分类，不进入优先表，避免覆盖节点位置）。
CHAT_ERROR_TEMPLATES: dict[str, str] = {
    template.code: template.text
    for template in ERROR_TEMPLATES
    if not template.shared and not template.contextual
}
#: 模型调用共享码 → 中文模板（聊天、图片、视频、语音同源）。
MODEL_CALL_ERROR_TEMPLATES: dict[str, str] = {
    template.code: template.text for template in ERROR_TEMPLATES if template.shared
}
#: 未知错误码的兜底文案（与 internal_error 同源，不输出供应商原文）。
DEFAULT_ERROR_MESSAGE = CHAT_ERROR_TEMPLATES["internal_error"]

# ---------------------------------------------------------------------------
# 模块与学习清单（领域模板由 24–36 按实际状态落地，本票登记路径与真状态）
# ---------------------------------------------------------------------------

_MODULE_ENTRIES: tuple[CopyEntry, ...] = (
    # -- 论文（工单 24） --------------------------------------------------
    CopyEntry(
        "module.paper.clarification",
        CopyCategory.CLARIFICATION,
        "paper",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("clarification",),
        renderer="bridges.paper.presenting.render_clarification_content",
        note="工单 24：澄清问句由论文解析结果选择。",
    ),
    CopyEntry(
        "module.paper.result",
        CopyCategory.MODULE,
        "paper",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success",),
        renderer="bridges.paper.presenting.render_result_content",
        note="工单 24：论文标识、阅读范围与引用由真实结果渲染。",
    ),
    CopyEntry(
        "module.paper.empty",
        CopyCategory.MODULE,
        "paper",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("empty",),
        renderer="bridges.paper.presenting.render_empty_content",
        note="工单 24：候选为空时如实说明。",
    ),
    CopyEntry(
        "module.paper.mismatch",
        CopyCategory.CLARIFICATION,
        "paper",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("clarification", "topic_mismatch"),
        renderer="bridges.paper.presenting.render_mismatch_content",
        note="工单 24：主题不匹配时停止推荐并请求澄清，不是部分成功。",
    ),
    CopyEntry(
        "module.paper.stopped",
        CopyCategory.MODULE,
        "paper",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("stopped",),
        renderer="bridges.paper.presenting.render_stopped_content",
        note="工单 24：停止不伪装完成。",
    ),
    CopyEntry(
        "module.paper.progress_labels",
        CopyCategory.PROGRESS,
        "paper",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("progress", "failure_location"),
        renderer="bridges.paper.service.PAPER_NODE_LABELS",
        note="工单 24：节点进度标签与失败定位同一来源。",
    ),
    # -- 学习资料（工单 25） ----------------------------------------------
    CopyEntry(
        "module.resources.clarification",
        CopyCategory.CLARIFICATION,
        "resources",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("clarification",),
        renderer="bridges.resources.presenting.render_clarification_content",
        note="工单 25：只有缺失会改变推荐方向时才追问。",
    ),
    CopyEntry(
        "module.resources.result",
        CopyCategory.MODULE,
        "resources",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success",),
        renderer="bridges.resources.presenting.render_result_content",
        note="工单 25：书目/视频证据与链接真实渲染。",
    ),
    CopyEntry(
        "module.resources.empty",
        CopyCategory.MODULE,
        "resources",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("empty",),
        renderer="bridges.resources.presenting.render_empty_content",
        note="工单 25：证据不足时给真实数量。",
    ),
    CopyEntry(
        "module.resources.mismatch",
        CopyCategory.MODULE,
        "resources",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("empty", "topic_mismatch"),
        renderer="bridges.resources.presenting.render_mismatch_content",
        note="工单 25：主题不匹配时停止推荐，真实投影为空结果。",
    ),
    CopyEntry(
        "module.resources.stopped",
        CopyCategory.MODULE,
        "resources",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("stopped",),
        renderer="bridges.resources.presenting.render_stopped_content",
        note="工单 25：停止不伪装完成。",
    ),
    CopyEntry(
        "module.resources.error",
        CopyCategory.MODULE,
        "resources",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("error",),
        renderer="bridges.resources.presenting.render_error_content",
        note="工单 25：失败如实给出缺口与可重试性。",
    ),
    CopyEntry(
        "module.resources.progress_labels",
        CopyCategory.PROGRESS,
        "resources",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("progress", "failure_location"),
        renderer="bridges.resources.service.RESOURCES_NODE_LABELS",
        note="工单 25：节点进度标签与失败定位同一来源。",
    ),
    # -- GitHub（工单 26） -----------------------------------------------
    CopyEntry(
        "module.github.clarification",
        CopyCategory.CLARIFICATION,
        "github",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("clarification",),
        renderer="bridges.github.presenting.render_clarification_content",
        note="工单 26：目的不明时只追问必要信息。",
    ),
    CopyEntry(
        "module.github.result",
        CopyCategory.MODULE,
        "github",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success",),
        renderer="bridges.github.presenting.render_result_content",
        note="工单 26：需求覆盖矩阵与许可/运行状态真实渲染。",
    ),
    CopyEntry(
        "module.github.empty",
        CopyCategory.MODULE,
        "github",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("empty",),
        renderer="bridges.github.presenting.render_empty_content",
        note="工单 26：少于目标时如实交付。",
    ),
    CopyEntry(
        "module.github.stopped",
        CopyCategory.MODULE,
        "github",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("stopped",),
        renderer="bridges.github.presenting.render_stopped_content",
        note="工单 26：停止不伪装完成。",
    ),
    CopyEntry(
        "module.github.progress_labels",
        CopyCategory.PROGRESS,
        "github",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("progress", "failure_location"),
        renderer="bridges.github.service.GITHUB_NODE_LABELS",
        note="工单 26：节点进度标签与失败定位同一来源。",
    ),
    # -- 贴吧（工单 27） --------------------------------------------------
    CopyEntry(
        "module.tieba.clarification",
        CopyCategory.CLARIFICATION,
        "tieba",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("clarification",),
        renderer="bridges.tieba.presenting.render_clarification_content",
        note="工单 27：对象或范围不足时只问一个必要问题。",
    ),
    CopyEntry(
        "module.tieba.result",
        CopyCategory.MODULE,
        "tieba",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success",),
        renderer="bridges.tieba.presenting.render_result_content",
        note="工单 27：规定/经历/冲突与实际读取范围分别呈现。",
    ),
    CopyEntry(
        "module.tieba.empty",
        CopyCategory.MODULE,
        "tieba",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("empty",),
        renderer="bridges.tieba.presenting.render_empty_content",
        note="工单 27：未取得回复时不称普遍共识。",
    ),
    CopyEntry(
        "module.tieba.stopped",
        CopyCategory.MODULE,
        "tieba",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("stopped",),
        renderer="bridges.tieba.presenting.render_stopped_content",
        note="工单 27：停止不伪装完成。",
    ),
    CopyEntry(
        "module.tieba.progress_labels",
        CopyCategory.PROGRESS,
        "tieba",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("progress", "failure_location"),
        renderer="bridges.tieba.service.TIEBA_NODE_LABELS",
        note="工单 27：节点进度标签与失败定位同一来源。",
    ),
    # -- 职业规划（工单 28/29） -------------------------------------------
    CopyEntry(
        "module.career_plan.clarification",
        CopyCategory.CLARIFICATION,
        "career_plan",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("clarification",),
        renderer="bridges.career_plan.presenting.render_clarification_content",
        note="工单 28：岗位或目的不足时只追问必要信息。",
    ),
    CopyEntry(
        "module.career_plan.result",
        CopyCategory.MODULE,
        "career_plan",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success",),
        renderer="bridges.career_plan.presenting.render_result_content",
        note="工单 28：岗位样本、薪资原文与单位由真实结果渲染。",
    ),
    CopyEntry(
        "module.career_plan.links_only",
        CopyCategory.MODULE,
        "career_plan",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("partial",),
        renderer="bridges.career_plan.presenting.render_links_only_content",
        note="工单 28：只有链接时给来源线索，不生成虚构样本分析。",
    ),
    CopyEntry(
        "module.career_plan.empty",
        CopyCategory.MODULE,
        "career_plan",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("empty",),
        renderer="bridges.career_plan.presenting.render_empty_content",
        note="工单 28：无可用岗位时如实说明。",
    ),
    CopyEntry(
        "module.career_plan.stopped",
        CopyCategory.MODULE,
        "career_plan",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("stopped",),
        renderer="bridges.career_plan.presenting.render_stopped_content",
        note="工单 28：停止不伪装完成。",
    ),
    CopyEntry(
        "module.career_plan.progress_labels",
        CopyCategory.PROGRESS,
        "career_plan",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("progress", "failure_location"),
        renderer="bridges.career_plan.service.CAREER_NODE_LABELS",
        note="工单 28：节点进度标签与失败定位同一来源。",
    ),
    # -- 通勤（工单 10 试点，模板调整仍受本票清单约束） ------------------
    CopyEntry(
        "module.commute.result",
        CopyCategory.MODULE,
        "commute",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success",),
        renderer="bridges.commute.presenting.render_result_content",
        note="工单 10：距离、时间、缓冲与路段保真。",
    ),
    CopyEntry(
        "module.commute.error",
        CopyCategory.MODULE,
        "commute",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("failure",),
        renderer="bridges.commute.presenting.render_error_content",
        note="工单 10：地点/路线失败时如实说明，不用其他方式替换。",
    ),
    CopyEntry(
        "module.commute.stopped",
        CopyCategory.MODULE,
        "commute",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("stopped",),
        renderer="bridges.commute.presenting.render_stopped_content",
        note="工单 10：停止不伪装完成。",
    ),
    CopyEntry(
        "module.commute.progress_labels",
        CopyCategory.PROGRESS,
        "commute",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("progress", "failure_location"),
        renderer="bridges.commute.kernel.COMMUTE_NODE_LABELS",
        note="工单 10：节点进度标签与失败定位同一来源。",
    ),
)

_STUDY_ENTRIES: tuple[CopyEntry, ...] = (
    CopyEntry(
        "study.scope.failure", CopyCategory.STUDY, "study",
        CopyStrategy.DETERMINISTIC_RENDERER, ("failure",),
        renderer="bridges.study.scope.StudyScopeNodeFlow",
        note="工单 31：范围结构、关键内容和排除理由核验详情及预习密度缺口。",
    ),
    CopyEntry(
        "study.pages.wait",
        CopyCategory.WAIT,
        "study",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("waiting", "awaiting_pages"),
        renderer="bridges.study.service.StudyWorkflow.run",
        note="工单 30：run.recognize 按实际书页缺失、页序或识别缺口说明等待原因。",
    ),
    CopyEntry(
        "study.preview.scope_and_questions",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success", "tutoring"),
        renderer="bridges.study.service.StudyWorkflow.run",
        note="工单 31：run.preview 从实际范围、页级定位和既有生成问题拼装预习说明。",
    ),
    CopyEntry(
        "study.pages.updated",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success", "tutoring"),
        renderer="bridges.study.service.StudyWorkflow.run",
        note="工单 35：run.finish_pages 说明追加后的真实页数与范围，保留既有问答来源。",
    ),
    CopyEntry(
        "study.review.paused",
        CopyCategory.WAIT,
        "study",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("paused", "tutoring"),
        renderer="bridges.study.service.StudyWorkflow.run",
        note="工单 35：run.review 的 pause 分支说明回辅导及题目、判定保留，不是生成停止。",
    ),
    CopyEntry(
        "study.review.current_question",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("waiting", "review"),
        renderer="bridges.study.service.StudyWorkflow.run",
        note="工单 34：run.review 的 start 分支复述已保存的当前题，不重复出题。",
    ),
    CopyEntry(
        "study.stopped",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.FIXED_TEMPLATE,
        ("stopped",),
        text=STUDY_STOPPED_TEXT,
        note="学习处理实际收到停止信号时使用，不把复盘完成或暂停当作停止。",
    ),
    CopyEntry(
        "study.tutoring.answer",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.MODEL_ADAPTED,
        ("success", "partial"),
        renderer="bridges.study.tutoring.tutor",
        note="工单 32：在既有辅导生成调用中接入表达约束，材料证据先于表达。",
    ),
    CopyEntry(
        "study.review.question",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.MODEL_ADAPTED,
        ("success",),
        renderer="bridges.study.review.plan_review",
        note="工单 33：题干由冻结的覆盖计划生成，不向用户提前暴露评分要点。",
    ),
    CopyEntry(
        "study.review.feedback",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.MODEL_ADAPTED,
        ("success", "partial", "failure"),
        renderer="bridges.study.review.render_feedback",
        note="工单 34：判定证据先于表达，正确/不完整/错误如实反馈。",
    ),
    CopyEntry(
        "study.review.next_question",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success", "complete"),
        renderer="bridges.study.review.next_question",
        note="工单 34：选择未问题；无下一题时标记复盘完成，不代表用户停止。",
    ),
    CopyEntry(
        "study.summary.render",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("success", "partial"),
        renderer="bridges.study.summary.render_summary",
        note="工单 36：本次覆盖/答对/漏洞/未判定按真实记录分节渲染。",
    ),
    CopyEntry(
        "study.failure.model_error",
        CopyCategory.STUDY,
        "study",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("failure",),
        renderer="bridges.study.service.model_failure_message",
        note="工单 30–36：学习模型失败复用共享错误映射，按可重试性给真实恢复方式。",
    ),
)


def _error_entries() -> tuple[CopyEntry, ...]:
    entries: list[CopyEntry] = []
    for template in ERROR_TEMPLATES:
        stopped = template.failure_class is FailureClass.STOPPED
        entries.append(
            CopyEntry(
                path=f"error.{template.code}",
                category=CopyCategory.STOP if stopped else CopyCategory.ERROR,
                owner="chat" if not template.shared else "ai",
                strategy=CopyStrategy.FIXED_TEMPLATE,
                states=(template.failure_class.value, template.recovery.value),
                text=template.text,
                note="错误只承诺真实可用恢复能力；类别与恢复方式随码登记。",
            )
        )
    return tuple(entries)


_PUBLIC_ENTRIES: tuple[CopyEntry, ...] = (
    # -- 澄清 --------------------------------------------------------------
    CopyEntry(
        "chat.clarification.multiple_tasks",
        CopyCategory.CLARIFICATION,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("clarification", "capability_ambiguity"),
        text=CLARIFICATION_MULTIPLE_TASKS_TEMPLATE,
        note="多个能力信号时只问一个必要问题。",
    ),
    CopyEntry(
        "chat.clarification.task_ambiguity",
        CopyCategory.CLARIFICATION,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("clarification", "task_ambiguity"),
        text=CLARIFICATION_TASK_AMBIGUITY_TEXT,
        note="多个历史任务同样合理时要求确认。",
    ),
    CopyEntry(
        "chat.clarification.task_history_ambiguity",
        CopyCategory.CLARIFICATION,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("clarification", "task_ambiguity"),
        text=CLARIFICATION_TASK_HISTORY_AMBIGUITY_TEXT,
        note="找到多个历史任务时要求确认继续哪一个。",
    ),
    CopyEntry(
        "chat.clarification.reference_ambiguity",
        CopyCategory.CLARIFICATION,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("clarification", "reference_ambiguity"),
        text=CLARIFICATION_REFERENCE_AMBIGUITY_TEXT,
        note="指代不唯一时要求确认，不强行归属最近模块。",
    ),
    CopyEntry(
        "route.clarification.paper_topic",
        CopyCategory.CLARIFICATION,
        "routing",
        CopyStrategy.FIXED_TEMPLATE,
        ("clarification", "missing_slot"),
        text=ROUTE_CLARIFY_PAPER_TOPIC_TEXT,
        note="论文检索缺主题时只追问主题。",
    ),
    CopyEntry(
        "route.clarification.paper_query_parts",
        CopyCategory.CLARIFICATION,
        "routing",
        CopyStrategy.FIXED_TEMPLATE,
        ("clarification", "missing_slot"),
        text=ROUTE_CLARIFY_PAPER_QUERY_PARTS_TEXT,
        note="论文检索缺可检索字段时列出可用字段。",
    ),
    CopyEntry(
        "route.clarification.multiple_paper_or_other",
        CopyCategory.CLARIFICATION,
        "routing",
        CopyStrategy.FIXED_TEMPLATE,
        ("clarification", "capability_ambiguity"),
        text=ROUTE_CLARIFY_MULTIPLE_PAPER_OR_OTHER_TEXT,
        note="论文与其他任务并存时只问先做哪一项。",
    ),
    CopyEntry(
        "route.clarification.multiple_text_capabilities",
        CopyCategory.CLARIFICATION,
        "routing",
        CopyStrategy.FIXED_TEMPLATE,
        ("clarification", "capability_ambiguity"),
        text=ROUTE_CLARIFY_MULTIPLE_TEXT_CAPABILITIES_TEXT,
        note="多个独立文本能力时只问先做哪一项。",
    ),
    # -- 进度与思考摘要 ----------------------------------------------------
    CopyEntry(
        "chat.progress.node_failure",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("failure_location",),
        text=GRAPH_NODE_FAILURE_TEMPLATE,
        note="失败位置按真实节点标注；恢复方式紧随错误真实可重试性。",
    ),
    CopyEntry(
        "chat.progress.recovery_retry",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("failure_location", "retryable"),
        text=GRAPH_RECOVERY_RETRY_TEXT,
        note="可重试错误才给点击重试指引。",
    ),
    CopyEntry(
        "chat.progress.recovery_adjust",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("failure_location", "not_retryable"),
        text=GRAPH_RECOVERY_ADJUST_TEXT,
        note="不可重试错误不承诺重试恢复。",
    ),
    CopyEntry(
        "chat.progress.recovery_retry_plain",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("failure_location", "recoverable"),
        text=GRAPH_RECOVERY_RETRY_PLAIN_TEXT,
        note="登记为可重试但未开放点击重试时只承诺「请重试」。",
    ),
    CopyEntry(
        "chat.progress.recovery_wait",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("failure_location", "recoverable"),
        text=GRAPH_RECOVERY_WAIT_TEXT,
        note="限流/冷却类恢复方式是等待后重试，不承诺立即成功。",
    ),
    CopyEntry(
        "chat.progress.recovery_reconfigure",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("failure_location", "not_retryable"),
        text=GRAPH_RECOVERY_RECONFIGURE_TEXT,
        note="配置类失败对用户只承诺联系管理员，不承诺自行重试。",
    ),
    CopyEntry(
        "chat.progress.node_labels",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.DETERMINISTIC_RENDERER,
        ("node_started", "node_completed", "failure_location"),
        renderer="bridges.chat.graph.NODE_LABELS",
        note="父图与子图节点进度、失败定位共用同一标签表。",
    ),
    CopyEntry(
        "chat.progress.thinking_done",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("success",),
        text=THINKING_DONE_QUALITY_TEXT,
        note="完成结论只描述已提交事实。",
    ),
    CopyEntry(
        "chat.progress.thinking_failed",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("failure",),
        text=THINKING_FAILED_FALLBACK_TEXT,
        note="未知错误码时的失败兜底，不伪装成功。",
    ),
    CopyEntry(
        "chat.progress.thinking_stopped",
        CopyCategory.PROGRESS,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("stopped",),
        text=THINKING_STOPPED_QUALITY_TEXT,
        note="停止保留已生成内容，不当作失败。",
    ),
    CopyEntry(
        "chat.progress.thinking_budget_warning",
        CopyCategory.DEGRADATION,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("degraded", "budget_limited"),
        text=THINKING_BUDGET_WARNING_TEXT,
        note="预算受限交付如实标注内容可能不完整。",
    ),
    # -- 等待与停止 --------------------------------------------------------
    CopyEntry(
        "chat.wait.generation_in_progress",
        CopyCategory.WAIT,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("waiting", "generation_in_progress"),
        text=WAIT_GENERATION_IN_PROGRESS_TEXT,
        note="上一轮仍在生成时给出可用的停止/等待方式。",
    ),
    CopyEntry(
        "chat.wait.delete_in_progress",
        CopyCategory.WAIT,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("waiting", "delete_blocked"),
        text=WAIT_DELETE_IN_PROGRESS_TEXT,
        note="生成中的消息不可删除，先停止。",
    ),
    CopyEntry(
        "chat.stop.user_stopped",
        CopyCategory.STOP,
        "chat",
        CopyStrategy.FIXED_TEMPLATE,
        ("stopped",),
        text=USER_STOPPED_TEXT,
        note="停止不是错误，不携带错误码。",
    ),
    # -- 检索：空结果、部分与降级 -----------------------------------------
    CopyEntry(
        "retrieval.status.sufficient",
        CopyCategory.PROGRESS,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("success",),
        text=RETRIEVAL_SUFFICIENT_TEXT,
        note="充足时如实说明已检索到足够材料。",
    ),
    CopyEntry(
        "retrieval.empty.no_hits",
        CopyCategory.EMPTY,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("empty",),
        text=RETRIEVAL_NO_HITS_TEXT,
        note="空结果如实说明，不伪造引用。",
    ),
    CopyEntry(
        "retrieval.empty.attachments_pending",
        CopyCategory.EMPTY,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("empty", "pending"),
        text=RETRIEVAL_ATTACHMENTS_PENDING_TEXT,
        note="附件未就绪时说明处理中或暂无可检索内容。",
    ),
    CopyEntry(
        "retrieval.empty.knowledge_base_not_ready",
        CopyCategory.EMPTY,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("empty",),
        text=RETRIEVAL_KNOWLEDGE_BASE_NOT_READY_TEXT,
        note="知识库无已就绪材料时如实说明。",
    ),
    CopyEntry(
        "retrieval.empty.project_retired",
        CopyCategory.EMPTY,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("empty", "retired_source"),
        text=RETRIEVAL_PROJECT_RETIRED_TEXT,
        note="退役来源如实标注，不恢复旧入口。",
    ),
    CopyEntry(
        "retrieval.partial.conflict",
        CopyCategory.PARTIAL,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("partial", "conflict"),
        text=RETRIEVAL_CONFLICT_TEXT,
        note="来源冲突时保留不确定，不强行平均。",
    ),
    CopyEntry(
        "retrieval.partial.insufficient_coverage",
        CopyCategory.PARTIAL,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("partial", "insufficient_coverage"),
        text=RETRIEVAL_INSUFFICIENT_COVERAGE_TEXT,
        note="覆盖不足时如实标注缺口。",
    ),
    CopyEntry(
        "retrieval.degradation.index_unavailable",
        CopyCategory.DEGRADATION,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("degraded", "index_unavailable"),
        text=RETRIEVAL_INDEX_UNAVAILABLE_TEXT,
        note="本地索引不可用时如实降级，不假装检索成功。",
    ),
    CopyEntry(
        "retrieval.degradation.vector_unavailable",
        CopyCategory.DEGRADATION,
        "retrieval",
        CopyStrategy.FIXED_TEMPLATE,
        ("degraded", "vector_unavailable"),
        text=RETRIEVAL_VECTOR_UNAVAILABLE_TEXT,
        note="向量检索不可用时只用关键词检索，并如实说明本轮降级。",
    ),
    # -- 公网搜索降级 ------------------------------------------------------
    CopyEntry(
        "web_search.degradation.provider_unready",
        CopyCategory.DEGRADATION,
        "web_search",
        CopyStrategy.FIXED_TEMPLATE,
        ("degraded", "provider_unready"),
        text=WEB_SEARCH_PROVIDER_UNREADY_TEXT,
        note="提供方未就绪时先如实提示，再尝试并说明降级路径。",
    ),
)

#: 全部登记项（模块与学习部分不重复实现文案，领域票据按此复核）。
STATE_COPY_ENTRIES: tuple[CopyEntry, ...] = (
    _PUBLIC_ENTRIES + _error_entries() + _MODULE_ENTRIES + _STUDY_ENTRIES
)
