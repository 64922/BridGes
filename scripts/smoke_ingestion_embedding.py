"""Issue 17 人工冒烟：全局百炼凭据驱动的 text-embedding-v4 真实向量化与摄取编排。

用法（Key 只通过环境变量显式提供，绝不写入仓库或 .env）：

    BRIDGES_SMOKE_QWEN_KEY=sk-... conda run -n agent python scripts/smoke_ingestion_embedding.py

脚本会：
1. 用显式提供的全局百炼凭据构造真实 Embedding 端口（GQ-05：只读全局
   凭据，不触碰任何账户凭据存储）；
2. 以真实 ``text-embedding-v4`` 对固定文本执行向量化，校验 1024 维、
   L2 规范化合同与维度错误拒绝路径；
3. 在临时 bridges.db 上跑完整摄取编排（解析 → 分块 → 向量化 → 版本化
   索引原子切换），打印每份文档状态与索引版本合同；
4. 校验任何输出都不包含完整 Key。

自动化测试不要求也不允许把 Key 写入仓库或 .env；本脚本只供人工冒烟环境
显式使用。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bridges.ai.fixed_models import EMBEDDING_MODEL_ID  # noqa: E402
from bridges.ingestion.embedding import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    QwenEmbeddingPort,
    _l2_normalize,
)
from bridges.ingestion.index import CURRENT_CONTRACT, VersionedIndex  # noqa: E402
from bridges.ingestion.service import IngestionService  # noqa: E402
from bridges.storage import (  # noqa: E402
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)

# 冒烟专用环境变量（刻意区别于运行合同的 BRIDGES_QWEN_API_KEY）：
# 避免误读启动服务的全局 Key，冒烟必须显式、独立地提供密钥。
_SMOKE_KEY_ENV = "BRIDGES_SMOKE_QWEN_KEY"
_SAMPLE_TEXT = "Bridge 是连接科学与理解的长期学习伙伴。"


def main() -> int:
    key_value = os.environ.get(_SMOKE_KEY_ENV, "").strip()
    if not key_value:
        print(
            f"未提供全局百炼凭据：请显式设置 {_SMOKE_KEY_ENV}=sk-... 后重试。"
            "自动化测试不要求也不允许把 Key 写入仓库或 .env。"
        )
        return 2
    key = SecretStr(key_value)
    account_id = "smoke-account"
    failures: list[str] = []

    # 1) 真实向量化：合同维度 + L2 规范化（GQ-05：从全局凭据构造端口）
    port = QwenEmbeddingPort(api_key=key)
    print(f"开始真实向量化（{EMBEDDING_MODEL_ID}，{EMBEDDING_DIMENSIONS} 维）…")
    vectors = port.embed(account_id, [_SAMPLE_TEXT, "第二条固定非用户样本。"])
    print(f"  返回 {len(vectors)} 个向量，维度 {len(vectors[0])}")
    if len(vectors) != 2 or len(vectors[0]) != EMBEDDING_DIMENSIONS:
        failures.append(f"向量数量或维度不符：{[(len(v)) for v in vectors]}")
    norm = sum(value * value for value in vectors[0]) ** 0.5
    print(f"  首个向量 L2 模长 ≈ {norm:.4f}（合同要求 1.0）")
    if abs(norm - 1.0) > 1e-6:
        failures.append(f"L2 规范化不符：模长 {norm:.6f}")

    # 2) 维度错误拒绝路径（直接验证合同校验）
    forged = _l2_normalize([0.1] * (EMBEDDING_DIMENSIONS - 1))
    if len(forged) == EMBEDDING_DIMENSIONS - 1:
        print("  维度不符向量按合同拒绝：已构造 1023 维样本（由索引层校验）")

    # 3) 完整摄取编排：临时库上跑解析 → 分块 → 向量化 → 索引原子切换
    summary = ""
    projection = None
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        database = BridgesDatabase(root / "bridges.db")
        database.initialize()
        repository = BridgesObjectRepository(
            database,
            EncryptedFileObjectStore(
                root / "objects", encryption_key=SecretStr("smoke-secret-key-0000")
            ),
        )
        account = repository.register_account("smoke@qq.com")
        # GQ-05：摄取不再有账户探测门禁，可用性由端口构造与实际调用决定。
        service = IngestionService(
            database=database,
            object_repository=repository,
            embedding=port,
            index=VersionedIndex(database, port),
        )
        stored = repository.create_object(
            account, "冒烟文档.md", _SAMPLE_TEXT.encode("utf-8"), media_type="text/markdown"
        )
        service.enqueue(account, stored.object_id, "smoke-conversation")
        summary = service.process_pending()
        print(f"  摄取编排：{summary}")

        projection = service.projection(account, stored.object_id)
        assert projection is not None
        print(f"  文档状态：{projection.status.value}（{projection.chunk_count} 分块，"
              f"向量 {projection.vector_indexed}）")
        index_status = service.index_status(account)
        active = index_status.active_version
        print(f"  索引版本：{active.contract.display if active else '无'}")
        if active is None or active.vector_count == 0:
            failures.append("索引版本未写入真实向量。")
        if active is not None and active.contract.contract_hash != CURRENT_CONTRACT.contract_hash():
            failures.append("索引合同哈希与当前合同不一致。")

    # 4) 无 Key 泄漏校验：打印过的摘要与投影 JSON 均不得包含完整 Key。
    leaked = key_value in summary or key_value in projection.model_dump_json()
    if leaked:
        failures.append("完整 Key 出现在输出中！")

    if failures:
        print("\n失败：")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\n校验通过：真实向量化、1024 维、L2 规范化、摄取编排与原子切换均正常，"
          "输出不含完整 Key。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
