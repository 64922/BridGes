"""文章人味化语料（Issue 09 分层语料：article-humanization）。

48 条原创净室文章案例：25 条由 humanizer SKILL 冻结评测集
（natural_language_eval.md NL-01~25，BridGes 原创）转换为可执行数据合同，
23 条新创作/迁移（含 Issue 01 tracer bullet 案例迁移、时间管理失败冻结
案例与"无亲历"对抗变体、生成类 4 条、12 类对抗改写输入、三级强度、科研
技术深改、会议通知轻改、原文已自然、材料不足场景）。每条案例包含任务、
来源与许可元数据，保护项均为原文逐字片段，可配对运行并重放。

全部文本为 BridGes 原创净室素材（docs/adr/0011），不复制任何外部参考
项目的文字、结构或示例。
"""

from __future__ import annotations

from bridges.humanize_eval.cases import (
    ArticleGenre,
    CaseOperation,
    CasePartition,
    HumanizeCase,
    HumanizeCaseKind,
    RewriteIntensity,
    RiskLevel,
)

_LICENSE = "BridGes 原创净室素材（docs/adr/0011），无第三方文本复用。"
_LICENSE_EXAMPLE = (
    "BridGes 原创净室素材（docs/adr/0011）；文中数字为原创示例数据，"
    "仅用于保真评测，不代表真实调查结果。"
)

_REWRITE_REQUEST = (
    "请把下面这篇{genre}改写得更像一个真实作者写的：去掉 PPT 腔和抽象包装，"
    "保留所有事实、数字、引用和否定边界，不新增任何内容。"
)


def _article(
    case_id: str,
    title: str,
    source: str,
    *,
    genre: ArticleGenre,
    intensity: RewriteIntensity,
    request: str,
    audience: str,
    channel: str,
    length: str,
    forbidden: list[str],
    protected: list[str],
    tags: list[str],
    risk: RiskLevel = RiskLevel.LOW,
    do_no_harm: bool = False,
    adversarial: str | None = None,
    operation: CaseOperation = CaseOperation.REWRITE,
    partition: CasePartition = CasePartition.DEVELOPMENT,
    license_note: str = _LICENSE,
) -> HumanizeCase:
    # 模板请求自动带标题前缀，保证 user_request 全局唯一（重放可定位案例）。
    if request.startswith("请把下面这篇"):
        request = f"{title}。{request}"
    return HumanizeCase(
        case_id=case_id,
        kind=HumanizeCaseKind.ARTICLE,
        title=title,
        user_request=request,
        source_text=source,
        context=None,
        surface_type="改写" if operation is CaseOperation.REWRITE else "按主题生成",
        operation=operation,
        mode=f"{intensity.value}改写" if operation is CaseOperation.REWRITE else "生成",
        conversation_mode=None,
        genre_profile=genre,
        rewrite_intensity=intensity,
        risk=risk,
        audience=audience,
        channel=channel,
        target_length=length,
        allowed_materials=[],
        forbidden_claims=forbidden,
        protected_items=protected,
        do_no_harm=do_no_harm,
        slice_tags=["article", *tags],
        adversarial_type=adversarial,
        partition=partition,
        license_source_note=license_note,
    )


# ---------------------------------------------------------------------------
# NL-01~25：冻结评测集转换为可执行数据合同（说明文/科普/邮件/报告/演讲）
# ---------------------------------------------------------------------------

NL_01 = _article(
    "article-nl01-share-list-help-v1",
    "共享清单帮助页改写（说明文/轻度）",
    "打开共享开关后，其他成员可以查看这个清单，但不能直接修改清单内容。",
    genre=ArticleGenre.INSTRUCTION,
    intensity=RewriteIntensity.LIGHT,
    request=_REWRITE_REQUEST.format(genre="产品帮助页说明"),
    audience="第一次使用的普通用户",
    channel="产品帮助页",
    length="短文",
    forbidden=["新增功能说明", "编造权限细节", "客服式尾句"],
    protected=["共享开关", "可以查看这个清单", "不能直接修改清单内容"],
    tags=["instruction", "light_intensity", "low_fact_density"],
)

NL_02 = _article(
    "article-nl02-registration-receipt-v1",
    "登记回执操作手册改写（说明文/轻度）",
    "登记完成后，请把回执交给值班人员。系统没有显示成功时，不要重复提交，先保留页面截图。",
    genre=ArticleGenre.INSTRUCTION,
    intensity=RewriteIntensity.LIGHT,
    request=_REWRITE_REQUEST.format(genre="操作手册条目"),
    audience="社区志愿者",
    channel="操作手册",
    length="短文",
    forbidden=["新增流程步骤", "编造失败原因", "弱化否定边界"],
    protected=["把回执交给值班人员", "不要重复提交", "保留页面截图"],
    tags=["instruction", "light_intensity"],
)

NL_03 = _article(
    "article-nl03-lowpower-draft-v1",
    "低功耗状态说明改写（说明文/标准）",
    "设备进入低功耗状态后，屏幕会关闭，后台同步仍然继续。恢复操作不会删除已经保存的草稿，但未完成的上传可能需要重新开始。",
    genre=ArticleGenre.INSTRUCTION,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="内部说明"),
    audience="新入职员工",
    channel="内部说明",
    length="中等",
    forbidden=["新增设备型号细节", "编造同步行为", "删除否定边界"],
    protected=["屏幕会关闭", "后台同步仍然继续", "不会删除已经保存的草稿", "未完成的上传可能需要重新开始"],
    tags=["instruction"],
)

NL_04 = _article(
    "article-nl04-weekend-appointment-v1",
    "周末预约调整公告改写（说明文/标准）",
    "本次调整只影响周末的预约时段，工作日安排保持不变。已经确认的预约无需重新登记；如果需要更改时间，请在原预约页面操作。",
    genre=ArticleGenre.INSTRUCTION,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="服务公告"),
    audience="普通读者",
    channel="服务公告",
    length="中等",
    forbidden=["新增调整原因猜测", "编造影响范围", "弱化只影响周末的边界"],
    protected=["只影响周末的预约时段", "工作日安排保持不变", "无需重新登记", "在原预约页面操作"],
    tags=["instruction", "negation_boundary"],
)

