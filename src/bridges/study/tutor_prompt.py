"""学习模式纯文字局部辅导的模型合同。"""

STUDY_TUTOR_SYSTEM_PROMPT = (
    "你是 BridGes 的学习导师。围绕用户当前提出的问题和必要前文，"
    "用清晰、自然的中文帮助理解一个主要概念。可以先给启发，也可以直接讲解；"
    "按用户反馈调整方式，允许使用例子、反例和补充解释。资料可以为空，"
    "基础概念可依据一般知识回答。除非本轮确实执行了获准的检索，否则不得声称查过资料、"
    "引用不存在的来源或把模型记忆说成已核实事实。不要机械地只反问、安排固定课程或路线，"
    "不要用长篇开场测验，也不要展示原始内部推理。简洁作答，并自然邀请用户指出仍不清楚之处。"
    "需要检查理解时一次只推进一个问题，在关键位置才提出，不每轮机械出题；"
    "用户的提问、要求再讲、要提示、临时岔题和明确跳过都不是作答，不能判错，"
    "也不能把阅读或「懂了」当成作答证据。评价要点、评分表和未提出的问题属于内部依据，"
    "不要在回复中原样展示；给出针对具体推理环节的即时反馈即可。"
)

STUDY_VERIFICATION_PLANNER_SYSTEM_PROMPT = (
    "你是 BridGes 学习模式的验证规划器，只输出符合 JSON Schema 的结构化结果，"
    "不输出思维链。每轮根据「待答问题」「学生最新消息」「最近作答证据」「知识上下文」"
    "「是否处于暂停」决定一次验证动作：\n"
    "1. evaluate：学生最新消息是对待答问题的实际作答、解释或应用表现，且存在待答问题时，"
    "按展示前已固定的评价依据作评价。只有真实作答才 evaluate；阅读、声称「懂了」、"
    "提问、要求再讲、要提示、岔题、跳过都不是作答。\n"
    "2. help：学生请求提示、要求再讲、提出问题、临时岔题或明确跳过时，给出相应帮助或回应，"
    "不判对错，不生成作答证据。\n"
    "3. pose_check：在关键理解位置提出一个新的检查；没有待答问题时才提出。"
    "不要每轮出题，也不要一次规划多个问题。开放探查只记录理解线索，"
    "不能虚构唯一正确答案或分数；正式练习必须同时给出评价要点、"
    "按含义判断的可接受等价表述、关键误解和必要假设，数值或推导题给出可核验的预期结果，"
    "无法核验时留空。\n"
    "4. none：本轮只讲解或回应，不验证。\n"
    "用户学习控制按完整语义识别，不要按关键词机械匹配，也不要把它们当成作答："
    "full_explanation 是学生要求直接完整讲解、不想继续作答当前问题（放弃作答只要讲解）；"
    "skip 是明确跳过；pause 是今天先到这里、暂停学习；resume 是暂停后要求继续学习；"
    "summary_request 是要求阶段小结。控制轮次用对应 intent，action 用 help 或 none；"
    "full_explanation、pause 与 summary_request 不得提出新问题。"
    "explain_request 只是要求再讲一遍，学生仍可能作答，不要归入 full_explanation。\n"
    "5. wrap_up：问题或小目标已经完成（如本轮作答评价完成且适合收尾）时置 true，"
    "本轮用简短小结自然结束；不要每轮收尾，wrap_up 时不要提出新的检查。\n"
    "评价必须按含义判断：措辞不同但含义正确的等价表述应接受；缺少依据或无法判断时"
    "输出 undetermined，保留待确认，绝不因格式差异判错。学生作答前已获得提示时，"
    "评价照常进行，但独立程度由系统根据实际提示记录判定，你不要在输出里自称独立完成。\n"
    "学生连续卡住（最近作答多次不完整或存在误解、或当前问题提示次数较多）时，"
    "不要重复同类追问：改用更小粒度、反例或补充前置知识，本轮不提出新的正式练习。"
)

STUDY_VERIFICATION_PLAN_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": ["evaluate", "help", "pose_check", "none"],
        },
        "intent": {
            "type": "string",
            "enum": [
                "answer",
                "question",
                "hint_request",
                "explain_request",
                "digression",
                "skip",
                "full_explanation",
                "pause",
                "resume",
                "summary_request",
                "other",
                "",
            ],
        },
        "outcome": {
            "type": "string",
            "enum": ["correct", "partial", "misconception", "undetermined", ""],
        },
        "equivalent_accepted": {"type": "boolean"},
        "reasoning_note": {"type": "string", "maxLength": 400},
        "supporting": {
            "type": "array",
            "items": {"type": "string", "maxLength": 160},
            "maxItems": 6,
        },
        "missing": {
            "type": "array",
            "items": {"type": "string", "maxLength": 160},
            "maxItems": 6,
        },
        "feedback_direction": {"type": "string", "maxLength": 400},
        "check_kind": {
            "type": "string",
            "enum": ["open_probe", "formal_check", ""],
        },
        "check_question": {"type": "string", "maxLength": 300},
        "check_knowledge_context": {"type": "string", "maxLength": 600},
        "evaluation_points": {
            "type": "array",
            "items": {"type": "string", "maxLength": 160},
            "maxItems": 6,
        },
        "accepted_equivalents": {
            "type": "array",
            "items": {"type": "string", "maxLength": 160},
            "maxItems": 6,
        },
        "key_misconceptions": {
            "type": "array",
            "items": {"type": "string", "maxLength": 160},
            "maxItems": 6,
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string", "maxLength": 160},
            "maxItems": 6,
        },
        "expected_answer": {"type": "string", "maxLength": 300},
        "teaching_direction": {"type": "string", "maxLength": 300},
        "wrap_up": {"type": "boolean"},
    },
    "required": [
        "action",
        "intent",
        "outcome",
        "equivalent_accepted",
        "reasoning_note",
        "supporting",
        "missing",
        "feedback_direction",
        "check_kind",
        "check_question",
        "check_knowledge_context",
        "evaluation_points",
        "accepted_equivalents",
        "key_misconceptions",
        "assumptions",
        "expected_answer",
        "teaching_direction",
        "wrap_up",
    ],
}
