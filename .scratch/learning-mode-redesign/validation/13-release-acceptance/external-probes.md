# 工单 42 外部能力探针报告

- 生成时间：2026-10-14T11:14:01.357706+00:00
- 生效模型：qwen3.7-plus-2026-05-26（factory）
- 说明：真实模型能力与外部服务可得性分开报告；本报告只含外部探针。

| 门 | 状态 | 实测层 | 宣称层 | 降级合同 | 结论 |
| --- | --- | --- | --- | --- | --- |
| arxiv.full_text | passed | full | degraded | 有 | 技术实测可下载并提取正文（前 3 页 9584 字符）；产品未接线全文读取，界面保持摘要层。 |
| amap.campus_routes | passed | full | partial | 有 | 校内起终点检索成功；三种方式 3/3 返回路线，3 条含路径点。 |
| web_search.tavily | passed | full | full | 有 | 公网搜索返回 5 条结果。 |
| tieba.replies | failed | unavailable | partial | 有 | 帖子读取受限或不可用（status=access_restricted，error=tieba_read_access_restricted）。 |
| jobs.public_detail | inconclusive | unavailable | partial | 有 | 未能通过公网搜索定位到公开岗位详情页。 |
| video.intro | passed | full | partial | 有 | 公开元数据核对通过：【高数入门】068 多元函数的极限连续性（含简介） |
| github.files | failed | unavailable | partial | 有 | 仓库元数据 失败，README 失败，许可 失败。 |
| model.configured_capabilities | passed | full | full | 无 | 生效模型 qwen3.7-plus-2026-05-26（factory）四项能力与有界上下文样本实测通过。 |

## 逐门降级说明

- **arxiv.full_text**：产品未宣称全文深读；接线扩展需另行审查。
- **amap.campus_routes**：某方式无路线时按方式如实失败，不拿其他耗时或猜测地图替代。
- **web_search.tavily**：搜索不可用时相关模块如实标注缺口，不编造来源。
- **tieba.replies**：回复读不到时只交付帖链与标题，不总结未读回复、不称普遍共识。
- **jobs.public_detail**：只交付未核实链接，不推断岗位要求。
- **video.intro**：只交付核对过的元数据，不宣称看过视频内容或字幕。
- **github.files**：README 只当项目自述；未读实现不作架构或可运行断言。
- **model.configured_capabilities**：未实测通过的能力不得激活或宣称为可用。

## 未验证门（缺凭据/不可达）

- jobs.public_detail