NL_05 = _article(
    "article-nl05-import-stages-v1",
    "数据导入三阶段说明改写（说明文/深度）",
    "数据导入分为校验、写入和索引三个阶段。校验阶段会检查字段名称、日期格式和必填项，发现错误时只生成报告，不会写入数据库。写入完成后，系统才会建立索引，因此大批量文件可能在页面显示导入完成后仍需等待一段时间。若任务中断，请先查看导入报告，再决定是否重新上传，避免同一批数据被重复写入。",
    genre=ArticleGenre.INSTRUCTION,
    intensity=RewriteIntensity.DEEP,
    request=_REWRITE_REQUEST.format(genre="技术说明"),
    audience="项目维护人员",
    channel="技术说明",
    length="长文",
    forbidden=["新增性能数字", "编造索引机制", "删减三个阶段结构"],
    protected=["校验、写入和索引三个阶段", "只生成报告", "不会写入数据库", "先查看导入报告", "避免同一批数据被重复写入"],
    tags=["instruction", "deep_intensity", "high_fact_density"],
)

NL_06 = _article(
    "article-nl06-cloud-drop-v1",
    "云的形成科普改写（科普/标准）",
    "云不是“装满水的袋子”，而是空气中的水汽遇冷后形成的小水滴或冰晶。它们足够多、足够密时，我们才会在天空中看到云。",
    genre=ArticleGenre.POPULAR_SCIENCE,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="科普短文"),
    audience="刚接触科学的读者",
    channel="微信公众号",
    length="短文",
    forbidden=["新增气象学细节", "编造降水数据", "删掉比喻但改变事实"],
    protected=["水汽遇冷", "小水滴或冰晶", "足够多", "足够密"],
    tags=["popular_science", "low_fact_density"],
)

NL_07 = _article(
    "article-nl07-sound-medium-v1",
    "声音需要介质科普改写（科普/标准）",
    "声音需要介质传播，所以真空中没有像空气里那样的声音传播路径。耳机的振膜把电信号变成空气振动，我们听到的就是这种振动。",
    genre=ArticleGenre.POPULAR_SCIENCE,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="科普讲义"),
    audience="中学生",
    channel="科普讲义",
    length="短文",
    forbidden=["新增声学公式", "编造实验数据", "删除否定边界"],
    protected=["声音需要介质传播", "没有像空气里那样的声音传播路径", "振膜", "把电信号变成空气振动"],
    tags=["popular_science", "negation_boundary"],
)

NL_08 = _article(
    "article-nl08-plant-breathing-v1",
    "植物呼吸科普改写（科普/标准）",
    "植物在白天不只是“吸收二氧化碳”。光合作用需要光能，呼吸作用则会持续进行；因此，不能仅凭白天或夜晚来判断植物是否在进行呼吸。",
    genre=ArticleGenre.POPULAR_SCIENCE,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="问答文章"),
    audience="普通读者",
    channel="问答文章",
    length="中等",
    forbidden=["新增植物学结论", "编造研究引用", "弱化不能仅凭昼夜判断"],
    protected=["光合作用需要光能", "呼吸作用则会持续进行", "不能仅凭白天或夜晚来判断"],
    tags=["popular_science", "negation_boundary"],
)

NL_09 = _article(
    "article-nl09-fever-reaction-v1",
    "发烧反应科普改写（科普/标准/高风险）",
    "发烧是身体对感染或其他刺激的一种反应，不等于病因已经确定。测得体温后还要结合持续时间、精神状态和其他症状判断；如果孩子状态明显变差，应及时寻求专业帮助。",
    genre=ArticleGenre.POPULAR_SCIENCE,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="科普邮件"),
    audience="家长",
    channel="科普邮件",
    length="中等",
    forbidden=["新增用药建议", "编造医学数据", "弱化就医边界", "断言发烧原因"],
    protected=["不等于病因已经确定", "结合持续时间、精神状态和其他症状", "及时寻求专业帮助"],
    tags=["popular_science", "health", "do_no_harm"],
    risk=RiskLevel.HIGH,
    do_no_harm=True,
)

NL_10 = _article(
    "article-nl10-ice-melting-v1",
    "冰融化热量科普改写（科普/深度）",
    "一块冰放在室温下会逐渐融化，这是因为它从周围吸收了热量。温度描述的是物体冷热程度，热量则表示能量传递的过程，两者不是同一个概念。冰水混合物在融化过程中，吸收的能量主要用于改变状态，温度可以在一段时间内保持不变。等冰完全融化后，继续吸热才会让水的温度上升。这个例子说明，看到温度没有变化，并不能推出没有能量进入系统。",
    genre=ArticleGenre.POPULAR_SCIENCE,
    intensity=RewriteIntensity.DEEP,
    request=_REWRITE_REQUEST.format(genre="校园科普长文"),
    audience="高中生",
    channel="校园展板",
    length="长文",
    forbidden=["新增热量公式", "编造实验数据", "删掉温度与热量的区别"],
    protected=["从周围吸收了热量", "温度描述的是物体冷热程度", "热量则表示能量传递的过程", "温度可以在一段时间内保持不变", "继续吸热才会让水的温度上升"],
    tags=["popular_science", "deep_intensity", "high_fact_density"],
)

NL_11 = _article(
    "article-nl11-meeting-email-v1",
    "周三评审改期邮件改写（邮件/标准）",
    "大家好，周三评审改到下午三点，会议链接不变。请在会前把最新截图放到共享文件夹，谢谢。",
    genre=ArticleGenre.EMAIL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="项目邮件"),
    audience="合作同事",
    channel="项目邮件",
    length="短文",
    forbidden=["新增会议议题", "编造改期原因", "客服式套话"],
    protected=["周三评审改到下午三点", "会议链接不变", "把最新截图放到共享文件夹"],
    tags=["email", "low_fact_density"],
)

