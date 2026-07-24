# 科学证据、RAG 与领域包可信体系研究

核验日期：2026-07-24

## 1. 结论

本项目不应把“接入向量数据库、检索若干片段、让模型附上引用”视为低幻觉方案。成熟的可信科学系统应采用以下内核：

> **版本化来源快照提供可核验材料，claim—evidence—wording 图谱约束每个科学判断，领域包按问题类型解释证据强度，确定性质量门决定回答能否发布。**

核心决策如下：

1. **RAG 是候选证据发现机制，不是事实验证机制。** 检索相关性只能回答“这段材料是否可能相关”，不能回答“它是否支持当前 claim”。
2. **引用生成晚于 claim 验证。** 系统先将回答拆成可核验 claim，再把每个 claim 绑定到精确版本和定位信息，最后由程序渲染引用；禁止模型凭记忆补 DOI、作者、页码或来源编号。
3. **不建立跨学科单一证据金字塔。** 公共层保存多维证据画像，领域包再按问题类型将其映射为结论确信度和允许措辞。
4. **元数据聚合器不是正文证据。** Crossref、DataCite、OpenAlex 等首先用于标识解析、版本关系、状态发现和引用网络；只有能定位到原文、数据、正式标准或经过领域规则认可的内容，才可成为 substantive claim 的证据。
5. **撤稿、勘误、版本更新是检索前门，而不是回答后的提示。** 状态未知、版本过时、仅有摘要或元数据时必须显式降级；关键状态检查失败时闭锁高置信输出。
6. **外部文档永远是不可信数据。** 文档、网页、OCR、工具返回值中的命令不得改变系统指令、权限或工具调用；发现完整性、注入或租户隔离异常时进入隔离区。
7. **LlamaIndex 只作为可替换的摄入与检索实现候选。** 项目自己的证据协议、租户边界、状态机、领域包和审计记录必须位于框架之上，不能依赖框架私有对象成为长期领域模型。

这套体系直接服务比赛最重要的科学事实准确性、结果校验和流程可复现性，同时遵守已确认的“本地优先混合架构”“可审计工作流”“领域包”和“用户账户”边界。

## 2. 一手来源核验与能力边界

### 2.1 LlamaIndex：可组合流水线可采用，事实裁决不可外包

LlamaIndex 官方文档和源码确认了以下可迁移能力：

