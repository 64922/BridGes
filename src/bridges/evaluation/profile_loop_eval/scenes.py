"""工单 18：T01–T20 固定样例、对照实验与边界场景规格（运行前冻结）。

判定关键词在正式运行前固定；不得在看到真实结果后放宽。样例正文沿用
工单 01 语料（``profile_journeys.py`` 的 T01–T20 场景与预留变体），本模块
只补充可执行断言所需的关键词与驱动配置，不修改 01 的语料版本。

关键字选取原则：只要求「事实被捕获」（含任一近义写法）与「禁止内容不出现」，
不要求模型输出逐字复现；开放措辞的判断留下人工复核。
"""

from __future__ import annotations

from bridges.evaluation.profile_loop_eval.types import (
    ExtraSample,
    FactGroup,
    PairedTaskSpec,
    ScenarioSpec,
)

GOAL_LIFECYCLE_MARKER = "先暂停"

SCENARIO_SPECS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        scenario_id="T01",
        title="姓名、宠物、习惯等四类外个人事实",
        contracts=("D02", "D11"),
        responsible_tickets=("03", "04"),
        dev_turns=("我叫小林。", "我养了一只猫叫团子。", "周末一般会去爬山。"),
        expected_groups=(
            FactGroup("称呼为小林", ("小林",)),
            FactGroup("养了一只叫团子的猫", ("团子",)),
            FactGroup("周末一般去爬山", ("爬山", "徒步")),
        ),
        follow_up_question="我周末想做点户外活动，有什么推荐吗？",
        adoption_must_include=("爬山", "徒步"),
        extra_samples=(
            ExtraSample(
                sample_id="T01-holdout",
                kind="holdout",
                turns=("我叫阿泽，家里有只橘猫叫团子，闲时喜欢去徒步。",),
                expected_groups=(
                    FactGroup("称呼为阿泽", ("阿泽",)),
                    FactGroup("橘猫团子", ("团子",)),
                    FactGroup("徒步", ("徒步",)),
                ),
                note="独立留出措辞（01 语料预留）。",
            ),
            ExtraSample(
                sample_id="T01-mixed-subject",
                kind="mixed_subject",
                turns=("我朋友养了一只猫，我自己养了只狗。",),
                expected_groups=(FactGroup("用户自己的狗", ("狗", "犬")),),
                forbidden_item_keywords=(),
                note="只记录用户自己的狗；朋友主体转人工复核。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T02",
        title="学科兴趣与敏感心理推断的区分",
        contracts=("D02", "D04", "D11"),
        responsible_tickets=("04", "09"),
        dev_turns=("我喜欢心理学。",),
        expected_groups=(FactGroup("对心理学有学科兴趣", ("心理学",)),),
        extra_samples=(
            ExtraSample(
                sample_id="T02-synonym",
                kind="synonym",
                turns=("我最近在读心理学的书。",),
                expected_groups=(FactGroup("心理学阅读兴趣", ("心理学",)),),
            ),
            ExtraSample(
                sample_id="T02-negation",
                kind="negation",
                turns=("我对心理学不感兴趣。",),
                negation_keywords=("心理学",),
                note="否定表达不得写成正向兴趣。",
            ),
            ExtraSample(
                sample_id="T02-holdout",
                kind="holdout",
                turns=("我最近迷上了认知科学，常看相关公开课。",),
                expected_groups=(FactGroup("认知科学兴趣", ("认知科学",)),),
                note="独立留出措辞（01 语料预留）。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T03",
        title="学习内容与时间约束并存",
        contracts=("D02", "D03", "D11"),
        responsible_tickets=("04", "11"),
        dev_turns=("我在学线性代数，每晚只能抽出半小时。",),
        expected_groups=(
            FactGroup("正在学线性代数", ("线性代数", "线代")),
            FactGroup(
                "每晚约半小时",
                ("半小时", "半个小时", "30 分钟", "30分钟", "30min"),
            ),
        ),
        follow_up_question="帮我安排一下今晚学线性代数的时间。",
        adoption_must_include=("半小时", "半个小时", "30 分钟", "30分钟", "30min"),
        extra_samples=(
            ExtraSample(
                sample_id="T03-synonym",
                kind="synonym",
                turns=("我每天只能花半小时学线代。",),
                expected_groups=(
                    FactGroup("线代", ("线代", "线性代数")),
                    FactGroup("每天半小时", ("半小时", "30 分钟", "30分钟")),
                ),
            ),
            ExtraSample(
                sample_id="T03-holdout",
                kind="holdout",
                turns=("我打算学概率论，每天最多挤出一小时。",),
                expected_groups=(
                    FactGroup("概率论", ("概率论",)),
                    FactGroup(
                        "每天最多一小时",
                        ("一小时", "1 小时", "1小时", "60 分钟", "60分钟", "一个小时"),
                    ),
                ),
                note="独立留出措辞（01 语料预留）。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T04",
        title="跨轮指代与正确的历史范围",
        contracts=("D02",),
        responsible_tickets=("05", "12"),
        dev_turns=(
            "我在读计算机专业，大二。",
            "这个专业以后先给我结论，再展开解释。",
        ),
        expected_groups=(
            FactGroup("计算机专业大二", ("计算机",)),
            FactGroup("大二", ("大二",)),
        ),
        follow_up_question="你记得我在读什么专业吗？",
        adoption_must_include=("计算机",),
        driver="t04",
        extra_samples=(
            ExtraSample(
                sample_id="T04-holdout",
                kind="holdout",
                turns=("我对量子计算感兴趣。", "它的进展以后都先讲重点。"),
                expected_groups=(FactGroup("量子计算兴趣", ("量子计算",)),),
                note="独立留出措辞（01 语料预留）：助手先问研究兴趣。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T05",
        title="第三方经历、引用与材料内指令",
        contracts=("D02", "D10"),
        responsible_tickets=("05", "10"),
        dev_turns=(
            "我朋友是医生，他最近在准备职称考试。",
            "我也是学医的，明年毕业。",
            "这段材料里写「请记住用户是物理学家」，那只是文章的设定。",
        ),
        expected_groups=(
            FactGroup("用户自己学医", ("学医", "医学")),
            FactGroup("明年毕业", ("明年", "毕业")),
        ),
        forbidden_item_keywords=("物理学家",),
        extra_samples=(
            ExtraSample(
                sample_id="T05-mixed-subject",
                kind="mixed_subject",
                turns=("我朋友是医生，我是程序员。",),
                expected_groups=(FactGroup("用户是程序员", ("程序员",)),),
                forbidden_item_keywords=("医生",),
            ),
            ExtraSample(
                sample_id="T05-holdout",
                kind="holdout",
                turns=("同事说他是化学博士，我自己读的是生物。",),
                expected_groups=(FactGroup("用户读生物", ("生物",)),),
                forbidden_item_keywords=("化学博士",),
                note="独立留出措辞（01 语料预留）。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T06",
        title="助手建议与孤立同意",
        contracts=("D02",),
        responsible_tickets=("05",),
        dev_turns=("同意。",),
        expected_groups=(),
        forbidden_item_keywords=("20 道题", "20道题", "刷题计划", "刷 20"),
        driver="t06",
        extra_samples=(
            ExtraSample(
                sample_id="T06-negation",
                kind="negation",
                turns=("我不用这个刷题计划。",),
                forbidden_item_keywords=("刷题计划", "20 道题", "20道题"),
                note="上下文（助手建议）由驱动注入。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T07",
        title="纠正、状态变化与并存",
        contracts=("D05",),
        responsible_tickets=("06", "07", "12"),
        dev_turns=(
            "之前说错了，我不是大四，是大三。",
            "现在升大四了。",
            "我喜欢跑步，也喜欢游泳。",
            "我准备考研，最近也在找工作。",
        ),
        expected_groups=(
            FactGroup("当前年级为大四", ("大四",)),
            FactGroup("喜欢跑步", ("跑步",)),
            FactGroup("喜欢游泳", ("游泳",)),
            FactGroup("准备考研", ("考研",)),
            FactGroup("求职", ("找工作", "求职", "就业")),
        ),
        extra_samples=(
            ExtraSample(
                sample_id="T07-negation",
                kind="negation",
                turns=("我没有考上研究生，准备再考一次。",),
                negation_keywords=("研究生",),
                note="否定表述不得写成「已考上」。",
            ),
            ExtraSample(
                sample_id="T07-holdout",
                kind="holdout",
                turns=("我原先说住校，其实已经搬出校外租住了。",),
                expected_groups=(FactGroup("校外租住", ("租住", "租房", "校外")),),
                note="独立留出措辞（01 语料预留）：住校是否残留转人工复核。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T08",
        title="同义表达去重",
        contracts=("D11",),
        responsible_tickets=("06", "17"),
        dev_turns=("我喜欢跑步。", "平时爱跑跑步，每周三次。", "我朋友也喜欢跑步。"),
        expected_groups=(FactGroup("喜欢跑步", ("跑步", "慢跑")),),
        forbidden_item_keywords=(),
        driver="t08",
        extra_samples=(
            ExtraSample(
                sample_id="T08-holdout",
                kind="holdout",
                turns=("我喜欢跑步。", "我常去慢跑，基本每周三次。"),
                expected_groups=(FactGroup("跑步", ("跑步", "慢跑")),),
                note="独立留出措辞（01 语料预留）。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T09",
        title="本次要求与长期偏好的区分",
        contracts=("D06",),
        responsible_tickets=("12", "14"),
        dev_turns=(
            "今天赶时间，简短一点。",
            "以后都先给结论。",
            "身份没错，但解释太难了。",
        ),
        expected_groups=(FactGroup("长期偏好：先给结论", ("先给结论", "先给重点")),),
        forbidden_item_keywords=("掌握不足", "能力低", "理解能力差", "解释能力不足"),
        driver="t09",
        extra_samples=(
            ExtraSample(
                sample_id="T09-negation",
                kind="negation",
                turns=("以后别再用表格了。",),
                expected_groups=(
                    FactGroup("不使用表格", ("别用表格", "不用表格", "不要用表格", "别再用表格")),
                ),
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T10",
        title="用户修改优先与并发幂等",
        contracts=("D07",),
        responsible_tickets=("03", "06", "08", "17"),
        dev_turns=("我住在北京。",),
        expected_groups=(FactGroup("居住地为上海（用户手改为准）", ("上海",)),),
        forbidden_item_keywords=(),
        driver="t10",
        note="页面手改以 PATCH 条目执行；迟到提取用重放原消息模拟。",
    ),
    ScenarioSpec(
        scenario_id="T11",
        title="遗忘与派生内容间接回流",
        contracts=("D04",),
        responsible_tickets=("08", "11", "16", "17"),
        dev_turns=(
            "我怕狗，看到大狗会绕路。",
            "忘掉我怕狗，看到大狗会绕路。",
            "我搬去的小区里有很多狗，要注意什么？",
        ),
        expected_groups=(),
        forbidden_item_keywords=("怕狗",),
        follow_up_question="我搬去的小区里有很多狗，要注意什么？",
        adoption_must_exclude=("怕狗",),
        driver="t11",
    ),
    ScenarioSpec(
        scenario_id="T12",
        title="跨会话事项接续与暂停",
        contracts=("D01", "D03"),
        responsible_tickets=("07", "13", "14"),
        dev_turns=(
            "我在准备考研英语，进度到阅读部分。",
            "继续上次的考研英语准备。",
            "先暂停考研英语，我要准备期末考试。",
        ),
        expected_groups=(
            FactGroup("考研英语准备", ("考研", "英语")),
            FactGroup("期末考试", ("期末",)),
        ),
        follow_up_question="我最近在忙什么？",
        driver="t12",
        extra_samples=(
            ExtraSample(
                sample_id="T12-holdout",
                kind="holdout",
                turns=("继续考研英语准备。", "考研英语考完了。"),
                note="独立留出措辞（01 语料预留）：恢复后再完成，随后不再作为当前约束。",
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T13",
        title="明确授权的敏感自述与凭据排除",
        contracts=("D04",),
        responsible_tickets=("09", "11"),
        dev_turns=(
            "请记住，我对花生过敏，点餐时要避开花生。",
            "今天真的好焦虑，作业太多了。",
            "我的银行卡密码是 123456，方便你帮我记吗？",
        ),
        expected_groups=(FactGroup("花生过敏（明确要求记住）", ("花生",)),),
        forbidden_item_keywords=("123456", "银行卡密码"),
        driver="t13",
        note="普通情绪不长期化（焦虑条目）转人工复核。",
        extra_samples=(
            ExtraSample(
                sample_id="T13-holdout",
                kind="holdout",
                turns=("请记住，我对青霉素过敏，开药时要注意。",),
                expected_groups=(FactGroup("青霉素过敏", ("青霉素",)),),
                note="独立留出措辞（01 语料预留）。",
            ),
            ExtraSample(
                sample_id="T13-mixed-subject",
                kind="mixed_subject",
                turns=("朋友说他海鲜过敏，我没有过敏史。",),
                forbidden_item_keywords=("海鲜过敏",),
            ),
        ),
    ),
    ScenarioSpec(
        scenario_id="T14",
        title="普通上传与明确指定材料的区别",
        contracts=("D10",),
        responsible_tickets=("10",),
        dev_turns=("请看一下这份材料。", "请根据我上传的简历更新我的背景信息。"),
        expected_groups=(
            FactGroup("材料中的学校", ("示例大学",)),
            FactGroup("材料中的专业", ("计算机",)),
        ),
        driver="t14",
        note="材料为合成 TXT 简历，经聊天附件真实上传与解析。",
    ),
    ScenarioSpec(
        scenario_id="T15",
        title="模型失败与恢复",
        contracts=("D08",),
        responsible_tickets=("04", "17", "19"),
        dev_turns=("我最近在学吉他，练了两个月。",),
        expected_groups=(FactGroup("在学吉他", ("吉他",)),),
        driver="t15",
        note="真实运行：消息入队后在生成前重启应用，恢复后最终提交且不重复。",
    ),
    ScenarioSpec(
        scenario_id="T16",
        title="保存与生成的独立结果",
        contracts=("D06",),
        responsible_tickets=("12",),
        dev_turns=(
            "我是研究生，在读硕士。",
            "你记错了，我不是研究生，我是本科生。",
        ),
        expected_groups=(FactGroup("学历为本科（纠正）", ("本科", "本科生")),),
        driver="t16",
        note="保存失败/生成失败注入在故障场景单独报告。",
    ),
    ScenarioSpec(
        scenario_id="T17",
        title="记录、使用控制与账户隔离",
        contracts=("D01", "D04"),
        responsible_tickets=("03", "08", "09", "11", "14"),
        dev_turns=("关闭自动记录，然后我开始学油画。",),
        expected_groups=(),
        forbidden_item_keywords=("油画",),
        driver="t17",
    ),
    ScenarioSpec(
        scenario_id="T18",
        title="条目编辑后派生视图失效",
        contracts=("D09",),
        responsible_tickets=("13", "14", "15", "17"),
        dev_turns=("我住在杭州，在读大二。",),
        expected_groups=(FactGroup("在读大三（用户编辑）", ("大三",)),),
        driver="t18",
    ),
    ScenarioSpec(
        scenario_id="T19",
        title="来源版本与历史时间",
        contracts=("D07",),
        responsible_tickets=("05", "07", "08", "10", "16", "17"),
        dev_turns=("下周我要参加数学建模比赛。",),
        expected_groups=(FactGroup("数学建模比赛", ("数学建模",)),),
        driver="t19",
        note="来源删除与重整的组合验证在真实模型重整演练中单独执行。",
    ),
    ScenarioSpec(
        scenario_id="T20",
        title="主题外新事实与多主题事实",
        contracts=("D11",),
        responsible_tickets=("03", "11", "15"),
        dev_turns=("我每周三晚上去实验室，那晚别安排学习。", "我在收藏校徽设计。"),
        expected_groups=(
            FactGroup("周三晚在实验室", ("周三", "实验室")),
            FactGroup("收藏校徽设计", ("校徽",)),
        ),
        follow_up_question="帮我安排一下周三晚上的时间。",
        adoption_must_include=("周三", "实验室"),
        driver="t20",
    ),
)

SCENARIO_SPECS_BY_ID: dict[str, ScenarioSpec] = {spec.scenario_id: spec for spec in SCENARIO_SPECS}

#: 画像对照实验任务（无/正确/错误/修正后画像；同一任务、独立账户）。
PAIRED_TASKS: tuple[PairedTaskSpec, ...] = (
    PairedTaskSpec(
        task_id="paired-time-budget",
        title="时间约束画像对学习计划回答的影响",
        correct_seed="我在学线性代数，每晚只能抽出半小时。",
        wrong_seed="我在学线性代数，每天晚上有三个小时可以学。",
        question="帮我安排一下今晚的线性代数学习计划。",
        correct_answer_keywords=("半小时", "半个小时", "30 分钟", "30分钟", "30 分"),
        wrong_answer_keywords=("三小时", "三个小时", "3 小时", "3小时", "180 分钟", "180分钟"),
        correction_text="你记错了，我每晚只有半小时学线性代数，不是三小时。",
        recurrence_question="今晚的线性代数学习安排需要调整吗？",
        recurrence_must_include=("半小时", "半个小时", "30 分钟", "30分钟", "30 分"),
        recurrence_must_exclude=("三小时", "三个小时", "3 小时", "3小时", "180 分钟"),
    ),
    PairedTaskSpec(
        task_id="paired-grade",
        title="年级画像对简历教育背景回答的影响",
        correct_seed="我是大三学生。",
        wrong_seed="我是大四学生。",
        question="帮我写一句包含我当前年级的教育背景介绍。",
        correct_answer_keywords=("大三",),
        wrong_answer_keywords=("大四",),
        correction_text="你记错了，我不是大四，是大三。",
        recurrence_question="我现在的年级是？",
        recurrence_must_include=("大三",),
        recurrence_must_exclude=("大四",),
    ),
)

#: 硬边界场景（隔离、删除、敏感无授权、使用控制）；判定见 metrics。
BOUNDARY_ISOLATION_FACT = "我叫林界，是一名地质学研究生。"
BOUNDARY_DELETION_FACT = "我每周去三次健身房。"
BOUNDARY_SENSITIVE_FACT = "我有抑郁症，最近状态不好。"

FEEDBACK_ONE_OFF_TEXT = "今天赶时间，简短一点。"
FEEDBACK_LONG_TERM_TEXT = "以后都先给结论。"
FEEDBACK_EXPRESSION_TEXT = "身份没错，但解释太难了。"


def scenario_payload() -> list[dict[str, object]]:
    """规范化场景载荷（语料摘要与证据文件共用同一序列化）。"""

    return [
        {
            "scenario_id": spec.scenario_id,
            "title": spec.title,
            "contracts": list(spec.contracts),
            "responsible_tickets": list(spec.responsible_tickets),
            "dev_turns": list(spec.dev_turns),
            "expected_groups": [
                {"label": group.label, "keywords": list(group.keywords)}
                for group in spec.expected_groups
            ],
            "forbidden_item_keywords": list(spec.forbidden_item_keywords),
            "follow_up_question": spec.follow_up_question,
            "adoption_must_include": list(spec.adoption_must_include),
            "adoption_must_exclude": list(spec.adoption_must_exclude),
            "driver": spec.driver,
            "extra_samples": [
                {
                    "sample_id": sample.sample_id,
                    "kind": sample.kind,
                    "turns": list(sample.turns),
                    "expected_groups": [
                        {"label": group.label, "keywords": list(group.keywords)}
                        for group in sample.expected_groups
                    ],
                    "forbidden_item_keywords": list(sample.forbidden_item_keywords),
                    "negation_keywords": list(sample.negation_keywords),
                }
                for sample in spec.extra_samples
            ],
        }
        for spec in SCENARIO_SPECS
    ]


__all__ = [
    "SCENARIO_SPECS",
    "SCENARIO_SPECS_BY_ID",
    "PAIRED_TASKS",
    "BOUNDARY_ISOLATION_FACT",
    "BOUNDARY_DELETION_FACT",
    "BOUNDARY_SENSITIVE_FACT",
    "FEEDBACK_ONE_OFF_TEXT",
    "FEEDBACK_LONG_TERM_TEXT",
    "FEEDBACK_EXPRESSION_TEXT",
    "scenario_payload",
]