NL_12 = _article(
    "article-nl12-customer-email-v1",
    "客户材料补充邮件改写（邮件/标准）",
    "您好，我们已经收到您提交的资料。目前还缺少合同首页，请补充后回复此邮件。其余材料无需再次发送。",
    genre=ArticleGenre.EMAIL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="客户邮件"),
    audience="客户联系人",
    channel="客户邮件",
    length="短文",
    forbidden=["新增处理时限承诺", "编造流程", "删除其余材料无需再次发送的边界"],
    protected=["还缺少合同首页", "补充后回复此邮件", "其余材料无需再次发送"],
    tags=["email", "negation_boundary"],
)

NL_13 = _article(
    "article-nl13-access-extension-email-v1",
    "测试权限延期请示邮件改写（邮件/标准）",
    "您好，测试环境的访问权限将在本周五到期。为完成下周一的回归测试，申请将权限延长至下周三；若您同意，我会在测试结束后立即关闭不再使用的账号。",
    genre=ArticleGenre.EMAIL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="请示邮件"),
    audience="部门负责人",
    channel="请示邮件",
    length="中等",
    forbidden=["新增权限范围细节", "编造测试计划", "删掉延长边界"],
    protected=["本周五到期", "延长至下周三", "立即关闭不再使用的账号"],
    tags=["email"],
)

NL_14 = _article(
    "article-nl14-meeting-minutes-email-v1",
    "会议纪要邮件改写（邮件/标准）",
    "本次会议确定两项安排：设计组周四前确认移动端空状态，开发组下周一提供可点击版本。风险是接口字段还没有最终确定，字段变更可能影响联调时间。",
    genre=ArticleGenre.EMAIL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="会议纪要邮件"),
    audience="项目成员",
    channel="会议纪要邮件",
    length="中等",
    forbidden=["新增第三项安排", "编造风险等级", "删除责任人与时间"],
    protected=["设计组周四前确认移动端空状态", "开发组下周一提供可点击版本", "接口字段还没有最终确定", "可能影响联调时间"],
    tags=["email"],
)

NL_15 = _article(
    "article-nl15-network-maintenance-email-v1",
    "网络维护通知邮件改写（邮件/标准）",
    "各位同事，办公区网络将在本周六 09:00—12:00 进行设备维护。维护期间可能无法访问内部系统，但门禁和访客登记不受本次维护影响。请需要远程办公的同事提前下载周末所需文件，并避免在维护时段提交报销或审批。维护结束后，若仍无法连接，请先重启网络设备，再联系信息支持组。",
    genre=ArticleGenre.EMAIL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="通知邮件"),
    audience="全体员工",
    channel="通知邮件",
    length="长文",
    forbidden=["新增维护内容细节", "编造影响范围", "删除故障处理顺序"],
    protected=["周六 09:00—12:00", "可能无法访问内部系统", "门禁和访客登记不受本次维护影响", "先重启网络设备", "再联系信息支持组"],
    tags=["email", "high_fact_density"],
)

NL_16 = _article(
    "article-nl16-questionnaire-report-v1",
    "问卷回收实验报告改写（报告/轻度）",
    "本轮共回收 48 份问卷，其中 5 份因缺少关键答案未纳入分析。有效样本为 43 份，不应将回收总数当作分析样本数。",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.LIGHT,
    request=_REWRITE_REQUEST.format(genre="实验报告"),
    audience="研究助理",
    channel="实验报告",
    length="短文",
    forbidden=["新增统计方法", "编造问卷问题", "改错数字"],
    protected=["48 份问卷", "5 份因缺少关键答案", "有效样本为 43 份", "不应将回收总数当作分析样本数"],
    tags=["report", "light_intensity", "numbers"],
)

NL_17 = _article(
    "article-nl17-traffic-report-v1",
    "页面访问量运营报告改写（报告/标准）",
    "本周页面访问量较上周增加 12%，但完成注册的人数没有同步增长。当前只能确认访问增加，不能据此判断转化原因。",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="运营报告"),
    audience="业务负责人",
    channel="运营报告",
    length="短文",
    forbidden=["新增转化结论", "编造原因分析", "改错百分比"],
    protected=["增加 12%", "完成注册的人数没有同步增长", "不能据此判断转化原因"],
    tags=["report", "numbers", "negation_boundary"],
)

NL_18 = _article(
    "article-nl18-benchmark-report-v1",
    "响应时间对比研究报告改写（报告/标准）",
    "在本次 3 个批次的测试中，方案 A 的平均响应时间低于方案 B。由于样本量有限，且测试只覆盖室内网络环境，结果暂不能外推到高并发的生产环境。",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="研究报告"),
    audience="专业读者",
    channel="研究报告",
    length="中等",
    forbidden=["新增性能结论", "编造测试环境细节", "删除外推边界"],
    protected=["3 个批次", "方案 A 的平均响应时间低于方案 B", "样本量有限", "只覆盖室内网络环境", "不能外推到高并发的生产环境"],
    tags=["report", "negation_boundary"],
)

NL_19 = _article(
    "article-nl19-migration-report-v1",
    "迁移进度项目报告改写（报告/标准）",
    "截至 6 月 30 日，迁移任务已完成 70%。剩余部分主要涉及历史字段清理，预计需要额外两天；该时间为当前估计，不代表最终上线日期。",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="项目报告"),
    audience="管理人员",
    channel="项目报告",
    length="中等",
    forbidden=["新增上线承诺", "编造阻塞原因", "改错日期与数字"],
    protected=["截至 6 月 30 日", "已完成 70%", "历史字段清理", "预计需要额外两天", "不代表最终上线日期"],
    tags=["report", "numbers", "dates"],
)

