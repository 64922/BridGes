/** 把领域等待代码转成用户可执行的中文说明，不把内部标识直接显示。 */
const WAIT_REASON_LABELS: Record<string, string> = {
  tieba_authentication: "等待你完成贴吧登录或安全验证",
  tieba_clarification: "等待你补充贴吧检索所需的信息",
  paper_clarification: "等待你补充论文检索信息",
  resources_clarification: "等待你补充学习资料需求",
  github_clarification: "等待你补充项目需求",
  commute_clarification: "等待你补充通勤起终点",
  career_clarification: "等待你补充求职目标",
};

export function waitReasonLabel(value: string): string {
  return WAIT_REASON_LABELS[value] ?? `等待你补充：${value}`;
}
