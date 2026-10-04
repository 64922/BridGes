"""工单 37 验收夹具：为四个模块种出真实内核依赖链。

这些链用于验证生产证据核验：交付投影必须与 ``verify``/``evaluate`` 载荷
一致，链上每个节点按配方依赖与哈希相互绑定。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleDelivery
from bridges.kernel.contracts import ArtifactTrust, InputDependency, NodeArtifact
from bridges.kernel.repository import NodeKernelRepository
from bridges.storage.database import BridgesDatabase

MODULE_FIELDS = {
    "paper": "paper_search",
    "resources": "learning_resources",
    "career": "career_plan",
    "github": "github_projects",
}


def _recipe(module: str) -> Any:
    if module == "paper":
        from bridges.paper.kernel import build_paper_recipe

        return build_paper_recipe()
    if module == "resources":
        from bridges.resources.kernel import build_resources_recipe

        return build_resources_recipe()
    if module == "career":
        from bridges.career_plan.kernel import build_career_recipe

        return build_career_recipe()
    from bridges.github.kernel import build_github_recipe

    return build_github_recipe()


def seed_chain(
    database: BridgesDatabase,
    *,
    module: str,
    run_id: str,
    account_id: str,
    conversation_id: str,
    payloads: dict[str, dict[str, Any]] | None = None,
    task_id: str | None = None,
    task_version: int | None = None,
    created_at: datetime | None = None,
    trust_state: ArtifactTrust = ArtifactTrust.EVIDENCE_BOUND,
    terminal_trust: ArtifactTrust | None = None,
) -> dict[str, NodeArtifact]:
    """按配方节点顺序种出完整依赖链，返回 node → NodeArtifact。"""
    recipe = _recipe(module)
    repository = NodeKernelRepository(database)
    moment = created_at or datetime.now(UTC)
    built: dict[str, NodeArtifact] = {}
    for spec in recipe.nodes:
        dependencies = tuple(
            InputDependency(
                node=name,
                artifact_id=built[name].artifact_id,
                content_hash=built[name].content_hash,
            )
            for name in spec.depends_on
        )
        artifact = NodeArtifact.build(
            account_id=account_id,
            conversation_id=conversation_id,
            run_id=run_id,
            task_id=task_id,
            task_version=task_version,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.recipe_version,
            node=spec.name,
            artifact_type=spec.artifact_type,
            capability_version=spec.capability_version,
            trust_state=(
                terminal_trust
                if terminal_trust is not None and spec.name == recipe.nodes[-1].name
                else trust_state
            ),
            input_key=f"test:{module}:{spec.name}",
            input_deps=dependencies,
            source_refs=(),
            read_scope="测试读取范围",
            requirement_coverage=(),
            unconfirmed=(),
            error=None,
            payload=dict((payloads or {}).get(spec.name, {})),
            now=moment,
        )
        repository.save_artifact(artifact)
        built[spec.name] = artifact
    return built


def delivery_from_chain(
    module: str,
    chain: dict[str, NodeArtifact],
    *,
    projection: dict[str, Any],
    content: str,
    status: str = "success",
    message_status: str = ChatMessageStatus.DONE.value,
) -> ModuleDelivery:
    terminal = "github.present" if module == "github" else f"{module}.verify"
    return ModuleDelivery(
        module_id=module,
        status=status,
        projection_field=MODULE_FIELDS[module],
        projection=projection,
        content=content,
        message_status=message_status,
        artifact_refs={terminal: chain[terminal].artifact_id},
    )


def paper_recommendation(
    *, order: int = 1, title: str = "入门论文", arxiv_id: str = "2401.00001"
) -> dict[str, Any]:
    return {
        "order": order,
        "arxiv_id": arxiv_id,
        "doi": None,
        "title": title,
        "authors": ["作者"],
        "published_year": 2024,
        "source": "arxiv",
        "venue": None,
        "cited_by_count": None,
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": None,
        "full_text_url": None,
        "primary_category": None,
        "full_text_available": True,
        "read_scope": "abstract",
        "role": "survey",
        "reason_zh": "入门推荐",
        "match_basis": "命中主题词",
        "match_evidence": [],
        "supported_claims": [],
        "summary_zh": None,
        "summary_evidence": None,
        "source_abstract": "来源摘要",
        "read_evidence": [],
        "unverified": [],
    }


def paper_identity(
    *, order: int = 1, title: str = "入门论文", arxiv_id: str = "2401.00001"
) -> dict[str, Any]:
    return {
        "order": order,
        "arxiv_id": arxiv_id,
        "doi": None,
        "title": title,
        "published_year": 2024,
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "content_hash": f"hash-{arxiv_id}",
    }


def seed_paper(
    database: BridgesDatabase,
    *,
    run_id: str,
    account_id: str,
    conversation_id: str,
    recommendations: list[dict[str, Any]] | None = None,
    selection: list[dict[str, Any]] | None = None,
    content: str = "论文正文：一篇入门论文。",
    created_at: datetime | None = None,
) -> tuple[dict[str, NodeArtifact], ModuleDelivery]:
    from bridges.paper.contracts import PaperSearchProjection

    papers = recommendations or [paper_recommendation()]
    chosen = selection if selection is not None else [paper_identity()]
    chain = seed_chain(
        database,
        module="paper",
        run_id=run_id,
        account_id=account_id,
        conversation_id=conversation_id,
        payloads={
            "paper.evaluate": {
                "recommendations": papers,
                "selection": chosen,
                "unsupported_claims": [],
                "hard_condition_violations": [],
                "identity_missing": False,
                "unconfirmed": [],
                "notes": [],
            },
            "paper.verify": {
                "unsupported_claims": [],
                "hard_condition_violations": [],
                "unofficial": [],
                "unconfirmed": [],
                "notes": [],
                "independent_review": {},
            },
        },
        created_at=created_at,
        terminal_trust=ArtifactTrust.EVIDENCE_BOUND,
    )
    projection = PaperSearchProjection(
        status="success",
        papers=papers,
        selected=chosen,
        final_query="入门论文",
    ).model_dump(mode="json")
    return chain, delivery_from_chain(
        "paper", chain, projection=projection, content=content
    )


def seed_resources(
    database: BridgesDatabase,
    *,
    run_id: str,
    account_id: str,
    conversation_id: str,
    items: list[dict[str, Any]] | None = None,
    content: str = "资料正文：一条精简路径。",
    created_at: datetime | None = None,
    terminal_trust: ArtifactTrust = ArtifactTrust.QUALIFIED,
) -> tuple[dict[str, NodeArtifact], ModuleDelivery]:
    payload_items = items if items is not None else [
        {
            "order": 1,
            "kind": "book",
            "title": "入门资料",
            "creator": "作者",
            "source": "openlibrary",
            "url": "https://example.org/book",
            "stage": "入门",
            "reason_zh": "适合零基础",
            "match_basis": "命中主题",
            "read_scope": "标题元数据",
            "unverified": [],
        }
    ]
    projection = {
        "status": "success",
        "original_phrase": "入门资料",
        "normalized_term": "入门资料",
        "items": payload_items,
        "path_verified": True,
    }
    chain = seed_chain(
        database,
        module="resources",
        run_id=run_id,
        account_id=account_id,
        conversation_id=conversation_id,
        payloads={
            "resources.read": {"book_records": [], "video_records": []},
            "resources.verify": {"projection": projection},
        },
        created_at=created_at,
        terminal_trust=terminal_trust,
    )
    return chain, delivery_from_chain(
        "resources", chain, projection=projection, content=content
    )


def seed_career(
    database: BridgesDatabase,
    *,
    run_id: str,
    account_id: str,
    conversation_id: str,
    topic: str = "Java 后端开发",
    requirements: list[dict[str, Any]] | None = None,
    content: str = "岗位正文：公开样本。",
    created_at: datetime | None = None,
    task_id: str | None = None,
    task_version: int | None = None,
) -> tuple[dict[str, NodeArtifact], ModuleDelivery]:
    from bridges.career_plan.contracts import CareerPlanProjection

    public_requirements = requirements if requirements is not None else [
        {
            "kind": "resources",
            "topic": topic,
            "goal": f"学习 {topic}",
            "skills": ["Java", "Spring Boot"],
            "basis": [f"负责 {topic}"],
            "inference": True,
        },
        {
            "kind": "github",
            "topic": topic,
            "goal": f"找 {topic} 练手项目",
            "skills": ["Java", "Spring Boot"],
            "basis": [f"负责 {topic}"],
            "inference": True,
        },
    ]
    moment = created_at or datetime.now(UTC)
    projection = CareerPlanProjection(
        status="success",
        topic=topic,
        original_request=f"找 {topic} 实习",
        samples=[],
        gaps=[],
        personal_advices=[],
        combination_requirements=public_requirements,
        completed_at=moment,
    ).model_dump(mode="json")
    chain = seed_chain(
        database,
        module="career",
        run_id=run_id,
        account_id=account_id,
        conversation_id=conversation_id,
        payloads={"career.verify": {"projection": projection}},
        task_id=task_id,
        task_version=task_version,
        created_at=created_at,
        terminal_trust=ArtifactTrust.QUALIFIED,
    )
    return chain, delivery_from_chain(
        "career", chain, projection=projection, content=content
    )