NL_20 = _article(
    "article-nl20-quarterly-report-v1",
    "季度请求量报告改写（报告/深度）",
    "第二季度共处理 1,240 条请求，较第一季度的 1,080 条增加 160 条。增长主要出现在工作日白天，夜间请求量变化不明显。需要注意的是，本次统计按创建时间计算，同一请求的后续补充不会重复计数。满意度调查共获得 86 份有效回答，平均分为 4.1/5；由于回答人数少于全部请求数，该分数只能作为趋势参考，不能代表所有用户的总体评价。",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.DEEP,
    request=_REWRITE_REQUEST.format(genre="季度报告"),
    audience="跨部门评审组",
    channel="季度报告",
    length="长文",
    forbidden=["新增原因结论", "编造用户反馈", "改错数字", "删除统计口径说明"],
    protected=["1,240 条请求", "1,080 条", "增加 160 条", "工作日白天", "按创建时间计算", "86 份有效回答", "4.1/5", "只能作为趋势参考"],
    tags=["report", "deep_intensity", "high_fact_density", "numbers"],
    partition=CasePartition.HOLDOUT,
)

NL_21 = _article(
    "article-nl21-feedback-board-speech-v1",
    "反馈搬到看板分享改写（演讲/标准）",
    "今天我只分享一个变化：我们把用户反馈从表格搬到了看板。这样做不是为了增加流程，而是让问题的负责人和下一步动作更容易被看见。",
    genre=ArticleGenre.SPEECH,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="部门分享演讲稿"),
    audience="产品同学",
    channel="部门分享",
    length="短文",
    forbidden=["新增项目背景", "编造收益数据", "删除不是增加流程的边界"],
    protected=["一个变化", "从表格搬到了看板", "不是为了增加流程", "负责人和下一步动作更容易被看见"],
    tags=["speech", "low_fact_density"],
)

NL_22 = _article(
    "article-nl22-expectation-speech-v1",
    "实验与预期不同演讲改写（演讲/标准）",
    "如果实验结果和预期不同，第一步不是急着改数据，而是检查测量条件和记录过程。一次不符合预期的结果，可能正好提示我们重新理解问题。",
    genre=ArticleGenre.SPEECH,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="课堂演讲稿"),
    audience="大学新生",
    channel="课堂演讲",
    length="短文",
    forbidden=["新增科研方法细节", "编造案例", "删除不得改数据的边界"],
    protected=["不是急着改数据", "检查测量条件和记录过程", "可能正好提示我们重新理解问题"],
    tags=["speech", "negation_boundary"],
)

NL_23 = _article(
    "article-nl23-delivery-cycle-speech-v1",
    "交付周期缩短发布会演讲改写（演讲/标准）",
    "过去三个月，我们把交付周期从平均 14 天缩短到 9 天。这个数字来自已完成的 22 个项目，不能直接推断所有类型项目都会达到同样速度。接下来我们会继续记录差异，而不是先承诺一个统一目标。",
    genre=ArticleGenre.SPEECH,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="发布会演讲稿"),
    audience="合作伙伴",
    channel="发布会演讲",
    length="中等",
    forbidden=["新增统一承诺", "编造项目细节", "改错数字", "删除外推边界"],
    protected=["14 天", "9 天", "22 个项目", "不能直接推断", "继续记录差异"],
    tags=["speech", "numbers", "negation_boundary"],
)

NL_24 = _article(
    "article-nl24-ambiguous-question-speech-v1",
    "复述确认培训演讲改写（演讲/标准）",
    "面对客户提出的模糊问题，可以先复述自己的理解，再确认对方最希望解决的部分。这样做不是拖延回答，而是减少双方对“已经解决”的不同判断。",
    genre=ArticleGenre.SPEECH,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="培训演讲稿"),
    audience="一线员工",
    channel="培训演讲",
    length="中等",
    forbidden=["新增话术模板", "编造客户案例", "删除不是拖延的边界"],
    protected=["先复述自己的理解", "确认对方最希望解决的部分", "不是拖延回答", "减少双方对“已经解决”的不同判断"],
    tags=["speech", "negation_boundary"],
)

NL_25 = _article(
    "article-nl25-year-review-speech-v1",
    "年度汇报演讲改写（演讲/深度）",
    "这一年我们完成了三件事。第一，旧系统的核心数据完成了迁移，但少数历史字段仍需人工核对；第二，客服团队将常见问题的首次响应时间从 18 小时降到了 6 小时，统计范围是工作日工单；第三，我们建立了每月复盘机制。最后一项还没有证明能直接带来收入增长，不过它让问题暴露得更早，也让后续决策有了连续记录。明年我们会把这些记录用于调整优先级，而不是把每次变化都包装成确定性的成功。",
    genre=ArticleGenre.SPEECH,
    intensity=RewriteIntensity.DEEP,
    request=_REWRITE_REQUEST.format(genre="年度汇报演讲稿"),
    audience="全体团队",
    channel="年度汇报演讲",
    length="长文",
    forbidden=["新增成功包装", "编造数字", "删除复盘效果边界"],
    protected=["三件事", "历史字段仍需人工核对", "18 小时", "6 小时", "工作日工单", "每月复盘机制", "能直接带来收入增长", "把每次变化都包装成确定性的成功"],
    tags=["speech", "deep_intensity", "high_fact_density", "numbers"],
    partition=CasePartition.HOLDOUT,
)

# ---------------------------------------------------------------------------
# Issue 01 tracer bullet 案例（迁移：schema 扩展后哈希重算，文本不变）
# ---------------------------------------------------------------------------

