#!/usr/bin/env bash
# 收尾发布验收：三轮 Windows 串行套件（issue 11）。
#
# 组合运行：
#   pytest 部分 —— tests/closeout（issue 01 反馈环 + issue 11 压力循环）+
#   issue 02/03/04/06/08/09 专项反馈环 + 秘密扫描；
#   e2e 部分 —— 主配置（真实 API 子进程 + 唯一数据目录 + 假邮件服务器，
#   CI=1 串行、无重试），覆盖用户 9 组场景的浏览器纵向切片。
#
# 用法：在仓库根目录用 Bash 运行
#   bash scripts/closeout_acceptance.sh [轮数，默认 3]
#
# 任一轮失败立即退出非零；重新从第 1 轮计数（issue 11 反馈环约定）。
# 每轮使用唯一 e2e 数据目录，轮间清理；结束后输出轮次汇总。

set -u
ROUNDS="${1:-3}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO_ROOT/.venv/Scripts/python.exe"
LOG_DIR="$REPO_ROOT/.tmp/closeout-rounds"
mkdir -p "$LOG_DIR"

PYTEST_TARGETS=(
  tests/closeout
  tests/chat/test_issue02_durable_generation.py
  tests/chat/test_issue03_atomic_first_turn.py
  tests/chat/test_issue04_atomic_binding.py
  tests/chat/test_issue04_attachment_contract.py
  tests/chat/test_issue06_budget.py
  tests/chat/test_issue06_latency_budget.py
  tests/chat/test_issue08_conversational_learning.py
  tests/chat/test_issue09_career_resilience.py
  tests/humanizer
  tests/reminder/test_verification_state_machine.py
  tests/security/test_secret_scan.py
)

E2E_SPECS=(
  e2e/closeout-smoke.spec.ts
  e2e/issue03-atomic-first-turn.spec.ts
  e2e/issue04-atomic-attachment.spec.ts
  e2e/issue06-latency-stages.spec.ts
  e2e/issue07-humanizer-delivery.spec.ts
  e2e/issue08-conversational-learning.spec.ts
  e2e/issue09-career-planner-resilience.spec.ts
  e2e/issue22-arxiv-paper-search.spec.ts
  e2e/issue33-smtp-reminders.spec.ts
)

declare -A ROUND_RESULTS

for round in $(seq 1 "$ROUNDS"); do
  echo "==================== 第 ${round} 轮 / ${ROUNDS} ===================="
  echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"

  # --- pytest 部分 ---
  echo "--- [${round}] pytest 收尾组合套件 ---"
  PYTEST_LOG="$LOG_DIR/round${round}-pytest.log"
  "$PYTHON" -m pytest "${PYTEST_TARGETS[@]}" -q > "$PYTEST_LOG" 2>&1
  PYTEST_CODE=$?
  tail -3 "$PYTEST_LOG"

  # --- e2e 部分（唯一数据目录） ---
  E2E_DATA="$REPO_ROOT/.tmp/e2e-round-${round}"
  rm -rf "$E2E_DATA"
  echo "--- [${round}] Playwright 浏览器纵向切片（数据目录 $E2E_DATA） ---"
  E2E_LOG="$LOG_DIR/round${round}-e2e.log"
  (
    cd "$REPO_ROOT/apps/web"
    CI=1 E2E_DATA_DIR="$E2E_DATA" npx playwright test \
      -c playwright.config.ts --retries=0 "${E2E_SPECS[@]}"
  ) > "$E2E_LOG" 2>&1
  E2E_CODE=$?
  tail -3 "$E2E_LOG"

  ROUND_RESULTS[$round]="$PYTEST_CODE $E2E_CODE"
  echo "第 ${round} 轮结果：pytest=${PYTEST_CODE} e2e=${E2E_CODE} 结束时间: $(date '+%H:%M:%S')"

  # 轮间清理：e2e 数据目录与 pytest 临时目录（成功与失败路径都执行，
  # 保证失败轮不残留数据锁；进程残留由系统退出释放，端口由 Playwright
  # 进程树管理）
  rm -rf "$E2E_DATA" "$REPO_ROOT/.tmp/pytest-basetemp"

  if [ "$PYTEST_CODE" -ne 0 ] || [ "$E2E_CODE" -ne 0 ]; then
    echo "第 ${round} 轮失败：pytest=${PYTEST_CODE} e2e=${E2E_CODE}"
    echo "日志：$PYTEST_LOG / $E2E_LOG"
    echo "按 issue 11 反馈环约定：修复后重新从第 1 轮计数。"
    exit 1
  fi
done

echo "==================== ${ROUNDS} 轮串行全部通过 ===================="
for round in $(seq 1 "$ROUNDS"); do
  echo "第 ${round} 轮: ${ROUND_RESULTS[$round]}"
done

# 验收标准：无残留进程/端口/数据锁（机械检查）。
# 套件自身不应留下 python/pytest/playwright 进程与监听端口。
echo "--- 残留检查 ---"
RESIDUAL=""
if command -v tasklist > /dev/null 2>&1; then
  RUNNING=$(tasklist 2>/dev/null | grep -ciE "python|node" || true)
  if [ "${RUNNING:-0}" -gt 0 ]; then
    # python/node 可能包含用户其他进程，仅提示不判失败
    echo "notice: 存在 python/node 进程 ${RUNNING} 个（可能含用户其他进程）"
  fi
fi
if [ -d "$REPO_ROOT/.tmp" ]; then
  LEFT=$(find "$REPO_ROOT/.tmp" -maxdepth 1 -name "e2e-round-*" -o -maxdepth 1 -name "pytest-basetemp" 2>/dev/null | wc -l)
  if [ "${LEFT:-0}" -gt 0 ]; then
    RESIDUAL="残留数据目录 ${LEFT} 个"
  fi
fi
if [ -n "$RESIDUAL" ]; then
  echo "警告: $RESIDUAL"
  exit 1
fi
echo "残留检查：通过（无套件数据目录/临时目录残留）"
exit 0
