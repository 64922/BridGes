"""Issue 24：统一桌面搜索服务级测试。

覆盖三类内容命中、组合筛选、高亮片段、账户隔离、改名/删除即时反映、
空查询与索引就绪信号。全部经真实摄取状态机与权威数据库验证。
"""

from __future__ import annotations

from typing import Any

import pytest

from bridges.search import SearchService
from tests.search.conftest import (
    add_image,
    add_material,
    add_message,
    seed_conversation,
)


def _search(env: dict[str, Any], account_id: str, query: str, **kwargs: Any) -> Any:
    service: SearchService = env["search"]
    return service.search(account_id, query=query, **kwargs)


class TestThreeContentTypes:
    def test_chat_title_hit(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        seed_conversation(env, account, "量子计算入门讨论")
        response = _search(env, account, "量子计算")
        chat = [item for item in response.results if item.result_type == "chat"]
        assert len(chat) == 1
        assert chat[0].conversation_id is not None
        assert chat[0].message_id is None
        assert response.counts["chat"] == 1

    def test_chat_message_hit(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        conversation_id = seed_conversation(env, account, "普通对话")
        message_id = add_message(env, account, conversation_id, "今天学习了光合作用原理")
        response = _search(env, account, "光合作用")
        chat = [item for item in response.results if item.result_type == "chat"]
        assert len(chat) == 1
        assert chat[0].message_id == message_id
        assert chat[0].conversation_id == conversation_id
        assert chat[0].result_id == message_id

    def test_document_name_and_text_hits(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        add_material(env, account, "热力学笔记.txt", "一些无关内容")
        add_material(env, account, "其他.txt", "熵增定律是热力学第二定律")
        response = _search(env, account, "热力学")
        documents = [item for item in response.results if item.result_type == "document"]
        assert len(documents) == 2
        assert response.counts["document"] == 2
        by_title = {item.title: item for item in documents}
        # 名称命中：片段取自展示名（原始文件名）；正文命中：片段取自分块内容。
        name_hit = by_title["热力学笔记.txt"]
        assert name_hit.page_number is None
        text_hit = by_title["其他.txt"]
        assert any(seg.matched and seg.text == "热力学" for seg in text_hit.snippet)

    def test_document_anchor_from_chunk(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        add_material(env, account, "锚点文档.txt", "广义相对论与引力波")
        # 模拟解析器写入页码/章节锚点（文本解析器本身不写页码）。
        env["database"].connection.execute(
            "UPDATE document_chunks SET page_number = 3, section_title = '第三章'"
            " WHERE account_id = ?",
            (account,),
        )
        response = _search(env, account, "引力波")
        documents = [item for item in response.results if item.result_type == "document"]
        assert len(documents) == 1
        assert documents[0].page_number == 3
        assert documents[0].section_title == "第三章"
        assert documents[0].object_id is not None

    def test_image_filename_and_metadata_hits(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        add_image(env, account, "星云照片.png")
        # 文件名命中：摄取元数据也包含文件名，同一对象只返回一条。
        response = _search(env, account, "星云")
        images = [item for item in response.results if item.result_type == "image"]
        assert len(images) == 1
        assert images[0].object_id is not None
        assert images[0].result_id == images[0].object_id
        # 元数据命中：查询只出现在摄取元数据文本中的内容。
        metadata_hit = _search(env, account, "640×480")
        meta_images = [
            item for item in metadata_hit.results if item.result_type == "image"
        ]
        assert len(meta_images) == 1

    def test_single_query_returns_multiple_types(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        seed_conversation(env, account, "黑洞漫谈")
        add_material(env, account, "黑洞讲义.txt", "事件视界")
        add_image(env, account, "黑洞模拟.png")
        response = _search(env, account, "黑洞")
        assert response.counts["chat"] >= 1
        assert response.counts["document"] >= 1
        assert response.counts["image"] >= 1
        assert response.index_ready is True


class TestFilters:
    def test_type_subset(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        seed_conversation(env, account, "暗物质讨论")
        response = _search(env, account, "暗物质", types={"chat"})
        # counts 契约：筛选条件下仍给出三类真实命中数（供 tab 计数切换），
        # 结果列表才受 types 约束。
        assert response.counts["chat"] == 1
        assert all(item.result_type == "chat" for item in response.results)

    def test_time_range_filter(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        seed_conversation(
            env, account, "旧的对流讨论", updated_at="2024-01-01T00:00:00+00:00"
        )
        seed_conversation(
            env, account, "新的对流讨论", updated_at="2026-01-01T00:00:00+00:00"
        )
        from datetime import datetime

        response = _search(
            env,
            account,
            "对流",
            from_=datetime.fromisoformat("2025-06-01T00:00:00+00:00"),
        )
        titles = {item.title for item in response.results if item.result_type == "chat"}
        assert titles == {"新的对流讨论"}
        bounded = _search(
            env,
            account,
            "对流",
            to=datetime.fromisoformat("2025-06-01T00:00:00+00:00"),
        )
        bounded_titles = {
            item.title for item in bounded.results if item.result_type == "chat"
        }
        assert bounded_titles == {"旧的对流讨论"}

    def test_like_wildcards_are_escaped(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        seed_conversation(env, account, "进度 50% 记录")
        seed_conversation(env, account, "进度 500 记录")
        response = _search(env, account, "50%")
        titles = {item.title for item in response.results if item.result_type == "chat"}
        assert titles == {"进度 50% 记录"}
        underscore = _search(env, account, "_")
        assert underscore.results == []


class TestSnippetHighlight:
    def test_matched_segments_equal_query(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        conversation_id = seed_conversation(env, account, "高亮测试")
        add_message(
            env,
            account,
            conversation_id,
            "前文铺垫" * 30 + "超新星爆发" + "后文补充" * 30,
        )
        response = _search(env, account, "超新星爆发")
        item = next(i for i in response.results if i.message_id is not None)
        matched = [seg for seg in item.snippet if seg.matched]
        assert [seg.text for seg in matched] == ["超新星爆发"]
        plain = "".join(seg.text for seg in item.snippet if not seg.matched)
        assert "前文铺垫" in plain and "后文补充" in plain
        # 窗口截断：片段长度远小于全文。
        snippet_len = sum(len(seg.text) for seg in item.snippet)
        assert snippet_len < 200


class TestAccountIsolation:
    def test_same_named_content_isolated(self, env: dict[str, Any]) -> None:
        account_a, account_b = env["account_a"], env["account_b"]
        for account in (account_a, account_b):
            seed_conversation(env, account, "同名会话")
            add_material(env, account, "同名文档.txt", "同名正文")
            add_image(env, account, "同名图片.png")
        for account in (account_a, account_b):
            response = _search(env, account, "同名")
            assert response.counts == {"chat": 1, "image": 1, "document": 1}
            # 账户内各类恰好一条，证明没有读到另一账户的同名内容。


class TestMutationFreshness:
    def test_conversation_rename_and_delete(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        conversation_id = seed_conversation(env, account, "旧标题")
        message_id = add_message(env, account, conversation_id, "脉冲星观测")
        service: SearchService = env["search"]
        assert service.search(account, query="旧标题").counts["chat"] == 1
        env["database"].connection.execute(
            "UPDATE conversations SET title = '新标题' WHERE account_id = ?",
            (account,),
        )
        assert service.search(account, query="旧标题").counts["chat"] == 0
        renamed = service.search(account, query="新标题")
        assert renamed.counts["chat"] == 1
        # 消息命中随改名后的会话仍返回，删除会话后标题与消息命中同时消失。
        assert service.search(account, query="脉冲星").counts["chat"] == 1
        env["database"].connection.execute(
            "DELETE FROM messages WHERE account_id = ? AND message_id = ?",
            (account, message_id),
        )
        env["database"].connection.execute(
            "DELETE FROM conversations WHERE account_id = ? AND conversation_id = ?",
            (account, conversation_id),
        )
        assert service.search(account, query="新标题").counts["chat"] == 0
        assert service.search(account, query="脉冲星").counts["chat"] == 0


class TestEmptyQueryAndIndexReady:
    def test_blank_query_returns_empty_results(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        seed_conversation(env, account, "任意对话")
        for query in ("", "   ", "　　"):
            response = _search(env, account, query)
            assert response.results == []
            assert response.counts == {"chat": 0, "image": 0, "document": 0}
            assert response.index_ready is True

    def test_index_not_ready_while_document_pending(self, env: dict[str, Any]) -> None:
        account = env["account_a"]
        add_material(env, account, "待处理.txt", "引力透镜", process=False)
        pending = _search(env, account, "引力透镜")
        assert pending.index_ready is False
        env["ingestion"].process_pending()
        ready = _search(env, account, "引力透镜")
        assert ready.index_ready is True
        assert ready.counts["document"] == 1