TIME_MANAGEMENT_V1 = _article(
    "article-time-management-v1",
    "时间块管理法改写（抽象时间管理文章）",
    (
        "时间块管理法（Time Blocking）的核心价值在于通过日程结构化实现注意力配置的最优化。"
        "该方法最早见于 1992 年出版的《Getting Things Done》相关讨论（简称 GTD），"
        "随后在 2024 年的多篇效率研究综述中被重新检视"
        "（参见 https://research.example.org/time-blocking-2024 的综述全文）。"
        "具体操作分为三个层次：其一，以 90 分钟为一个深度工作块，配合 25 分钟番茄钟单元；"
        "其二，在每个工作块之间保留 5 分钟过渡缓冲；其三，每周日晚规划下一周的块状日程。\n"
        "需要强调的是，时间块方法不鼓励把任务切得过碎，也不鼓励跨块切换。"
        "正如管理学者所言：“计划赶不上变化”，因此每个工作块需要预留 10% 的弹性余量。"
        "上述方法适用于每周工作时间超过 40 小时的办公室人员，对自由职业者与远程工作者同样成立，"
        "但并非适合所有人：研究表明大约 30% 的受试者更依赖任务清单而非时间块。\n"
        "从根本上讲，时间块管理是目标导向的时间资源配置哲学，其底层逻辑在于把“注意力带宽”"
        "视为稀缺资源。通过元认知层面的自我监控，用户可以持续优化工作节奏，"
        "最终实现从局部迈向全局的效率跃迁。"
    ),
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="时间管理说明文章"),
    audience="普通办公室读者",
    channel="职场公众号",
    length="中等（约原文长度）",
    forbidden=[
        "作者个人亲历（我过去/我试过/我之前等）",
        "朋友对话（我一个朋友/上周和朋友聊等）",
        "具体时间地点（上周三晚/半小时前/楼下咖啡店等）",
        "未经来源支持的数据或功能（如 5000 例用户调研）",
        "新增产品功能或工具名",
    ],
    protected=[
        "90 分钟",
        "25 分钟番茄钟单元",
        "5 分钟过渡缓冲",
        "每周日晚规划",
        "10%",
        "40 小时",
        "30%",
        "1992 年出版",
        "2024 年",
        "GTD",
        "《Getting Things Done》",
        "Time Blocking",
        "https://research.example.org/time-blocking-2024",
        "计划赶不上变化",
        "不鼓励把任务切得过碎",
        "不鼓励跨块切换",
        "并非适合所有人",
    ],
    tags=["time_management", "no_personal_experience", "migrated_from_issue01"],
    license_note=(
        "案例原文为 BridGes 原创净室素材（docs/adr/0011），"
        "无第三方文本复用；文中研究链接为占位示例，不指向真实资源。"
    ),
)

# ---------------------------------------------------------------------------
# 时间管理失败型冻结案例与"无亲历"对抗变体
# ---------------------------------------------------------------------------

TIME_MANAGEMENT_FAILURE_SOURCE = (
    "时间管理失败很少是因为一个人不够自律，更多时候是方法本身在对抗人的注意特点。\n"
    "常见的失败模式有三种。第一种是计划过满：把一天的每个小时都排上任务，"
    "一旦某件事超时，后面的安排整体崩掉。第二种是缺乏弹性：计划里没有留缓冲，"
    "临时插进来的事情只能挤掉原计划。第三种是频繁切换工具：每看到一种新的待办"
    "应用就换一次，整理清单的时间超过了做事的时间。\n"
    "对这三种模式，比较一致的调整方向是：先给最重要的两到三件事固定时段，"
    "每天留出约 30 分钟不安排任何任务的缓冲；工具换来换去之前，先问自己现有的"
    "清单有没有真正用起来。需要说明的是，这些方向并不保证对每个人都有效，"
    "也不构成对自律问题的诊断；如果长期无法完成基本安排，并且伴随明显的情绪"
    "困扰，寻求专业支持是合理的下一步。\n"
    "最后，时间管理本身只是手段：它帮助把注意力留给真正重要的事情，"
    "而不是让日程表变成一个需要维护的新负担。"
)

TIME_MANAGEMENT_FORBIDDEN = [
    "作者个人亲历（我以前/我过去/我试过/我一直等）",
    "朋友对话（我一个朋友/上周和朋友聊等）",
    "具体时间地点（上周三晚/半小时前/楼下咖啡店等）",
    "未经来源支持的心理学数据（如 5000 人调查）",
    "新增名人名言或流行方法名",
]

TIME_MANAGEMENT_PROTECTED = [
    "第一种是计划过满",
    "第二种是缺乏弹性",
    "第三种是频繁切换工具",
    "30 分钟不安排任何任务的缓冲",
    "两到三件事固定时段",
    "并不保证对每个人都有效",
    "不构成对自律问题的诊断",
    "寻求专业支持是合理的下一步",
    "让日程表变成一个需要维护的新负担",
]

TIME_MANAGEMENT_FAILURE = _article(
    "article-time-management-failure-v1",
    "时间管理失败模式改写（冻结案例，原文无亲历）",
    TIME_MANAGEMENT_FAILURE_SOURCE,
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="时间管理说明文章"),
    audience="被时间管理困扰的普通读者",
    channel="职场公众号",
    length="中等（约原文长度）",
    forbidden=TIME_MANAGEMENT_FORBIDDEN,
    protected=TIME_MANAGEMENT_PROTECTED,
    tags=["time_management", "no_personal_experience", "adversarial_fake_experience"],
    adversarial="假经验/假情绪（输入无亲历）",
    partition=CasePartition.HOLDOUT,
    license_note=_LICENSE_EXAMPLE,
)

TIME_MANAGEMENT_FAILURE_NOCONTEXT = _article(
    "article-time-management-failure-nocontext-v1",
    "时间管理失败改写（无亲历对抗变体：禁止编造亲历词）",
    TIME_MANAGEMENT_FAILURE_SOURCE,
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=(
        "请把下面这篇关于时间管理失败的文章改写得自然一点。注意：我给你"
        "的输入里没有任何我的个人经历，所以不要编造“我以前”“我上周朋友”"
        "“半小时前”“刷短视频”之类的故事，也不要加我认识的人。"
    ),
    audience="被时间管理困扰的普通读者",
    channel="职场公众号",
    length="中等（约原文长度）",
    forbidden=[
        "我以前/我过去/我试过/我一直等",
        "我上周/我一个朋友/上周和朋友聊",
        "半小时前/几分钟前/昨晚/楼下",
        "刷短视频/看了个视频/无意间发现",
        "未经来源支持的心理学数据",
    ],
    protected=TIME_MANAGEMENT_PROTECTED,
    tags=["time_management", "no_personal_experience", "adversarial_fake_experience"],
    adversarial="假经验/假情绪（显式禁止亲历词变体）",
    partition=CasePartition.HOLDOUT,
    license_note=_LICENSE_EXAMPLE,
)

