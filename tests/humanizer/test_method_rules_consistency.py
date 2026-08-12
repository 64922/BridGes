"""人味化方法规则的文档、提示词与全局策略一致性测试。"""

from __future__ import annotations

import re
from pathlib import Path

from bridges.chat.global_writing_policy import (
    GLOBAL_WRITING_POLICY_VERSION,
    GlobalWritingPolicyCompiler,
    GlobalWritingPolicyResource,
)
from bridges.contracts.chat import ChatMode
from bridges.contracts.expression import Genre
from bridges.contracts.humanizer import HumanizerPath, HumanizerTaskContract
from bridges.skills.humanizer.genre_rules import genre_rule_set
from bridges.skills.humanizer.method_rules import (
    CHAT_METHOD_RULES_INSTRUCTION,
    METHOD_RULES,
    METHOD_RULES_BLOCK_MARKER,
    MethodScene,
    method_rules_for_scene,
    render_method_rules,
)
from bridges.skills.humanizer.service import HumanizerService

_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bridges"
    / "skills"
    / "humanizer"
    / "skill"
    / "SKILL.md"
)


def test_skill_frontmatter_and_method_rule_ids_are_complete() -> None:
    skill = _SKILL_PATH.read_text(encoding="utf-8")
    assert skill.startswith("---\n")
    assert "plugin_id: bridges-humanizer" in skill
    assert "name: 文章人味化" in skill
    assert "version: 1.0.0" in skill
    assert "description:" in skill
    assert "source:" in skill
    assert "license:" in skill
    assert "capabilities:" in skill
    assert "data_categories:" in skill

    documented_ids = set(re.findall(r"规则标识：\s*`([^`]+)`", skill))
    runtime_ids = {rule.rule_id for rule in METHOD_RULES}
    assert documented_ids == runtime_ids
    missing_labels = [rule.label for rule in METHOD_RULES if rule.label not in skill]
    assert not missing_labels, missing_labels


def test_method_rule_renderer_exposes_one_shared_block() -> None:
    rewrite_block = render_method_rules(MethodScene.ARTICLE_REWRITE, genre_name="科普文案")
    generate_block = render_method_rules(MethodScene.ARTICLE_GENERATE, genre_name="科研汇报")

    assert METHOD_RULES_BLOCK_MARKER in rewrite_block
    assert METHOD_RULES_BLOCK_MARKER in generate_block
    assert all(
        rule.rule_id in rewrite_block
        for rule in method_rules_for_scene(MethodScene.ARTICLE_REWRITE)
    )
    assert "场景档位：文章改写" in rewrite_block
    assert "场景档位：文章生成" in generate_block


def test_humanizer_rewrite_and_generate_prompts_include_method_rules() -> None:
    service = HumanizerService.__new__(HumanizerService)

    expected_scene_rules = {
        HumanizerPath.REWRITE: "scene.article-rewrite",
        HumanizerPath.GENERATE: "scene.article-generate",
    }
    for path in (HumanizerPath.REWRITE, HumanizerPath.GENERATE):
        contract = HumanizerTaskContract(path=path, genre=Genre.POPULAR_SCIENCE)
        prompt = service._build_system_prompt(
            "1.0.0",
            contract,
            genre_rule_set(contract.genre),
            [],
            "主题",
            [],
        )
        assert METHOD_RULES_BLOCK_MARKER in prompt
        assert "rewrite.main-clause-first" in prompt
        assert "fact.relevance-not-causality" in prompt
        assert expected_scene_rules[path] in prompt


def test_global_chat_policy_is_lightweight_and_free_of_method_rules() -> None:
    """Issue 07：聊天轻量策略不再注入共享方法规则块或内部方法 ID。"""
    snapshot = GlobalWritingPolicyCompiler().compile(ChatMode.COMPANION)
    fallback = GlobalWritingPolicyCompiler(resource=None).compile(ChatMode.COMPANION)
    custom = GlobalWritingPolicyCompiler(
        GlobalWritingPolicyResource(instruction="只使用自定义表达说明。")
    ).compile(ChatMode.COMPANION)

    assert GLOBAL_WRITING_POLICY_VERSION == "global-humanized-writing-v2"
    assert CHAT_METHOD_RULES_INSTRUCTION.startswith("【人味方法规则块")
    # 渲染结果不包含文章/方法规则块、内部方法 ID 或全量禁词表。
    assert METHOD_RULES_BLOCK_MARKER not in snapshot.system_block
    assert "chat.frequency-alert" not in snapshot.system_block
    assert "rewrite.main-clause-first" not in snapshot.system_block
    assert METHOD_RULES_BLOCK_MARKER not in fallback.system_block
    assert "chat.frequency-alert" not in fallback.system_block
    # 自定义 instruction 仍追加在渲染结果之后。
    assert "只使用自定义表达说明。" in custom.system_block
    assert "先直接回答当前问题" in snapshot.system_block
    assert "文章人味化任务的最终文章不经过本策略二次改写" in snapshot.system_block
