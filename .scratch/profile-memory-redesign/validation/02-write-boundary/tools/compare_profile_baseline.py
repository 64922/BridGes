"""Issue 02：比较重构前后（main vs 分支）确定性基线旅程的可见结果。

用法（仓库根目录、conda ``agent`` 环境）：

    python validation/02-write-boundary/tools/compare_profile_baseline.py \
        "<main 证据 glob>" "<分支证据 glob>"

归一化只剔除运行间必然不同的字段——运行时间、随机标识（条目/记录/消息/
运行/目录标识）、创建与更新时间戳、并按内容排序同刻写入的列表；其余字段
（助手回复正文、画像快照内容与状态、评估 checks、邻居读取探测、模型调用
计数）必须逐字段一致，否则按 journey 输出差异摘要并以退出码 1 结束。

字段级差异定位见 ``diff_profile_baseline_fields.py``。
"""

from __future__ import annotations

import glob
import json
import re
import sys

_DROP_TOP = {"generated_at", "run_dir", "run_lock"}
_DROP_TOTALS = {"generation_duration_ms_samples", "extraction_duration_ms_samples"}
_DROP_ANYWHERE = {
    "assistant_message_id",
    "user_message_id",
    "message_id",
    "created_at",
    "updated_at",
    "compiled_at",
    "run_id",
    "slice_id",
    "owner_account_id",
    "duration_ms",
    "elapsed_ms",
    "conversation_id",
    "record_id",
    "profile_item_id",
    "first_stable_recorded_at",
    "user_edited_at",
    "evidence_message_id",
    "source_message_ids",
    "identity_key",
    "observation_id",
    "task_id",
    "revocation_version",
}
_RANDOM_ID = re.compile(
    r"(auto|profile-extract|profile-observation|profile-retry|msg|run|slice|item)"
    r"-[A-Za-z0-9_-]{16,}"
)


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in _DROP_ANYWHERE}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return _RANDOM_ID.sub(lambda m: f"{m.group(1)}-<hash>", value)
    return value


def normalize(path: str) -> dict:
    data = json.load(open(path, encoding="utf-8"))
    out = {k: v for k, v in data.items() if k not in _DROP_TOP}
    if isinstance(out.get("totals"), dict):
        for key in _DROP_TOTALS:
            out["totals"].pop(key, None)
    out = _scrub(out)
    for journey in out.get("journeys", []):
        snapshot = journey.get("profile_snapshot")
        if isinstance(snapshot, dict):
            for group in (
                "records",
                "items",
                "observations",
                "tasks",
                "extraction_runs",
            ):
                rows = snapshot.get(group)
                if isinstance(rows, list):
                    rows.sort(
                        key=lambda entry: json.dumps(
                            entry, ensure_ascii=False, sort_keys=True
                        )
                    )
    return out


def main() -> int:
    main_path = glob.glob(sys.argv[1])[0]
    branch_path = glob.glob(sys.argv[2])[0]
    main = normalize(main_path)
    branch = normalize(branch_path)
    print("totals main  :", main.get("totals"))
    print("totals branch:", branch.get("totals"))
    same = json.dumps(main, ensure_ascii=False, sort_keys=True) == json.dumps(
        branch, ensure_ascii=False, sort_keys=True
    )
    print("normalized equal:", same)
    if not same:
        for left, right in zip(main["journeys"], branch["journeys"]):
            a = json.dumps(left, ensure_ascii=False, sort_keys=True)
            b = json.dumps(right, ensure_ascii=False, sort_keys=True)
            if a != b:
                print("differs:", left["journey_id"])
                for label, item in (("main", left), ("brn ", right)):
                    snap = item.get("profile_snapshot", {})
                    print(
                        f"  {label} eval={item.get('evaluation')} "
                        f"records={len(snap.get('records', []))} "
                        f"items={len(snap.get('items', []))} "
                        f"probe={item.get('neighbor_reader_probe')}"
                    )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