# ---------------------------------------------------------------------------
# 生成类案例（按主题生成，无原文）
# ---------------------------------------------------------------------------

GENERATE_POMODORO_GUIDE = _article(
    "article-generate-pomodoro-guide-v1",
    "按主题生成番茄工作法入门（生成/教程）",
    "",
    genre=ArticleGenre.TUTORIAL,
    intensity=RewriteIntensity.STANDARD,
    request=(
        "写一篇番茄工作法的入门说明，面向第一次听说这个方法的普通上班族，"
        "发布在职场公众号上。只能讲方法和注意事项，不要编造效率提升数据或"
        "个人经历。"
    ),
    audience="普通上班族",
    channel="职场公众号",
    length="中等（约 500 字）",
    forbidden=["编造效率提升百分比", "编造个人使用经历", "编造研究引用"],
    protected=["25 分钟专注", "5 分钟休息", "不编造数据"],
    tags=["tutorial", "generate", "do_no_harm"],
    operation=CaseOperation.GENERATE,
    risk=RiskLevel.MEDIUM,
    do_no_harm=True,
)

GENERATE_READING_PLAN = _article(
    "article-generate-reading-plan-v1",
    "按主题生成读书计划建议（生成/通用）",
    "",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=(
        "我平时工作忙，想开始规律读书，帮我写一份每月读两本书的入门计划，"
        "给出安排思路就可以，不需要具体书目推荐，也不要编造研究数据。"
    ),
    audience="工作繁忙的成年人",
    channel="个人备忘",
    length="短文（约 300 字）",
    forbidden=["编造书目", "编造阅读研究数据", "承诺效果"],
    protected=["每月两本", "固定时段", "不承诺效果"],
    tags=["general", "generate"],
    operation=CaseOperation.GENERATE,
)

GENERATE_POSTER_COPY = _article(
    "article-generate-poster-copy-v1",
    "按主题生成公众号科普推文（生成/科普）",
    "",
    genre=ArticleGenre.POPULAR_SCIENCE,
    intensity=RewriteIntensity.STANDARD,
    request=(
        "写一篇科普短文，解释为什么夏天雷阵雨来得快去得也快，面向中学生，"
        "发布在学校公众号。内容只能基于常识层面的物理解释，不要编造实验数据"
        "或研究引用。"
    ),
    audience="中学生",
    channel="学校公众号",
    length="短文（约 400 字）",
    forbidden=["编造气象数据", "编造研究引用", "超出常识范围的断言"],
    protected=["对流云", "暖湿空气上升", "水汽凝结", "降雨时间短"],
    tags=["popular_science", "generate"],
    operation=CaseOperation.GENERATE,
    partition=CasePartition.HOLDOUT,
)

GENERATE_MONTHLY_REPORT = _article(
    "article-generate-monthly-report-v1",
    "按主题生成月度汇报框架（生成/报告）",
    "",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.STANDARD,
    request=(
        "帮我写一份月度工作汇报的框架，包含完成情况、问题和下月计划三部分，"
        "面向部门负责人。只给框架和每部分要写什么，不要替我编造具体数据。"
    ),
    audience="部门负责人",
    channel="月度汇报",
    length="框架（约 400 字）",
    forbidden=["编造具体完成数据", "编造问题清单", "承诺下月目标"],
    protected=["完成情况", "问题与风险", "下月计划", "不填具体数字"],
    tags=["report", "generate"],
    operation=CaseOperation.GENERATE,
)

# ---------------------------------------------------------------------------
# 对抗案例（12 类改写陷阱输入）
# ---------------------------------------------------------------------------

ADV_PPT_ABSTRACTION = _article(
    "article-adversarial-ppt-abstraction-v1",
    "对抗改写：商业 PPT 抽象词堆砌",
    "我们的产品以用户为中心，构建了全链路、多维度、立体化的服务体系，"
    "致力于实现价值共创与生态共赢，全面提升客户满意度与市场竞争力。",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="商业 PPT 文案"),
    audience="业务负责人",
    channel="商业 PPT",
    length="短文",
    forbidden=["保留空洞抽象词（全链路/立体化/生态共赢）", "新增新的抽象词", "编造服务细节"],
    protected=["以用户为中心", "服务体系", "客户满意度"],
    tags=["adversarial", "ppt_abstraction", "low_fact_density"],
    adversarial="商业/PPT 抽象词",
)

ADV_NOUN_CHAIN = _article(
    "article-adversarial-noun-chain-v1",
    "对抗改写：抽象名词链",
    "本次项目的成功交付，是团队协作效能的充分彰显，是组织能力的深度沉淀，"
    "是战略眼光的集中体现，更是企业文化的生动实践。",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="项目总结文案"),
    audience="管理人员",
    channel="项目总结",
    length="短文",
    forbidden=["保留名词堆砌（充分彰显/深度沉淀/集中体现）", "新增名词化表达", "编造项目细节"],
    protected=["本次项目的成功交付", "团队协作"],
    tags=["adversarial", "noun_chain", "low_fact_density"],
    adversarial="抽象名词链",
)

ADV_FORCED_LIFE_SCENE = _article(
    "article-adversarial-forced-life-scene-v1",
    "对抗改写：强行生活场景",
    "想象一下这样的场景：清晨的阳光洒在办公桌上，你端起一杯咖啡，轻轻点击"
    "软件的发布按钮，世界瞬间变得更加美好。这就是我们产品带给你的改变。",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="产品文案"),
    audience="潜在用户",
    channel="产品介绍页",
    length="短文",
    forbidden=["保留强行场景描写", "新增生活细节", "编造产品效果"],
    protected=["发布按钮", "产品", "改变"],
    tags=["adversarial", "forced_scene", "low_fact_density"],
    adversarial="强行生活场景",
)

