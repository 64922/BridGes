"""Agent 方向的准备步骤：岗位原文提供依据，实施路线明确标为推断。"""

from bridges.career_plan.contracts import CareerAdviceItem, CareerRequestAnalysis, JobSample
from bridges.career_plan.lexicon import family_for


def agent_preparation(
    analysis: CareerRequestAnalysis, samples: list[JobSample]
) -> list[CareerAdviceItem]:
    if not samples or not any(
        family is not None and family.key == "agent"
        for family in (family_for(term) for term in analysis.job_terms)
    ):
        return []
    basis = [
        f"{sample.title}：{line}（{sample.url}）"
        for sample in samples[:2]
        for line in sample.requirements
        if any(term in line.lower() for term in ("agent", "智能体", "工具调用", "rag"))
    ][:3]
    if not basis:
        return []
    return [
        CareerAdviceItem(
            kind="project",
            title="先完成一个能运行、能评测的 Agent 项目",
            detail=(
                "准备路线（推断）：①先复现岗位要求中的一项模型或智能体任务，"
                "确认输入、输出与运行方式；②做成可演示的小项目，逐项实现岗位原文"
                "提到的能力，不需要一次做全；③保留成功与失败案例，记录评价方法、"
                "结果与改进过程，整理运行说明和演示作为投递材料。"
                "目前未确认你的编程与项目基础，这是一条可调整的练习路线，"
                "不表示你缺少这些能力，也不承诺完成时间或求职结果。"
            ),
            basis=basis,
            inference=True,
        )
    ]
