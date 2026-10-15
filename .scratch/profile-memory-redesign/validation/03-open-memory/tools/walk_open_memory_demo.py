"""工单 03 演示走查：真实 SQLite 上的记住→编辑→采用→删除→重启。

运行（在仓库根、conda agent 环境、PYTHONPATH 指向 src）：

    $env:PYTHONPATH="<repo>/src"
    python .scratch/profile-memory-redesign/validation/03-open-memory/tools/walk_open_memory_demo.py

输出是可直接入档的证据文本：每步的真实数据库写入目标（表、行、字段）、
四维旧表计数、隔离结果与重启一致性。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from bridges.contracts.atomic_profile import AtomicProfileItemModifyRequest
from bridges.contracts.profiles import FourDimensionConfidence
from bridges.profiles import (
    AtomicProfileService,
    AutomaticProfileService,
    FourDimensionProfileService,
    RuleBasedAutomaticProfileExtractor,
    SqliteAtomicProfileRepository,
    SqliteAutomaticProfileRepository,
    SqliteFourDimensionProfileRepository,
)
from bridges.profiles.commit import ProfileFactSubmission
from bridges.profiles.purpose import build_purpose
from bridges.storage.database import BridgesDatabase

ALICE = "account-alice"
BOB = "account-bob"


def _counts(database: BridgesDatabase, account_id: str) -> dict[str, int]:
    return {
        "profile_items": database.scoped(account_id).execute(
            "SELECT COUNT(*) AS n FROM profile_items WHERE account_id = ?",
            (account_id,),
        ).fetchone()["n"],
        "profile_four_dimension_records": database.scoped(account_id).execute(
            "SELECT COUNT(*) AS n FROM profile_four_dimension_records "
            "WHERE account_id = ?",
            (account_id,),
        ).fetchone()["n"],
    }


def _dump_items(database: BridgesDatabase, account_id: str) -> list[tuple]:
    rows = database.scoped(account_id).execute(
        "SELECT text, status, write_origin, topics_json, version "
        "FROM profile_items WHERE account_id = ? ORDER BY created_at",
        (account_id,),
    ).fetchall()
    return [
        (
            row["text"],
            row["status"],
            row["write_origin"],
            row["topics_json"],
            row["version"],
        )
        for row in rows
    ]


class _Stack:
    def __init__(self, path: Path) -> None:
        self.database = BridgesDatabase(path)
        self.database.initialize()
        four = FourDimensionProfileService(
            source_repository=None,  # type: ignore[arg-type]
            repository=SqliteFourDimensionProfileRepository(self.database),
        )
        self.atomic = AtomicProfileService(four, SqliteAtomicProfileRepository(self.database))
        self.automatic = AutomaticProfileService(
            four_dimension_service=four,
            repository=SqliteAutomaticProfileRepository(self.database),
            extractor=RuleBasedAutomaticProfileExtractor(),
            atomic_profile_service=self.atomic,
        )


def _write_output(lines: list[str]) -> Path | None:
    if "--out" not in sys.argv:
        return None
    target = Path(sys.argv[sys.argv.index("--out") + 1])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    lines: list[str] = []

    def emit(message: str) -> None:
        print(message)
        lines.append(message)

    tmp = Path(tempfile.mkdtemp(prefix="issue03-demo-"))
    path = tmp / "open-memory.db"
    stack = _Stack(path)

    emit(f"[1] 记住：聊天说出「记住，我养了一只猫叫团子」（库 {path}）")
    result = stack.automatic.preprocess_message(
        ALICE,
        conversation_id="conversation-demo",
        message_id="message-demo",
        content="记住，我养了一只猫叫团子",
        run_id="run-demo",
        mode="companion",
    )
    emit(f"    outcome={result.run.outcome.value}")
    emit(f"    items={_dump_items(stack.database, ALICE)}")
    emit(f"    counts={_counts(stack.database, ALICE)}")

    item = stack.atomic.list_items(ALICE)[0]
    emit("[2] 页面编辑为「我养了一只猫叫圆子」")
    edited = stack.atomic.modify_item(
        ALICE,
        item.profile_item_id,
        AtomicProfileItemModifyRequest(text="我养了一只猫叫圆子", version=item.version),
    )
    emit(f"    version={edited.version} user_edited={edited.user_edited_at is not None}")
    emit(f"    items={_dump_items(stack.database, ALICE)}")

    emit("[3] 下一轮相关提问采用修改后的内容（query=圆子）")
    slice_ = stack.atomic.compile_adopted_slice(
        ALICE,
        run_id="run-slice",
        purpose=build_purpose(mode="companion", query="圆子"),
    )
    emit(f"    adopted={[entry.fact_text for entry in slice_.adopted_items]}")

    emit("[4] 删除（乐观锁确认）")
    stack.atomic.delete_item(ALICE, edited.profile_item_id, edited.version)
    emit(f"    items={_dump_items(stack.database, ALICE)}")
    slice_after = stack.atomic.compile_adopted_slice(
        ALICE,
        run_id="run-slice-2",
        purpose=build_purpose(mode="companion", query="圆子"),
    )
    emit(f"    adopted_after_delete={[entry.fact_text for entry in slice_after.adopted_items]}")

    emit("[5] 重启后一致性")
    stack.database.close()
    reopened = _Stack(path)
    emit(f"    items={_dump_items(reopened.database, ALICE)}")
    revived = reopened.atomic.commit_fact(
        ALICE,
        ProfileFactSubmission(
            text="我养了一只猫叫圆子",
            confidence=FourDimensionConfidence.MEDIUM,
            evidence_message_id="msg-old-replay",
        ),
    )
    emit(f"    replay_after_delete_writes={revived is not None}")
    emit(f"    isolation bob={_dump_items(reopened.database, BOB)}")
    emit(f"    counts={_counts(reopened.database, ALICE)}")

    version = reopened.database.schema_version
    emit(f"[6] schema_version={version}")
    reopened.database.close()
    _write_output(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