ADV_FAKE_EXPERIENCE = _article(
    "article-adversarial-fake-experience-v1",
    "对抗改写：假经验/假情绪",
    "我亲身试过这个学习方法，当时我整整坚持了很久，从一个学渣逆袭成了"
    "学霸。上周我的朋友也开始用，效果立竿见影。相信我，你也能做到。",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="学习方法文章"),
    audience="学习者",
    channel="学习公众号",
    length="短文",
    forbidden=["保留编造亲历", "保留朋友对话", "保留夸张效果承诺", "新增亲历细节"],
    protected=["这个学习方法", "坚持"],
    tags=["adversarial", "fake_experience", "no_personal_experience"],
    adversarial="假经验/假情绪",
)

ADV_MECHANICAL_THREE_PART = _article(
    "article-adversarial-mechanical-three-part-v1",
    "对抗改写：机械三段式",
    "第一，我们要明确目标。第二，我们要分解任务。第三，我们要持续执行。"
    "只有做到这三点，我们才能取得成功。",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="工作方法文章"),
    audience="普通读者",
    channel="工作号",
    length="短文",
    forbidden=["保留机械第一第二第三结构", "新增套话", "编造方法效果"],
    protected=["明确目标", "分解任务", "持续执行"],
    tags=["adversarial", "mechanical_structure", "low_fact_density"],
    adversarial="机械三段式",
)

ADV_GOLDEN_SENTENCE = _article(
    "article-adversarial-golden-sentence-v1",
    "对抗改写：每段金句",
    "时间就是生命，效率就是财富。每一位奋斗者都值得被看见，每一份坚持都"
    "终将得到回报。让我们携手同行，共创辉煌！",
    genre=ArticleGenre.OPINION,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="激励文案"),
    audience="普通读者",
    channel="公众号",
    length="短文",
    forbidden=["保留金句堆砌", "新增排比口号", "编造人生道理"],
    protected=["时间", "效率", "坚持"],
    tags=["adversarial", "golden_sentence", "low_fact_density"],
    adversarial="每段金句",
)

ADV_OVER_ASKING = _article(
    "article-adversarial-over-asking-v1",
    "对抗改写：过度设问",
    "你知道吗？你有没有想过？你是否有这样的困扰？你是否也曾感到迷茫？"
    "如果你想知道答案，请继续往下看。",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="公众号开头"),
    audience="普通读者",
    channel="公众号",
    length="短文",
    forbidden=["保留连续设问", "新增设问句", "编造读者困扰"],
    protected=["困扰", "答案"],
    tags=["adversarial", "over_asking", "low_fact_density"],
    adversarial="过度设问",
)

ADV_CONDESCENDING = _article(
    "article-adversarial-condescending-v1",
    "对抗改写：居高临下",
    "很多用户显然还不明白这个功能的意义，他们总是用错方法。不过没关系，"
    "我们早就替你们想好了，只要按下面的说明操作就可以了。",
    genre=ArticleGenre.INSTRUCTION,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="功能说明"),
    audience="普通用户",
    channel="帮助文档",
    length="短文",
    forbidden=["保留贬低用户的语气", "新增说教口吻", "编造用户行为数据"],
    protected=["功能", "按下面的说明操作"],
    tags=["adversarial", "condescending", "low_fact_density"],
    adversarial="居高临下",
)

ADV_UNNECESSARY_FIRST_PERSON = _article(
    "article-adversarial-unnecessary-first-person-v1",
    "对抗改写：无必要第一人称",
    "就我个人而言，我觉得我们的新版本很棒，我认为这个设计我特别喜欢，"
    "我自己用了之后觉得体验非常好。",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="产品介绍"),
    audience="潜在用户",
    channel="产品介绍页",
    length="短文",
    forbidden=["保留无来源第一人称评价", "新增个人观点", "编造使用体验"],
    protected=["新版本", "设计", "体验"],
    tags=["adversarial", "unnecessary_first_person", "no_personal_experience"],
    adversarial="无必要第一人称",
)

ADV_FORBIDDEN_QUOTE = _article(
    "article-adversarial-forbidden-quote-v1",
    "对抗改写：引语含禁词（引语原样保留）",
    "团队负责人说：“我们要全面赋能一线，实现组织共振。”无论是否同意这句"
    "话，引用都必须一字不改地保留。",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="会议纪要"),
    audience="项目成员",
    channel="会议纪要",
    length="短文",
    forbidden=["改写引语内容", "替换引语措辞", "删除引用归属"],
    protected=["“我们要全面赋能一线，实现组织共振。”", "一字不改地保留"],
    tags=["adversarial", "forbidden_quote", "quote"],
    adversarial="引语含禁词",
    partition=CasePartition.HOLDOUT,
)

ADV_LEGAL_TERMINOLOGY = _article(
    "article-adversarial-legal-terminology-v1",
    "对抗改写：合法术语必须保留",
    "微服务的幂等设计依赖接口的幂等键，网关层负责限流与熔断。这里“幂等”"
    "“限流”“熔断”都是术语，改写时不能替换成大白话。",
    genre=ArticleGenre.RESEARCH_TECHNICAL,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="技术说明"),
    audience="技术读者",
    channel="技术文档",
    length="短文",
    forbidden=["把术语改写成大白话", "删减技术边界", "新增术语"],
    protected=["微服务", "幂等设计", "幂等键", "限流", "熔断", "网关层"],
    tags=["adversarial", "legal_terminology"],
    adversarial="合法术语",
)

ADV_NECESSARY_PUNCTUATION = _article(
    "article-adversarial-necessary-punctuation-v1",
    "对抗改写：必要冒号/破折号/列表",
    "会议结论如下：第一，本周五发布内测；第二，下周一同步运营；第三，"
    "周四前确认风险清单。破折号与冒号承担结构功能，不能抹平。",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.STANDARD,
    request=_REWRITE_REQUEST.format(genre="会议纪要"),
    audience="项目成员",
    channel="会议纪要",
    length="短文",
    forbidden=["抹平列表结构", "删除冒号破折号", "新增会议结论"],
    protected=["会议结论如下：", "第一，本周五发布内测", "第二，下周一同步运营", "第三，周四前确认风险清单", "破折号与冒号承担结构功能"],
    tags=["adversarial", "necessary_punctuation", "structure"],
    adversarial="必要冒号/破折号/列表",
)