- 摄入流程可把 `Document` 经可组合 `Transformations` 转成 `Node`，进行分块、元数据提取和嵌入；节点与变换组合可以缓存，挂接 docstore 后可基于 `doc_id`、`ref_doc_id` 和文档哈希做重复检测与更新处理。[官方摄入文档](https://docs.llamaindex.ai/en/v0.10.17/module_guides/loading/ingestion_pipeline/root.html)
- `Document` 会被拆成带父文档关系的 Node；框架支持句子、Token、HTML、JSON 等分块器。节点可保存来源、前后、父子等关系，层次解析器源码会显式建立 `PARENT`/`CHILD` 关系。[官方加载文档](https://docs.llamaindex.ai/en/v0.10.19/understanding/loading/loading.html)、[层次节点解析器源码](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/relational/hierarchical.py)
- Node 元数据可分别排除在 embedding 输入或 LLM 输入之外，说明“用于检索的字段”和“可暴露给生成模型的字段”可以分开控制。[官方 Schema API](https://docs.llamaindex.ai/en/v0.10.23/api_reference/schema/)
- 官方示例包含 BM25 与向量检索融合、`QueryFusionRetriever` 和重排；Node Postprocessor 可用交叉编码器或 LLM 对候选节点重排。[融合检索示例](https://docs.llamaindex.ai/en/v0.10.19/examples/low_level/fusion_retriever.html)、[Node Postprocessor 文档](https://docs.llamaindex.ai/en/v0.10.20.post1/module_guides/querying/node_postprocessors/node_postprocessors.html)
- `CitationQueryEngine` 能在检索结果之上再切出更细的 citation node，并通过 `citation_chunk_size`、`citation_chunk_overlap` 控制引用粒度。[官方 API 文档](https://docs.llamaindex.ai/en/v0.10.17/api_reference/query/query_engines/citation_query_engine.html)、[当前源码](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/query_engine/citation_query_engine.py)

采用边界：

- 采用其“摄入 → Node → 检索 → 后处理 → 合成”的接口思想，并在技术选型阶段评估是否直接使用对应库。
- 不直接把 Node 当作本项目 `Evidence`，也不把检索分数当证据强度。
- `CitationQueryEngine` 的职责仅相当于“引用候选与展现辅助”。它的提示词要求基于来源并标编号，但源码并未证明每个生成 claim 都与引用形成逻辑支持关系，因此不能替代 claim 级验证。
- LlamaIndex 官方安全策略明确说明：输入验证、认证授权、限流、SSRF、路径安全和提示注入都由宿主应用负责；这进一步说明本项目必须在框架外建立安全边界。[LlamaIndex Security Policy](https://github.com/run-llama/llama_index/security)
- 实施时必须锁定实际使用版本并做适配测试；本研究不把上述历史版本文档中的类名和默认参数硬编码为产品契约。

### 2.2 W3C PROV：用于出处链，不用于替代真实性判断

W3C PROV-O 用 `Entity`、`Activity`、`Agent` 表达产物、过程和责任主体，并定义 `used`、`wasGeneratedBy`、`wasDerivedFrom`、`wasRevisionOf`、`wasQuotedFrom`、`hadPrimarySource`、`wasInvalidatedBy` 等关系，适合表示“某段文字由哪个版本材料经何种解析、抽取和模型运行产生”。[W3C PROV-O Recommendation](https://www.w3.org/TR/prov-o/)

项目采用 PROV 的语义骨架，但不要求业务数据库内部完全 RDF 化：

- Source、Document、Chunk、Claim、Wording 等是 `Entity`；
- 摄入、解析、OCR、检索、验证、改写和人工裁决是 `Activity`；
- 发布者、上传用户、连接器、Qwen 运行实例、领域包和人工审核者是 `Agent`；
- 每次回答保存一个可导出的 provenance bundle。

W3C 同时明确提醒：provenance 记录本身不保证权威或正确，来源信任需要单独判断；因此“有完整出处链”与“内容为真”是两项不同质量门。[W3C PROV-AQ](https://www.w3.org/TR/prov-aq/)

### 2.3 科学元数据与内容接口

| 接口 | 可用于本项目的能力 | 必须保留的限制 |
| --- | --- | --- |
| Crossref REST API | DOI 元数据、作品类型、作者/机构、基金、许可、参考文献、关系、更新信息；可按更新/索引日期增量同步 | 数据主要由成员和可信来源提交，字段并非每条都完整；引用计数只覆盖其可匹配网络，不能作为可靠性分数 |
| Crossmark / Crossref updates | correction、retraction、withdrawal 等会影响解释或署名的状态更新；API 可发现 `has-update`、`is-update`、`update-type` 等关系 | 依赖发布者/来源提交；“没有 update 字段”不等于“已证明未撤稿” |
| DataCite REST API / Schema | DOI 元数据、`metadataVersion`、`schemaVersion`、状态；`relatedIdentifier` 可表达版本、引用、补充、取代和预印本—正式版关系 | 公共 API 只返回 Findable DOI；作品转为 Registered 后可能从公共结果消失，不能把“查不到”直接解释为不存在或撤稿 |
| OpenAlex Works API | 跨学科作品发现、DOI/其他标识映射、位置、被引计数、参考作品、被引网络、`is_retracted` 候选信号 | 聚合与匹配数据适合发现和网络扩展，不是发布者状态的最终权威；布尔撤稿信号必须回查 Crossref、PubMed、出版社或仓储来源 |
| arXiv API | Atom 元数据、arXiv ID、标题、摘要、作者、类别、DOI/期刊引用、`published` 与 `updated`；支持按具体版本标识定位 | arXiv 是预印本仓储；`published` 是首版提交时间，`updated` 是所取版本时间，不能等同同行评审或正式出版状态 |
| PubMed / NCBI E-utilities | 检索和取得 PubMed 记录；MEDLINE/PubMed XML 提供 PMID 版本、修订日期、PublicationType 与 CommentsCorrections | 主要覆盖生物医学；摘要不等同全文；状态关系仍须读取并处理，不能只检索标题摘要 |
| Europe PMC REST API | 生命科学文献元数据、OA 子集全文 XML、参考文献、被引网络、数据库链接和 status-update search | 全文只对允许的开放子集提供；版权和许可必须在摄入前检查；聚合引用网络不保证完整 |

对应一手依据：

- Crossref REST API 公开其成员和可信来源提交的书目、许可、资助、post-publication update、ORCID/ROR 与摘要等元数据；其过滤器包含 `has-update`、`is-update`、`update-type`、`has-references` 和关系过滤。[REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)、[REST API filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/)
- Crossmark 用于展示当前状态、correction、retraction 和其他重要更新；Crossref 的注册说明指出重要更新通常是独立 DOI，并与被更新作品建立关系。[Crossmark](https://www.crossref.org/documentation/crossmark/)、[Crossmark registering updates](https://support.crossref.org/hc/en-us/articles/115000501246-Crossmark-registering-updates)
- Crossref 说明公开 API 的被引数来自成员提交并成功匹配的引用，覆盖范围不同会导致计数差异；所以引用数只作为发现和排序的弱特征。[Cited-by](https://www.crossref.org/documentation/cited-by/)
- DataCite REST API 可检索完整 DOI 元数据并返回 `metadataVersion`、`schemaVersion`、`state` 等附加字段；DataCite 的版本关系使用 `IsNewVersionOf`、`IsPreviousVersionOf`、`IsVersionOf`、`HasVersion`，修正可使用 `IsObsoletedBy`/`Obsoletes`。[REST API](https://support.datacite.org/docs/api)、[Retrieving a single DOI](https://support.datacite.org/docs/api-get-doi)、[版本与修正关系](https://support.datacite.org/docs/connecting-versions-with-related-identifiers)
- DataCite 公共 API 只返回 Findable 状态；其官方说明指出作品撤下或撤回可表现为 Findable 转 Registered，需要认证接口或持续状态历史才能识别。[DOI States](https://support.datacite.org/docs/doi-states)、[检测 removed records/retractions](https://support.datacite.org/docs/how-do-i-detect-removed-records-or-retractions-with-the-rest-api)
- OpenAlex Works API 的作品记录和列表字段包括 DOI、作品类型、出版时间、位置、`cited_by_count`、`referenced_works` 和 `is_retracted`，适合做跨源候选发现与引用图扩展。[Works API](https://developers.openalex.org/api-reference/works)、[List works](https://developers.openalex.org/api-reference/works/list-works)
- arXiv API 返回 Atom；条目 `published` 表示第一版提交日期，`updated` 表示当前取回版本日期。其版本帮助页和撤回帮助页表明版本与撤回状态应作为来源生命周期处理。[API User's Manual](https://info.arxiv.org/help/api/user-manual.html)、[Version Availability](https://info.arxiv.org/help/versions.html)、[Withdraw / Retract](https://info.arxiv.org/help/withdraw.html)
- NCBI E-utilities 提供 Entrez 数据库程序化访问；PubMed XML 的 `CommentsCorrections` 关系包括 Erratum、ExpressionOfConcern、Retraction、Update 等正反向类型。[NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25499/)、[PubMed CommentsCorrections DTD](https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/250101/el-CommentsCorrections.html)、[NLM errata/retractions policy](https://www.nlm.nih.gov/bsd/policy/errata.html)
- Europe PMC REST API 提供 metadata、OA 全文 XML、references、citations、数据库链接与 status-update search，并明确只有开放子集可程序化获得全文。[RESTful Web Service](https://europepmc.org/RestfulWebService)、[Developer resources](https://europepmc.org/developers)

所有这些接口都应通过带版本的 Source Adapter 统一接入。任何 API 字段缺失都应解释为 `unknown`，而不是 `false`。

### 2.4 证据分级：采用多维画像，不采用全学科统一等级

不存在能够直接适用于数学证明、物理实验、化学测量、生命科学干预研究、天文观测、计算机基准和软件文档的单一权威证据金字塔。

可迁移的权威方法是“按问题类型选择评价规则”：

- Oxford CEBM 明确说明其层级是“可能最佳证据”的检索捷径，必须结合判断；其等级表本身也按治疗、诊断、预后等问题类型变化。[CEBM introductory document](https://www.cebm.ox.ac.uk/resources/levels-of-evidence/levels-of-evidence-introductory-document)、[2011 Levels of Evidence](https://www.cebm.ox.ac.uk/files/levels-of-evidence/cebm-levels-of-evidence-2-1.pdf/view)
- GRADE 针对“某结果的一组证据”考察偏倚风险、不一致性、间接性、不精确性和发表偏倚，并要求记录降级/升级理由；它适合由生物医学领域包映射，而不应直接套到所有学科单篇材料。[Cochrane Handbook Chapter 14](https://training.cochrane.org/handbook/current/chapter-14)
- Cochrane 将风险偏倚和随机误差区分，且强调风险偏倚应针对具体结果评价；这支持本项目把 evidence assessment 绑定到 claim/outcome，而不是给整篇文献打一个永久总分。[Cochrane Handbook Chapter 7](https://training.cochrane.org/handbook/current/chapter-07)

因此公共层只保存多维证据画像；领域包负责回答：

1. 当前问题属于定义、机制、测量、干预、诊断、预后、因果、相关、证明、算法性能、工程规范还是其他类型？
2. 哪些研究设计和来源对该问题有效？
3. 哪些维度会降低确信度？
4. 允许使用多强的措辞？

这是一项架构结论，不主张 GRADE、CEBM 或任何单一方法具有跨全部科学领域的普适权威。

### 2.5 RAG 文档注入与不可信内容

OWASP 将间接 prompt injection 定义为外部网页、文件等内容改变模型行为；并明确指出 RAG 和微调不能彻底消除 prompt injection。[OWASP LLM01:2025](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)

OWASP RAG Security 指南进一步覆盖文档投毒、嵌入与索引完整性、来源追踪、租户命名空间和 fail-closed；其建议包括摄入时记录哈希与上传来源、检索前做租户过滤、来源归因携带文档哈希、索引写入仅允许受控摄入管线，并在访问控制或归因失败时闭锁。[OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)

本项目据此设定不可逾越的控制面/数据面边界：

- 系统提示、工作流、权限、工具 Schema 和领域包是控制面；
- 用户问题、上传材料、网页、OCR、ASR、检索片段和工具返回值一律是数据面；
- 数据面中的“忽略规则”“调用工具”“导出数据”等文字只能作为被分析内容，不能成为可执行指令；
- 读不可信内容的模型实例无写权限和高风险工具权限；
- 任何模型提出的工具调用都必须由独立策略层重新校验用户意图、租户、参数、目标和权限；
- 正则或分类器只用于发现风险，不能被宣称为完全防御；最终安全依赖最小权限、隔离和失败闭锁。

## 3. 核心数据模型

### 3.1 总体关系

```text
Source
  └─ Document（一个不可变版本快照）
       └─ Chunk（可精确定位的结构片段）
            └─ Evidence（Chunk/数据/标准对 Claim 的关系）
Claim ── Evidence ── Document/Chunk
  └─ Wording（在特定证据强度与受众下的表达）
       └─ Citation（Wording 中对 Evidence 的呈现）
Conflict（若干 Claim/Evidence 的显式冲突集合）
```

`Source → Document → Chunk` 是出处链，`Claim ↔ Evidence` 是科学支持链，`Claim → Wording → Citation` 是发布链。三条链必须同时存在，不能用一条代替另一条。

### 3.2 实体定义

#### Source

表示来源主体或来源入口，不等于某一版内容。

必备字段：

- `source_id`
- `source_kind`：publisher、repository、standards_body、government、database、user_upload、web 等
- `canonical_identity`：DOI agency、ROR、ISSN、域名或本地来源标识
- `publisher_or_owner`
- `authority_scope`：它在哪些问题上可能具有权威
- `access_policy`、`license`
- `trust_assertions[]`：谁在何时做了何种来源判断
- `connector_id`、`connector_version`
- `tenant_scope`：public_catalog、tenant_private、explicit_shared

来源权威性不能只由域名或期刊名自动判定；它只是 evidence assessment 的一个输入。

#### Document

表示可冻结、可哈希、可复现的具体内容版本。

必备字段：

- `document_id`、`source_id`
- `canonical_identifiers[]`：DOI、PMID、PMCID、arXiv ID、DataCite DOI 等
- `work_id` 与 `version_id` 分离
- `version_label`、`version_date`、`retrieved_at`
- `publication_stage`：preprint、accepted_manuscript、version_of_record、standard、dataset、documentation 等
- `lifecycle_status`：active、corrected、expression_of_concern、retracted、withdrawn、superseded、unknown
- `status_evidence[]` 与 `status_checked_at`
- `content_hash`、`metadata_hash`
- `media_type`、`language`
- `rights_snapshot`
- `raw_blob_ref`：本地加密对象地址，不把原文塞入普通日志
- `parser_id/version`、`ocr_asr_id/version`
- `provenance_bundle_id`

同一 work 的新版本生成新 Document，旧版不覆盖；通过 `revision_of`、`supersedes`、`corrects`、`retracts` 等边连接。

#### Chunk

表示可被检索并能回到原文的最小结构单元。

必备字段：

- `chunk_id`、`document_id`
- `parent_chunk_id`、`previous_chunk_id`、`next_chunk_id`
- `structure_path`：章/节/小节/段/表/图/公式/代码块
- `page`, `section`, `paragraph`, `figure`, `table`
- `start_offset`, `end_offset`
- `text_hash`
- `content`
- `embedding_ref`
- `lexical_index_ref`
- `visibility_label`、`tenant_id`、`project_id`
- `parse_confidence`、`human_corrected`
- `injection_flags[]`

分块不能丢失标题路径、图表标题、脚注、公式编号和邻接关系。

#### Claim

表示系统准备向用户表达的、可判断真伪或证据状态的最小命题。

必备字段：

- `claim_id`
- `claim_type`：definition、descriptive、quantitative、comparative、causal、mechanistic、predictive、normative、proof_step 等
- `canonical_subject`、`predicate`、`object_or_value`
- `qualifiers`：群体、条件、时空、剂量、单位、比较对象
- `polarity`
- `scope`
- `risk_tier`
- `importance`：key、supporting、illustrative
- `extraction_method` 与 `review_status`

“地球变暖”“过去 50 年全球平均地表温度上升 X”“主要由某因素导致”必须是不同 claim。

#### Evidence

Evidence 是“某个来源片段对某个 claim 的关系”，不是文献本身。

必备字段：

- `evidence_id`
- `claim_id`
- `document_id`、`chunk_ids[]`
- `relation`：supports、refutes、limits、contextualizes、does_not_address
- `quoted_span_or_data_ref`
- `evidence_role`：primary_result、synthesis、guideline、standard、dataset、documentation、expert_statement 等
- `question_type`
- `method_design`
- `source_proximity`：full_text、abstract_only、metadata_only、secondary_quote
- `risk_of_bias`
- `directness`
- `consistency`
- `precision`
- `independence`
- `recency_and_version`
- `lifecycle_status`
- `domain_assessment`
- `assessment_reason`
- `assessor`：rule、Qwen run、human
- `valid_from`、`invalidated_at`

禁止只存一个不可解释的 `confidence=0.87`。

#### Wording

表示 claim 在给定证据边界、受众和体裁下的一次表达。

必备字段：

- `wording_id`、`claim_id`
- `text`
- `allowed_strength`
- `actual_strength`
- `uncertainty_disclosure`
- `audience_level`、`genre`
- `profile_slice_ids[]`
- `locked_facts_hash`
- `generated_by_run`
- `validation_status`

画像只能改变解释层级、例子、次序、术语密度和语气，不能提高 `allowed_strength`。

#### Citation

Citation 是 Wording 到 Evidence 的显示绑定。

必备字段：

- `citation_id`、`wording_id`、`evidence_id`
- `locator`：页、节、段、表、图、公式、字符偏移
- `identifier_snapshot`
- `title/author/year/version_snapshot`
- `canonical_url`
- `accessed_at`
- `render_style`
- `verification_status`

引用必须由已绑定 Evidence 的结构化字段渲染，不能让 Qwen自由生成书目信息。

#### Conflict

表示无法通过范围、版本或定义差异自动消解的证据冲突。

必备字段：

- `conflict_id`
- `claim_ids[]`、`evidence_ids[]`
- `conflict_type`：true_disagreement、scope_mismatch、definition_mismatch、version_superseded、methodological、unknown
- `materiality`
- `resolution_status`
- `resolution_reason`
- `resolved_by`
- `user_visible_summary`

冲突不是异常日志，而是一等科学产物。

## 4. 端到端可信科学流水线

### 阶段 0：任务定界

1. 验证登录会话、`tenant_id`、`user_id`、项目权限。
2. 将问题分类为学习解释、资料综述、事实核查、创作、计算、证明、风险领域等。
3. 识别关键 claim 风险、时间敏感性和需要的领域包。
4. 生成 `EvidencePlan`：问题类型、所需证据形态、时间范围、来源范围、最低发布门槛。

### 阶段 1：来源发现与标识解析

1. 优先搜索用户材料和项目内已授权语料。
2. 使用领域包的 source registry 调用 Crossref、DataCite、OpenAlex、arXiv、PubMed/Europe PMC 等 adapter。
3. 规范化 DOI、PMID、PMCID、arXiv ID，合并“同一 work 的不同入口”，但保留不同版本。
4. 记录每个候选从何查询、查询参数、返回时间、原始响应哈希和 adapter 版本。

元数据记录在此阶段只是候选，不进入 substantive answer。

### 阶段 2：状态、版本、许可与完整性门

1. 解析预印本、正式版、修正版、勘误、撤稿、关注声明、取代关系。
2. 选出适合当前问题的具体版本；不能简单选择日期最新者。
3. 检查许可和访问条件，决定可保存全文、仅保存派生索引还是只能保存链接。
4. 下载或接收原件后计算内容哈希；后续解析和引用都回指该快照。
5. 连接器、MIME、扩展名、魔数、大小、压缩层级和恶意内容检查不通过则隔离。

### 阶段 3：隔离解析与结构感知分块

解析在无凭据、无网络写权限、有限资源的沙箱内执行：

1. 保留标题层级、段落、页码、公式、图表、脚注、参考文献和代码块。
2. PDF 先保留原始文本层；OCR 结果作为派生版本，低置信区域可人工纠正。
3. 采用两级或三级节点：文档摘要节点 → 章节节点 → 证据片段节点。
4. Chunk 边界优先遵循结构和语义，Token 大小只是上限；表格、公式、定义、证明步骤和图注不得被随意切断。
5. 每个 Chunk 保留父子、前后、字符偏移、页/节/图表定位和哈希。
6. 外部内容先做不可见字符、隐藏层、指令样式和异常编码检测，风险片段带 `injection_flags`，不能获得工具控制权。

### 阶段 4：索引构建

每个可检索片段至少进入三种视图：

- **词法索引**：保留术语、符号、公式名称、专有名词和精确短语；
- **向量索引**：支持语义召回；
- **关系索引**：来源—版本—章节—claim—引用—修正/撤稿等边。

索引写入只允许摄入服务；embedding 模型、维度或分块规则变化时生成新 `index_snapshot_id`，不能原地混用。

### 阶段 5：查询编译与混合检索

1. Qwen 产生结构化查询计划：核心术语、同义词、实体、时间、问题类型和子问题。
2. 程序为每个子查询并行执行词法检索、向量检索和关系扩展。
3. 在查询前强制添加租户、项目、可见性、状态、领域和时间过滤；禁止“全库召回后再过滤”。
4. 使用可记录参数的 rank fusion 合并列表，并保留每个候选的原始排名和分数。
5. 对实体、数值、公式、标准编号等精确问题，提高词法通道权重；对解释性问题提高语义通道权重。权重由领域包和评测决定，不在本研究硬编码。

### 阶段 6：重排与证据覆盖

1. 专用 reranker 对 query—chunk 相关性重排。
2. 程序执行去重、来源多样性、版本优先级和相互独立性约束。
3. 对关键 claim 保留支持、反驳和限制三类候选，不能只取最支持用户预期的片段。
4. 若只有摘要或元数据，标记 `source_proximity`，不得伪装成全文验证。

### 阶段 7：claim 规划与抽取

Qwen 在固定 Schema 下把候选回答拆为原子 claim，并显式给出群体、条件、时间、单位、因果强度和例外。程序检查：

- 数字、单位、日期、对象和比较关系是否完整；
- 一个 claim 是否混入多个可独立真假判断；
- 引用需要覆盖的是句子整体还是其中一个从句；
- 哪些句子只是解释、类比或建议，不能伪装成实证结论。

### 阶段 8：claim 级证据验证

每个关键 claim 都执行：

1. 从 Chunk 精确抽取支持或反驳 span；
2. 确认对象、条件、方向、数值、单位、时间和结论类型一致；
3. 区分“文献提到该主题”和“文献支持该命题”；
4. 检查证据来自正文、摘要、引用他人还是元数据；
5. 依据领域包评估研究设计、偏倚、直接性、精确性、版本和状态；
6. 生成带理由的 Evidence 记录。

Qwen 可做语义蕴含和方法描述抽取，但不能自行把 `unknown` 改成 `supported`；确定性字段冲突优先于模型判断。

### 阶段 9：冲突处理

系统按 claim 的主体、关系、方向、群体、条件、时间、单位和研究问题聚类：

1. 先排除定义差异、版本取代、不同群体/条件和不同结果指标造成的伪冲突；
2. 再比较方法设计、偏倚、直接性、精度、独立性与状态；
3. 不按引用数多数表决，不自动平均不兼容结果；
4. 若冲突仍实质存在，保留双方 Evidence，并输出冲突原因、当前无法裁决项和需补充的信息；
5. 高风险、重大或会改变结论的冲突进入人工裁决。

### 阶段 10：证据确信度与措辞约束

领域包将多维证据画像映射为当前 claim 的：

- `certainty`：high、moderate、low、very_low、unassessable；
- `allowed_strength`；
- 必须披露的限制；
- 可否用于教学解释、一般科普、高风险建议或正式发布。

映射以透明理由保存，不把等级隐藏成单一浮点数。

### 阶段 11：回答生成与引用渲染

1. Qwen 只接收通过验证的 Claim、允许措辞、必要限制和最小用户画像切片。
2. 生成时 Claim ID 与 Evidence ID 不可丢失。
3. 程序根据 Citation 记录渲染脚注、文中引用和来源卡。
4. 每个引用可回到具体版本、页/节/图表/段落；用户能区分全文证据、摘要依据和元数据线索。
5. 人味改写只能修改 Wording，改写前后 `locked_facts_hash` 必须一致。

### 阶段 12：发布前质量门

发布前执行：

- 关键 claim 覆盖率；
- claim—citation 支持关系；
- DOI/PMID/arXiv 等标识和 URL 格式；
- 数字、单位、日期、公式与限定词一致性；
- 撤稿/修正/版本状态是否过期；
- 租户与权限；
- 注入风险和敏感数据泄漏；
- 措辞是否超过允许强度；
- 冲突和局限是否披露；
- provenance bundle 是否完整。

未通过时按诚实降级状态机处理，不允许把未验证答案标成完成。

## 5. 多维证据画像与措辞强度

### 5.1 公共证据维度

| 维度 | 典型值 | 含义 |
| --- | --- | --- |
| 内容接近度 | full_text / abstract_only / metadata_only / secondary_quote | 系统究竟看到了多少原始内容 |
| 生命周期 | active / corrected / concern / retracted / withdrawn / superseded / unknown | 当前版本能否继续用于结论 |
| 证据角色 | primary_result / synthesis / standard / guideline / dataset / documentation / expert_statement | 它在论证中的角色 |
| 问题匹配 | direct / partially_direct / indirect / not_applicable | 是否回答同一问题 |
| 方法设计 | 领域包枚举 | 研究或证明采用什么方法 |
| 偏倚风险 | low / some_concerns / high / critical / unknown | 方法与执行的系统误差风险 |
| 精确性 | precise / limited / imprecise / unknown | 数量和不确定区间是否支持结论 |
| 一致性 | consistent / mixed / conflicting / single_source / unknown | 独立证据是否一致 |
| 独立性 | independent / partially_overlapping / duplicate / unknown | 是否为同一数据或同一综述链重复计数 |
| 版本与时效 | current / older_valid / superseded / time_sensitive_unknown | 是否适合当前问题 |
| 可复现性 | materials_available / partial / unavailable / not_applicable | 方法、数据、代码或证明能否复核 |

### 5.2 领域包映射示例

- 生物医学干预问题可以映射 GRADE/Cochrane 的风险偏倚、不一致、间接、不精确和发表偏倚。
- 诊断问题使用诊断准确性与适用人群规则，不能照搬干预等级。
- 数学 claim 以定义、假设、证明链和反例为主；“更多论文引用”不能提升证明成立性。
- 物理/化学测量需要仪器校准、误差、不确定度、实验条件和独立复现。
- 计算机算法性能需要任务定义、数据集版本、基线、公平比较、统计波动、代码/配置和数据泄漏检查。
- 标准/API 行为优先使用对应版本的正式规范和官方源码/文档；社区帖子只能作为故障线索。

### 5.3 公共措辞策略

| 状态 | 允许的中文表达 | 禁止 |
| --- | --- | --- |
| high | “多项直接且一致的证据表明……”“在所述条件下可较有把握地认为……” | “绝对证明”“永远如此” |
| moderate | “现有证据支持……，但仍受……限制”“较可能……” | 删除关键限制后作确定断言 |
| low | “有限证据提示……”“目前可能存在……关联” | 写成已确立因果 |
| very_low | “目前只有初步信号，无法确认……”“该判断仍高度不确定” | 用流畅叙事制造确定感 |
| unassessable | “现有材料不足以判断”“只能确认书目信息，不能核验该结论” | 从模型常识补答案 |
| conflicting | “现有研究结论不一致；差异可能来自……，目前不能作单一结论” | 只展示支持一方 |
| retracted_only | “曾有材料报告该结论，但相关作品已撤稿/撤回，不能作为正面依据” | 将撤稿作品作为有效支持 |

领域包可以收紧表达，不能放宽公共安全下限。

## 6. 确定性规则、Qwen 推理与人工裁决矩阵

| 环节 | 确定性程序/专用组件 | Qwen 的合法职责 | 必须人工的情形 |
| --- | --- | --- | --- |
| 登录与租户访问 | 会话、ACL、RLS、命名空间、对象归属 | 无 | 权限策略本身变更 |
| 文件接收 | 类型、大小、哈希、许可字段、沙箱、病毒/异常检查 | 提出内容风险标签 | 高风险来源是否批准进入知识库 |
| 标识解析 | DOI/PMID/arXiv 格式、API 精确匹配、版本边 | 对模糊标题/作者给候选匹配 | 候选无法唯一消歧且影响引用 |
| 生命周期 | 解析 Crossmark、DataCite、PubMed、arXiv 状态字段 | 汇总状态说明 | 来源冲突或高风险状态未知 |
| 结构解析 | 页码、偏移、标题树、表格/公式 AST、哈希 | OCR/版面语义补全候选 | 低置信 OCR 影响关键事实 |
| 分块 | 结构边界、大小上限、父子/邻接关系 | 建议语义边界和章节摘要 | 无法保持公式、表格或证明完整 |
| 查询规划 | Schema、范围、预算、过滤器 | 意图、术语、同义词、子问题 | 用户目标存在实质歧义 |
| 混合检索 | 词法/向量/关系查询、rank fusion、租户前置过滤 | 生成查询变体 | 无 |
| 重排 | 已注册的专用重排器、去重和多样性规则 | 仅在注册流程中做语义辅助 | 高风险任务候选证据过少 |
| Claim 抽取 | Schema、数字/单位/日期解析 | 原子命题与限定条件抽取 | 关键 claim 无法稳定拆分 |
| Claim 验证 | 精确 span、单位换算、数值比较、状态门 | 语义支持/反驳/限制判断并给理由 | 高风险、重大冲突、证据间接且结论敏感 |
| 证据评价 | 领域包决策表、必填维度、禁止状态 | 抽取方法描述、提出维度判断 | 领域包要求双人评审或结论将用于高影响决策 |
| 冲突 | 条件/单位/版本归一、冲突聚类 | 解释可能的范围或方法差异 | 实质冲突无法消解 |
| 措辞 | allowed-strength 规则、事实锁和词项黑白名单 | 受众适配、解释、自然中文 | 高风险公开发布或争议性结论 |
| 引用 | Evidence ID 到书目信息/locator 的程序渲染 | 不生成书目；只决定引用放置候选 | 定位或版本无法确认 |
| 发布门 | 覆盖率、状态、权限、Schema、哈希、阈值 | 提供结构化审查报告 | override；必须记录身份、理由和范围 |

原则：

- Qwen 不决定访问控制，不创建事实来源，不伪造缺失字段，不单独解除闭锁。
- 人工 override 不能改写历史记录；它生成新的带责任人的裁决版本。
- 可计算、可解析和可枚举的规则优先程序执行；语义、意图和解释才交给 Qwen。
- 具体 Qwen 型号、embedding、reranker、OCR 组件和价格由后续技术栈任务决定，本研究只定义角色和接口。

## 7. 诚实降级状态机

### 7.1 状态

| 状态 | 含义 | 面向用户的行为 |
| --- | --- | --- |
| `VERIFIED` | 所有关键 claim 通过，状态与出处完整 | 正常回答，展示证据与限制 |
| `QUALIFIED` | 关键 claim 有支持，但确信度有限或需强限定 | 降低措辞，强制展示限制 |
| `PARTIAL` | 只有部分关键 claim 通过 | 只发布已通过部分，列出缺口 |
| `CONFLICTED` | 存在会改变答案的未决冲突 | 并列展示分歧，不输出单一确定结论 |
| `METADATA_ONLY` | 只有题录、摘要或发现线索 | 只提供资料导航，不宣称完成事实核验 |
| `STALE_OR_UPDATED` | 原使用版本已修正、取代、关注或撤回 | 暂停旧结论，重跑受影响 claim |
| `QUARANTINED` | 来源、内容、解析或索引存在完整性/注入风险 | 不进入生成上下文，提示材料待审查 |
| `BLOCKED` | 权限、状态、引用、哈希或关键服务检查失败 | 不生成科学结论，说明失败阶段和恢复方式 |

### 7.2 转移规则

```text
DISCOVERED
  ├─ 权限/完整性失败 ───────────────→ BLOCKED 或 QUARANTINED
  ├─ 只有元数据/摘要 ───────────────→ METADATA_ONLY
  └─ 取得可核验内容
       ├─ 版本失效/状态更新 ─────────→ STALE_OR_UPDATED
       └─ claim 验证
            ├─ 全部关键 claim 通过 ──→ VERIFIED
            ├─ 支持但有限制 ─────────→ QUALIFIED
            ├─ 部分通过 ─────────────→ PARTIAL
            ├─ 实质冲突 ─────────────→ CONFLICTED
            └─ 核心 claim 无证据 ────→ BLOCKED
```

状态可以在补充来源、修正 OCR、人工裁决或状态刷新后重跑，但必须生成新 run，不覆盖旧 run。

### 7.3 失败闭锁清单

以下情形不得静默回退到“仅凭模型记忆回答”：

- 租户/项目权限检查异常；
- 私人索引过滤器未生效；
- 文档哈希与摄入记录不一致；
- 关键来源处于 retracted、withdrawn 或未决 concern 且没有合格替代证据；
- 高风险问题无法完成状态刷新；
- 关键 claim 无精确 evidence span；
- 引用无法指向具体版本或 locator；
- 事实锁校验发现数字、单位、对象、方向或限定条件改变；
- 领域包缺失、签名无效或与平台版本不兼容；
- Qwen 结构化输出连续达到重试上限；
- provenance bundle 无法完整落盘；
- 注入检测、沙箱或策略服务异常。

非高风险学习问题若只缺少完整度，可进入 `PARTIAL` 或 `METADATA_ONLY`；访问控制和跨用户隔离失败永远只能 `BLOCKED`。

## 8. 领域包协议

### 8.1 Manifest

每个领域包必须带签名和语义版本，至少声明：

```yaml
id: physics.mechanics
version: 1.2.0
platform_api: ">=1.0,<2.0"
languages: [zh-CN, en]
disciplines: [physics]
question_types: [definition, measurement, mechanism, prediction]
risk_tiers: [general_education]
source_policies: [...]
source_adapters: [...]
identifier_rules: [...]
publication_stage_rules: [...]
evidence_dimensions: [...]
certainty_mappings: [...]
wording_policy: [...]
claim_schemas: [...]
unit_and_formula_rules: [...]
ontologies: [...]
tools: [...]
validators: [...]
conflict_rules: [...]
evaluation_sets: [...]
migrations: [...]
dependencies: [...]
maintainers: [...]
signature: ...
```

### 8.2 稳定接口

领域包实现以下窄接口：

- `classify_question(question_context) -> QuestionType`
- `plan_sources(question_type, risk_tier) -> SourcePolicy`
- `normalize_metadata(adapter_record) -> CanonicalMetadata`
- `resolve_version_status(records) -> LifecycleAssessment`
- `parse_domain_structure(document) -> DomainAnnotations`
- `extract_claim_schema(content) -> ClaimSchema`
- `assess_evidence(claim, evidence_set) -> EvidenceAssessment`
- `detect_conflicts(claim_set, evidence_set) -> ConflictSet`
- `constrain_wording(assessment, audience, genre) -> WordingPolicy`
- `validate_claim(claim, evidence_set) -> ValidationReport`
- `evaluate(run_artifacts, fixture_set) -> EvaluationResult`

领域包不得直接访问其他租户数据、绕过 Source Adapter、任意调用网络、修改平台 ACL 或自行发布回答。

### 8.3 版本与优先级

规则优先级：

```text
平台权限与安全不变量
  > 高风险公共策略
  > 已签名领域包
  > 项目级来源偏好
  > 用户表达偏好
```

- patch：不改变判定语义的修复；
- minor：增加兼容规则或来源；
- major：证据等级、claim schema 或措辞映射发生不兼容变化。

任何领域包升级都必须触发兼容检查和受影响评测；重大规则变化可标记旧 EvidenceAssessment 为 stale，但不能删除历史。

### 8.4 最小首批领域包

项目可以覆盖广泛学科，但平台应先定义“包类型”，再由团队并行填充：

1. 数学与形式证明；
2. 物理/化学实验与测量；
3. 生命科学一般研究；
4. 生物医学高风险教育；
5. 地球科学与气候观测；
6. 天文学观测与模型；
7. 计算机科学、算法与软件文档；
8. 跨学科通用定义、标准和数据集。

这里不是开发优先级裁剪，而是防止所有学科复用同一套错误证据等级。

## 9. 多用户隔离

### 9.1 数据边界

以下实体必须携带并强制校验 `tenant_id`、`owner_user_id`、`project_id` 或明确的 public scope：

- 私人 Source、Document、Chunk；
- 用户上传全文和 OCR/ASR 派生物；
- 私人索引、查询、EvidencePlan；
- Claim、Evidence、Wording、Citation、Conflict；
- 工作流运行、缓存、评测和导出。

公共科学元数据可以作为只读 canonical catalog 共享，但：

- 公共目录只保存公开字段和来源响应，不保存用户查询关系；
- 用户把公共作品加入项目时生成项目级引用，不把其他用户的批注、检索、claim 或使用历史带入；
- 私人正文、派生 Chunk 和 embedding 不能因内容哈希相同而跨租户复用；
- 去重服务不能向用户泄露“另一个账户已上传过此文件”；
- 协作只能通过显式 shared workspace 和最小权限 ACL。

### 9.2 检索隔离

1. 独立数据库策略或行级安全；
2. 向量库使用租户/项目命名空间和服务端 pre-filter；
3. 词法索引、关系图和缓存采用相同边界；
4. 缓存键包含 tenant、project、ACL policy version、document version、index snapshot；
5. 后台任务消息携带不可伪造的租户上下文；
6. 输出再次检查全部 Evidence 的可见性；
7. 日志不记录原始私人正文，使用不可逆标识和受控内容引用。

LlamaIndex 示例中可给 Document 写入用户 metadata 并在检索时过滤，但这只是示例模式，不足以替代数据库 RLS、命名空间和输出后二次校验。[LlamaIndex multi-tenancy pack](https://docs.llamaindex.ai/en/stable/api_reference/packs/multi_tenancy_rag/)

## 10. 可观察性与复现字段

每次摄入、检索、验证和回答至少记录：

### 运行身份

- `run_id`、`trace_id`、父子 span；
- `tenant_id`、`project_id`、匿名化 actor id；
- `workflow_id/version`；
- `domain_pack_id/version/signature`；
- 代码 commit、构建产物或容器 digest。

### 输入与数据快照

- 用户问题哈希与经授权保存的原文引用；
- EvidencePlan；
- Source Adapter 及版本、请求参数哈希、响应哈希、时间；
- Document/metadata/content hash；
- lifecycle status 与检查来源、检查时间；
- parser/OCR/ASR 版本；
- chunking 配置与 `index_snapshot_id`。

### 检索与模型

- 词法、向量、关系各候选 ID、原始排名和分数；
- 过滤原因、fusion 算法和参数；
- reranker 标识、版本、参数与最终排名；
- Qwen 能力角色、实际模型标识、区域、参数；
- prompt/template 版本、Schema 版本；
- 随机种子、temperature 等复现参数；
- token、延迟、重试、错误与降级。

### 证据与发布

- Claim 列表及版本；
- 每个 Evidence 的 span、关系、维度、评估理由与 assessor；
- Conflict 及裁决；
- allowed/actual wording strength；
- Citation locator 与验证结果；
- 每个质量门输入、阈值、结果；
- 人工 override 的身份、理由、时间和范围；
- 最终状态及未发布 claim。

复现不等于把敏感原文写入日志。运行记录保存哈希、受控引用和加密快照地址；复现实验在获得相同权限后解析快照。

## 11. 采用矩阵

### 11.1 直接采用

| 来源/方法 | 采用内容 |
| --- | --- |
| LlamaIndex | 可组合摄入、Document/Node 分层、父子/邻接关系、缓存、增量处理、词法与向量融合、重排、细粒度引用节点的接口思想 |
| W3C PROV | Entity—Activity—Agent 与 derivation/revision/quotation/primary-source/invalidation 语义 |
| Crossref/Crossmark、DataCite、PubMed | 标识解析、版本/更新/撤稿/勘误等生命周期关系 |
| OpenAlex | 跨学科候选发现、标识补全和引用网络扩展 |
| arXiv | 预印本版本和更新时间追踪 |
| Europe PMC | 生命科学 OA 全文、参考文献、被引和状态更新接口 |
| GRADE/CEBM/Cochrane | 按问题/结果评价、透明记录偏倚/间接/不一致/不精确等判断的原则 |
| OWASP | 外部内容不可信、租户预过滤、索引完整性、最小权限与 fail-closed |

### 11.2 调整采用

| 候选做法 | 项目化调整 |
| --- | --- |
| LlamaIndex Node | 映射到本项目 Document/Chunk，但 Evidence 必须另建 |
| CitationQueryEngine | 仅作为引用粒度和合成参考；前置 claim 验证、后置引用校验 |
| 元数据 API 的状态字段 | 多源交叉并保留 `unknown`；高风险回查发布者/仓储一手记录 |
| 引用计数 | 只作发现、排序和冲突探索弱特征，不进入真伪分数 |
| GRADE 等证据等级 | 放入适用的领域包；公共层保存维度，不建立全学科统一排序 |
| 模型重排/语义判断 | 置于租户过滤之后，记录版本和结果，不能解除确定性闭锁 |
| 自动元数据/claim 抽取 | 只生成候选，必须通过 Schema、规则和必要人工确认 |

### 11.3 明确舍弃

- 用 top-k 相似片段直接生成并宣称“有据可查”；
- 让模型自由编写 DOI、页码、引用编号或书目；
- 把整篇文献设置一个永久“可信度分数”；
- 用期刊名、影响因子、被引数或“同行评审”单独决定 claim 真伪；
- 将摘要、搜索摘要或聚合元数据冒充全文证据；
- 自动选择最新日期作品并覆盖旧版本；
- 自动删除冲突或只保留支持用户预期的一方；
- 以预印本状态冒充正式同行评审；
- 把检索文档中的自然语言当工具指令；
- 所有租户共用平面向量空间后做应用层过滤；
- RAG 故障后静默退回 Qwen 参数记忆；
- 领域包自行放宽平台权限、安全或证据下限。

## 12. 对后续规划任务的硬约束

### 智能体编排

- 来源发现、摄入、状态解析、检索、Claim 抽取、证据验证、冲突分析、措辞生成、引用渲染和发布门必须是可单测的独立节点。
- 智能体之间只能交换 `EvidencePlan`、`CanonicalMetadata`、`ClaimSet`、`EvidenceSet`、`ConflictSet`、`WordingPolicy` 和 `ValidationReport` 等 Schema 产物。
- 读不可信文档的智能体无高风险工具权；发布智能体看不到未经隔离的原始控制样式文本。
- 模型不能自行完成、重试无限次或修改领域包。

### 前端

证据与校验台必须能展示：

- 每个关键 claim 的 VERIFIED/QUALIFIED/PARTIAL/CONFLICTED 等状态；
- 支持、反驳、限制证据；
- 来源类型、具体版本、生命周期状态、全文/摘要/元数据层级；
- 精确页/节/图表定位和原文上下文；
- “为什么只能这样说”的措辞强度解释；
- 冲突、未决问题、人工裁决和版本变化；
- 本次回答调用了哪些用户项目材料，但不得暴露其他用户存在。

普通用户界面不显示不可解释的 0.87 可信度；专家视图可展开维度和审计记录。

### 人味表达

- 人味复查只能生成新的 Wording，不能改 Claim/Evidence；
- 数字、单位、条件、对象、结论方向、引用关系和不确定性是事实锁；
- 任何自然化改写后必须重新做 claim 对齐和措辞强度检查；
- 用户风格偏好不能把“可能相关”改成“已经证明”。

### 多模态

- 图表、图像、音频、动画和交互 HTML 中的科学陈述也要映射 Claim ID；
- OCR/ASR 是派生 Document，保留原始媒体、时间/区域定位和置信度；
- 图表数据点、坐标、单位、图例和动画叙事分别验证；
- 渲染成功与科学验证是两道独立质量门。

### 技术栈与架构

- 后续技术选型可以决定关系库、对象存储、向量库、词法检索、图存储及 LlamaIndex 是否采用，但必须实现本文数据语义和状态机。
- 模型和数据库不得成为领域主键；稳定 ID、版本和 provenance 由平台掌握。
- 手动/CLI、Docker、Podman 部署必须使用同一迁移、领域包注册、索引版本和健康检查。
- 本地优先场景下，私人原文、索引和证据图默认在本地加密；外发 Qwen 的内容是任务所需最小 Chunk 和画像切片。

### 持续评测

后续评测至少覆盖：

- 关键 claim 支持率与 unsupported claim rate；
- citation entailment、citation completeness、citation locator accuracy；
- 撤稿/勘误/版本更新识别率和状态刷新时延；
- metadata-only 被错误当成全文证据的比例；
- 冲突检出、伪冲突消解和未决冲突披露率；
- 证据等级—措辞一致率；
- 混合检索 recall、rerank nDCG、来源多样性和独立证据覆盖；
- 租户越权召回率必须为零；
- 文档投毒、间接 prompt injection 和索引篡改对抗测试；
- fail-closed 覆盖和恢复率；
- 裸 Qwen、仅向量 RAG、带引用但无 claim 验证、完整可信流水线的消融对比。

评测样本要包含版本变化、撤稿、摘要与全文结论不一致、相同术语不同定义、单位陷阱、范围差异、伪造引用、恶意文档和跨用户同名材料。

## 13. 验收标准

本体系进入实施前，应以以下可验证产物作为完成定义：

1. 一套独立于具体框架的 Source/Document/Chunk/Claim/Evidence/Wording/Citation/Conflict Schema；
2. 至少一个公开来源和一个私人上传来源的完整 provenance bundle；
3. 同一作品多个版本、勘误/撤稿和预印本—正式版的状态解析测试；
4. 词法 + 向量 + 关系的混合检索和可解释排名记录；
5. claim 级 supports/refutes/limits 验证及精确 locator；
6. 至少两个不同问题类型的领域包，证明公共多维画像可做不同映射；
7. VERIFIED、QUALIFIED、PARTIAL、CONFLICTED、METADATA_ONLY、QUARANTINED、BLOCKED 的状态测试；
8. 任一安全或出处关键门失败时不退回模型记忆；
9. 两个账户上传同名/同内容文件时无跨用户召回、缓存和侧信道泄漏；
10. 给定同一文档快照、索引快照、领域包、工作流、模型和参数时，可回放关键检索与判定产物。

## 14. 来源清单

以下均为官方文档、标准、源码或一手 API；核验日期均为 2026-07-24：

- [LlamaIndex ingestion pipeline](https://docs.llamaindex.ai/en/v0.10.17/module_guides/loading/ingestion_pipeline/root.html)
- [LlamaIndex loading and Nodes](https://docs.llamaindex.ai/en/v0.10.19/understanding/loading/loading.html)
- [LlamaIndex Node Schema](https://docs.llamaindex.ai/en/v0.10.23/api_reference/schema/)
- [LlamaIndex hierarchical parser source](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/relational/hierarchical.py)
- [LlamaIndex fusion retriever example](https://docs.llamaindex.ai/en/v0.10.19/examples/low_level/fusion_retriever.html)
- [LlamaIndex Node Postprocessors](https://docs.llamaindex.ai/en/v0.10.20.post1/module_guides/querying/node_postprocessors/node_postprocessors.html)
- [LlamaIndex CitationQueryEngine source](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/query_engine/citation_query_engine.py)
- [LlamaIndex Security Policy](https://github.com/run-llama/llama_index/security)
- [W3C PROV-O](https://www.w3.org/TR/prov-o/)
- [W3C PROV-AQ](https://www.w3.org/TR/prov-aq/)
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [Crossref REST API filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/)
- [Crossmark](https://www.crossref.org/documentation/crossmark/)
- [Crossref Cited-by](https://www.crossref.org/documentation/cited-by/)
- [DataCite REST API](https://support.datacite.org/docs/api)
- [DataCite single DOI response](https://support.datacite.org/docs/api-get-doi)
- [DataCite versions and related identifiers](https://support.datacite.org/docs/connecting-versions-with-related-identifiers)
- [DataCite DOI states](https://support.datacite.org/docs/doi-states)
- [OpenAlex Works API](https://developers.openalex.org/api-reference/works)
- [arXiv API User's Manual](https://info.arxiv.org/help/api/user-manual.html)
- [arXiv Version Availability](https://info.arxiv.org/help/versions.html)
- [arXiv Withdraw / Retract](https://info.arxiv.org/help/withdraw.html)
- [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25499/)
- [PubMed CommentsCorrections DTD](https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/250101/el-CommentsCorrections.html)
- [NLM linked errata and retractions](https://www.nlm.nih.gov/bsd/policy/errata.html)
- [Europe PMC RESTful Web Service](https://europepmc.org/RestfulWebService)
- [Europe PMC Developer resources](https://europepmc.org/developers)
- [Oxford CEBM Levels of Evidence introduction](https://www.cebm.ox.ac.uk/resources/levels-of-evidence/levels-of-evidence-introductory-document)
- [Cochrane Handbook Chapter 14](https://training.cochrane.org/handbook/current/chapter-14)
- [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)

## 15. 研究置信度与待实施验证

- **高置信度**：来源版本化、claim 级证据绑定、检索与验证分离、PROV 出处链、多维证据画像、状态降级、租户预过滤和失败闭锁是本项目必要架构。
- **高置信度**：Crossref/DataCite/OpenAlex/arXiv/PubMed/Europe PMC 的公开能力互补且都有覆盖限制，不能选一个作为统一真相源。
- **中高置信度**：LlamaIndex 提供所需摄入、节点、融合、重排和引用构件；是否直接采用及具体版本需在技术栈任务中以 Conda `agent`、Docker/Podman 和 Qwen 集成进行兼容性验证。
- **需领域专家持续维护**：具体学科的证据评价、风险阈值和措辞映射。它们必须随领域包和评测集版本化，不能由通用模型一次生成后永久使用。
- **时效敏感**：外部 API 字段、访问条件、LlamaIndex 类名、Qwen 能力和价格。实施时必须再次核验，运行期通过 adapter/能力注册表监控，不写死在领域模型中。
