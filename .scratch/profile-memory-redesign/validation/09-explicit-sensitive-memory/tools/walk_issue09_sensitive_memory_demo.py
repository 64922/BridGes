"""画像工单 09 走查：敏感自述、用途与凭据的写入/采用边界（确定性）。

覆盖六个用户可见旅程（真实 SQLite 存储 + 真实聊天预处理管线，不调用外部
模型、不读取任何凭据）：

[1] 心理学兴趣是普通事实：明确记住 → 保存 → 相关提问采用。
[2] 短期情境不进长期记忆：自动提交「焦虑」/「过敏」候选零写入。
[3] 明确记住的过敏信息按用途保存：户外建议采用、其他问题不带；未说明用途
    时如实拒绝（UNRESOLVED + purpose_required），运行记账不留原文；补充
    用途后可保存。
[4] 凭据即使明确要求也不保存：密码/验证码/访问密钥全部拒绝；运行记账与
    数据库行不含凭据明文。
[5] 清除用途后旧采用与迟到提交失效；恢复用途只在匹配场景重新采用。
[6] 用途落库跨重启保留；聊天侧对未保存结果给出诚实约束（不声称已记住）。

用法：
    python .scratch/profile-memory-redesign/validation/09-explicit-sensitive-memory/tools/walk_issue09_sensitive_memory_demo.py \
        --out .scratch/profile-memory-redesign/validation/09-explicit-sensitive-memory/evidence/demo-walkthrough-<stamp>.txt

脚本只使用临时库（`.tmp/issue09-demo-*`），不读取或记录任何凭据与真实数据。
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bridges.chat.turn import profile_memory_context  # noqa: E402
from bridges.contracts.atomic_profile import (  # noqa: E402
    AtomicProfileItemModifyRequest,
    AtomicProfileMemoryStatus,
)
from bridges.contracts.profile_extraction import (  # noqa: E402
    ProfileExtractionOutput,
    ProfileExtractionStatus,
)
from bridges.contracts.profiles import FourDimensionConfidence  # noqa: E402
from bridges.profiles.adapters import InMemoryProfileRepository  # noqa: E402
from bridges.profiles.atomic import (  # noqa: E402
    RECALL_PURPOSE_MISMATCH_REASON,
    RECALL_PURPOSE_REQUIRED_REASON,
    AtomicProfileService,
    SqliteAtomicProfileRepository,
)
from bridges.profiles.automatic import (  # noqa: E402
    AutomaticProfileService,
    SqliteAutomaticProfileRepository,
)
from bridges.profiles.commit import ProfileFactSubmission  # noqa: E402
from bridges.profiles.four_dimensions import (  # noqa: E402
    FourDimensionProfileService,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.purpose import build_purpose  # noqa: E402
from bridges.storage.database import BridgesDatabase  # noqa: E402

ACCOUNT = "demo-alice"
PSYCH_TEXT = "我对心理学很感兴趣"
PSYCH_QUERY = "推荐几本心理学科普书"
ALLERGY_REMEMBER = "记住我有花粉过敏，户外建议要注意"
OUTDOOR_QUERY = "我花粉过敏，周末想去户外徒步，有什么建议？"
PLAN_QUERY = "帮我制定下周的学习计划"


class _Script:
    def __init__(self, out: list[str]) -> None:
        self._out = out
        self.failures = 0

    def say(self, text: str) -> None:
        print(text)
        self._out.append(text)

    def check(self, ok: bool, label: str) -> None:
        status = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        self.say(f"    [{status}] {label}")


class _Stack:
    """真实 SQLite 栈：原子画像 + 自动画像（可带一个确定性抽取器替身）。"""

    def __init__(self, path: Path, extractor: object | None = None) -> None:
        self.database = BridgesDatabase(path)
        self.database.initialize()
        self.four = FourDimensionProfileService(
            source_repository=InMemoryProfileRepository(),
            repository=SqliteFourDimensionProfileRepository(self.database),
        )
        self.atomic = AtomicProfileService(
            self.four, SqliteAtomicProfileRepository(self.database)
        )
        self.state = SqliteAutomaticProfileRepository(self.database)
        self.automatic = AutomaticProfileService(
            four_dimension_service=self.four,
            repository=self.state,
            extractor=extractor,
            atomic_profile_service=self.atomic,
        )

    def remember_via_chat(self, content: str, *, message_id: str = "m-1"):
        return self.automatic.process_synchronous_controls(
            ACCOUNT,
            conversation_id="c-1",
            message_id=message_id,
            content=content,
            run_id=f"r-{message_id}",
            mode="companion",
        )

    def adopted(self, query: str) -> list[str]:
        slice_ = self.atomic.compile_adopted_slice(
            ACCOUNT,
            run_id=f"slice-{query}",
            purpose=build_purpose(mode="companion", query=query),
        )
        return [item.fact_text for item in slice_.adopted_items]

    def exclusion_reasons(self, query: str) -> dict[str, str]:
        slice_ = self.atomic.compile_adopted_slice(
            ACCOUNT,
            run_id=f"slice-ex-{query}",
            purpose=build_purpose(mode="companion", query=query),
        )
        return {item.fact_text: item.exclusion_reason for item in slice_.excluded_items}

    def close(self) -> None:
        self.database.close()


class _SensitiveExtractor:
    """抽取器提出过敏候选：服务端候选级拦截必须零写入。"""

    version = "demo-sensitive-v1"

    def extract(self, *, message_id: str, **_ignored: object) -> ProfileExtractionOutput:
        return ProfileExtractionOutput.model_validate(
            {
                "items": [
                    {
                        "normalized_value": "对花生过敏",
                        "fact_text": "对花生过敏",
                        "evidence_ref": message_id,
                        "reliability": 0.95,
                        "action": "create",
                    }
                ]
            }
        )


def journey_1_psychology_interest(script: _Script, tmp: Path) -> None:
    script.say("[1] 心理学兴趣：普通事实，正常保存与采用")
    stack = _Stack(tmp / "psych.db")
    result = stack.remember_via_chat("记住，" + PSYCH_TEXT)
    assert result is not None and result.memory is not None
    script.say(f"    记住结果：{result.memory.status.value}")
    items = stack.atomic.list_items(ACCOUNT)
    script.check(
        result.memory.status is AtomicProfileMemoryStatus.REMEMBERED
        and [item.text for item in items] == [PSYCH_TEXT],
        "心理学兴趣已保存（未被敏感词误拦）",
    )
    script.check(PSYCH_TEXT in stack.adopted(PSYCH_QUERY), "相关提问采用该兴趣")
    stack.close()


def journey_2_transient_anxiety(script: _Script, tmp: Path) -> None:
    script.say("[2] 短期情境：焦虑/过敏不进长期记忆（自动路径零写入）")
    stack = _Stack(tmp / "transient.db", extractor=_SensitiveExtractor())
    for text, message_id in (
        ("我最近很焦虑", "m-anxious"),
        ("我对花生过敏", "m-allergy"),
    ):
        submitted = stack.atomic.commit_fact(
            ACCOUNT,
            ProfileFactSubmission(
                text=text,
                confidence=FourDimensionConfidence.MEDIUM,
                evidence_message_id=message_id,
            ),
        )
        script.check(submitted is None, f"自动提交「{text}」被拒（零写入）")
    result = stack.automatic.preprocess_message(
        ACCOUNT,
        conversation_id="c-transient",
        message_id="m-candidate",
        content="我对花生过敏",
        run_id="r-candidate",
        mode="companion",
    )
    script.check(
        result is not None
        and result.run.status == ProfileExtractionStatus.SUCCEEDED
        and stack.atomic.list_items(ACCOUNT) == [],
        "抽取器提出的过敏候选被候选级门禁拦截（条目 0 条）",
    )
    stack.close()


def journey_3_allergy_purpose(script: _Script, tmp: Path) -> None:
    script.say("[3] 明确记住的过敏信息：按用途保存与采用；缺用途如实拒绝")
    stack = _Stack(tmp / "allergy.db")

    # 缺用途：如实拒绝，原文不留在运行记账；重放结论一致。
    rejected = stack.remember_via_chat("记住我对花生过敏", message_id="m-nopurpose")
    assert rejected is not None and rejected.memory is not None
    script.say(
        "    缺用途结果："
        f"{rejected.memory.status.value} / "
        f"{rejected.memory.blocked_reason.value if rejected.memory.blocked_reason else ''}"
    )
    script.check(
        rejected.memory.status is AtomicProfileMemoryStatus.UNRESOLVED
        and "花生" not in rejected.run.source_snapshot
        and stack.atomic.list_items(ACCOUNT) == [],
        "缺用途被拒且运行记账不含原文",
    )
    replay = stack.remember_via_chat("记住我对花生过敏", message_id="m-nopurpose")
    script.check(
        replay is not None
        and replay.memory is not None
        and replay.memory.status is AtomicProfileMemoryStatus.UNRESOLVED
        and stack.atomic.list_items(ACCOUNT) == [],
        "重放同一消息仍零写入（幂等、不重试）",
    )

    # 带用途：解析「户外」并保存；只有匹配用途的问题携带。
    saved = stack.remember_via_chat(ALLERGY_REMEMBER, message_id="m-outdoor")
    assert saved is not None and saved.memory is not None
    items = stack.atomic.list_items(ACCOUNT)
    script.say(f"    保存结果：{saved.memory.status.value}，用途={items[0].usage_purpose}")
    script.check(
        saved.memory.status is AtomicProfileMemoryStatus.REMEMBERED
        and items[0].usage_purpose == "户外",
        "过敏信息按「户外」用途保存",
    )
    script.check(items[0].text in stack.adopted(OUTDOOR_QUERY), "户外建议问题携带该信息")
    reasons = stack.exclusion_reasons(PLAN_QUERY)
    script.check(
        reasons.get(items[0].text) == RECALL_PURPOSE_MISMATCH_REASON,
        "其他问题不携带（用途不匹配）",
    )
    stack.close()


def journey_4_credentials(script: _Script, tmp: Path) -> None:
    script.say("[4] 凭据：即使明确要求也不进入记忆与派生存储")
    stack = _Stack(tmp / "credential.db")
    cases = (
        ("记住我的密码是 abc123456", "abc123456"),
        ("记住我的验证码是 246810", "246810"),
        ("记住我的访问密钥是 sk-demo-98765", "sk-demo-98765"),
    )
    for index, (content, secret) in enumerate(cases, start=1):
        result = stack.remember_via_chat(content, message_id=f"m-secret-{index}")
        assert result is not None and result.memory is not None
        run_json = result.run.model_dump_json()
        rows = stack.database.connection.execute(
            "SELECT source_snapshot FROM profile_extraction_runs"
            " WHERE account_id = ?",
            (ACCOUNT,),
        ).fetchall()
        leaked = secret in run_json or any(
            secret in str(row["source_snapshot"]) for row in rows
        )
        script.say(
            f"    案例 {index}：{result.memory.status.value} / "
            f"{result.memory.blocked_reason.value if result.memory.blocked_reason else ''}"
        )
        script.check(
            result.memory.status is AtomicProfileMemoryStatus.UNRESOLVED and not leaked,
            f"案例 {index}：凭据被拒且明文不落库",
        )
    script.check(stack.atomic.list_items(ACCOUNT) == [], "画像条目 0 条")
    stack.close()


def journey_5_purpose_shrink(script: _Script, tmp: Path) -> None:
    script.say("[5] 清除/恢复用途：旧采用与迟到提交失效；恢复不扩大范围")
    stack = _Stack(tmp / "shrink.db")
    stack.remember_via_chat(ALLERGY_REMEMBER, message_id="m-outdoor")
    item = stack.atomic.list_items(ACCOUNT)[0]
    original_version = item.version
    script.check(item.text in stack.adopted(OUTDOOR_QUERY), "前置：户外问题采用")

    cleared = stack.atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(
            text=item.text, version=original_version, usage_purpose=""
        ),
    )
    script.say(f"    清除用途：版本 {original_version} → {cleared.version}")
    reasons = stack.exclusion_reasons(OUTDOOR_QUERY)
    script.check(
        cleared.usage_purpose is None
        and cleared.version == original_version + 1
        and reasons.get(item.text) == RECALL_PURPOSE_REQUIRED_REASON,
        "清除后不再进入回答（含旧采用失效）",
    )
    late = stack.atomic.commit_fact(
        ACCOUNT,
        ProfileFactSubmission(
            text=item.text,
            confidence=FourDimensionConfidence.HIGH,
            evidence_message_id="m-late",
        ),
    )
    script.check(late is None, "迟到提交被拒（敏感事实自动路径零写入）")

    restored = stack.atomic.modify_item(
        ACCOUNT,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(
            text=item.text, version=cleared.version, usage_purpose="户外"
        ),
    )
    script.check(
        restored.usage_purpose == "户外"
        and item.text in stack.adopted(OUTDOOR_QUERY)
        and item.text not in stack.adopted(PLAN_QUERY),
        "恢复用途后只在匹配场景重新采用（不扩大范围）",
    )
    stack.close()


def journey_6_restart_and_chat_copy(script: _Script, tmp: Path) -> None:
    script.say("[6] 用途跨重启保留；未保存结果的聊天约束诚实")
    path = tmp / "restart.db"
    stack = _Stack(path)
    stack.remember_via_chat(ALLERGY_REMEMBER, message_id="m-outdoor")
    item = stack.atomic.list_items(ACCOUNT)[0]
    stack.close()

    reopened = _Stack(path)
    restored = reopened.atomic.get_item(ACCOUNT, item.profile_item_id)
    script.check(
        restored.usage_purpose == "户外" and restored.text == item.text,
        "重启后用途与正文一致",
    )
    reopened.close()

    credential_copy = profile_memory_context(
        {
            "memory_kind": "remember",
            "status": "unresolved",
            "matched_count": 0,
            "blocked_reason": "credential",
        }
    )
    purpose_copy = profile_memory_context(
        {
            "memory_kind": "remember",
            "status": "unresolved",
            "matched_count": 0,
            "blocked_reason": "purpose_required",
        }
    )
    script.check(
        credential_copy is not None
        and purpose_copy is not None
        and "不得声称已记住" in credential_copy
        and "不得声称已记住" in purpose_copy,
        "聊天约束：未保存时不声称已记住",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="证据输出文件")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_lines = [f"画像工单 09 走查（确定性） {stamp}"]
    script = _Script(out_lines)
    script.say(
        f"环境：conda agent / Python {sys.version.split()[0]}；"
        "临时库 .tmp/issue09-demo-*"
    )

    tmp_root = REPO_ROOT / ".tmp" / f"issue09-demo-{stamp}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    script.say("")
    journey_1_psychology_interest(script, tmp_root)
    script.say("")
    journey_2_transient_anxiety(script, tmp_root)
    script.say("")
    journey_3_allergy_purpose(script, tmp_root)
    script.say("")
    journey_4_credentials(script, tmp_root)
    script.say("")
    journey_5_purpose_shrink(script, tmp_root)
    script.say("")
    journey_6_restart_and_chat_copy(script, tmp_root)

    script.say("")
    script.say(
        f"结果：{'全部通过' if script.failures == 0 else f'{script.failures} 项失败'}"
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        print(f"证据已写入 {args.out}")
    return 1 if script.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