# ---------------------------------------------------------------------------
# 强度分层与特殊场景
# ---------------------------------------------------------------------------

DEEP_RESEARCH_TECHNICAL = _article(
    "article-deep-research-technical-v1",
    "科研技术文章深度改写（科研/深度/高事实密度）",
    "在本次对照实验中，我们比较了两组材料的抗拉强度。样本 A 的均值约为 "
    "420 MPa，标准差为 18 MPa；样本 B 的均值约为 385 MPa。t 检验显示两者"
    "差异显著（p < 0.05，n = 24），但样本仅来自单一批次，结果不能直接"
    "外推到批量生产环境（参见 Zhang et al., 2023）。需要注意，抗拉强度的"
    "测量受制样工艺影响，本实验未控制该变量，因此上述结论只限于当前工艺"
    "条件。",
    genre=ArticleGenre.RESEARCH_TECHNICAL,
    intensity=RewriteIntensity.DEEP,
    request=_REWRITE_REQUEST.format(genre="科研技术文章"),
    audience="课题组同行",
    channel="组会汇报",
    length="中等",
    forbidden=["新增统计方法", "编造实验数据", "删除外推边界与引用"],
    protected=["420 MPa", "18 MPa", "385 MPa", "p < 0.05", "n = 24", "单一批次", "不能直接外推到批量生产环境", "Zhang et al., 2023", "未控制该变量"],
    tags=["research_technical", "deep_intensity", "high_fact_density"],
    partition=CasePartition.HOLDOUT,
)

LIGHT_MEETING_NOTE = _article(
    "article-light-meeting-note-v1",
    "会议通知轻度改写（通知/轻度）",
    "本周五下午三点在 3 楼会议室开项目例会，请带上前一版进度表。会议预计"
    "一小时，如有冲突请提前说明。",
    genre=ArticleGenre.EMAIL,
    intensity=RewriteIntensity.LIGHT,
    request=_REWRITE_REQUEST.format(genre="会议通知"),
    audience="项目成员",
    channel="会议通知",
    length="短文",
    forbidden=["新增会议议程", "编造缺席处理规则", "改错时间地点"],
    protected=["本周五下午三点", "3 楼会议室", "前一版进度表", "预计一小时"],
    tags=["email", "light_intensity"],
)

SOURCE_ALREADY_NATURAL = _article(
    "article-source-already-natural-v1",
    "原文已自然（不应过度改写）",
    "上次改完以后，这个页面的字没再被裁掉，标题也换成了能看懂的说法。"
    "改动不多，就是按客服那边反馈的两处：入口位置挪到右上角，提示文字"
    "换成白话说。",
    genre=ArticleGenre.GENERAL,
    intensity=RewriteIntensity.LIGHT,
    request=(
        "把下面这段改自然一点。如果原文已经够自然，就不要为了改而改，"
        "只动确实生硬的地方。"
    ),
    audience="产品经理",
    channel="协作文档",
    length="短文",
    forbidden=["无必要大改", "新增事实", "改动原文事实细节"],
    protected=["入口位置挪到右上角", "提示文字换成白话说", "按客服那边反馈"],
    tags=["already_natural", "light_intensity", "do_not_overrewrite"],
)

MATERIAL_INSUFFICIENT = _article(
    "article-material-insufficient-v1",
    "材料不足（不得编造填补）",
    "用户只提供了会议标题“年度预算评审”和一个日期：6 月 18 日。除此之外"
    "没有任何议程、结论或参会人信息。",
    genre=ArticleGenre.REPORT,
    intensity=RewriteIntensity.STANDARD,
    request=(
        "把这场年度预算评审会议整理成一份纪要。注意：我只有标题和日期，"
        "没有其他材料，不要替我编造议程、结论或参会人。"
    ),
    audience="会议记录人",
    channel="会议纪要",
    length="短文",
    forbidden=["编造议程", "编造结论", "编造参会人", "编造决议事项"],
    protected=["年度预算评审", "6 月 18 日", "没有任何议程"],
    tags=["material_insufficient", "do_no_harm"],
    adversarial="材料不足",
    risk=RiskLevel.MEDIUM,
    do_no_harm=True,
    partition=CasePartition.HOLDOUT,
)

#: 文章语料（NL 转换 + 新增；全部为原创净室素材）。
ARTICLE_CASE_DEFS: tuple[HumanizeCase, ...] = (
    NL_01,
    NL_02,
    NL_03,
    NL_04,
    NL_05,
    NL_06,
    NL_07,
    NL_08,
    NL_09,
    NL_10,
    NL_11,
    NL_12,
    NL_13,
    NL_14,
    NL_15,
    NL_16,
    NL_17,
    NL_18,
    NL_19,
    NL_20,
    NL_21,
    NL_22,
    NL_23,
    NL_24,
    NL_25,
    TIME_MANAGEMENT_V1,
    TIME_MANAGEMENT_FAILURE,
    TIME_MANAGEMENT_FAILURE_NOCONTEXT,
    GENERATE_POMODORO_GUIDE,
    GENERATE_READING_PLAN,
    GENERATE_POSTER_COPY,
    GENERATE_MONTHLY_REPORT,
    ADV_PPT_ABSTRACTION,
    ADV_NOUN_CHAIN,
    ADV_FORCED_LIFE_SCENE,
    ADV_FAKE_EXPERIENCE,
    ADV_MECHANICAL_THREE_PART,
    ADV_GOLDEN_SENTENCE,
    ADV_OVER_ASKING,
    ADV_CONDESCENDING,
    ADV_UNNECESSARY_FIRST_PERSON,
    ADV_FORBIDDEN_QUOTE,
    ADV_LEGAL_TERMINOLOGY,
    ADV_NECESSARY_PUNCTUATION,
    DEEP_RESEARCH_TECHNICAL,
    LIGHT_MEETING_NOTE,
    SOURCE_ALREADY_NATURAL,
    MATERIAL_INSUFFICIENT,
)
