"""生成 Issue 04 E2E 夹具（一次性脚本，产物提交进仓库）。

产出（apps/web/e2e/fixtures/issue04/）：
- Transformer：AI 大模型的核心基石.docx —— 最小合法 DOCX，三段科学正文；
- 巴巴博一.jpg —— 真实 JPEG（魔数可嗅探、尺寸可解析）。
"""

import io
import zipfile
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "apps/web/e2e/fixtures/issue04"

CONTENT_TYPES = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    b'<Default Extension="rels"'
    b' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    b'<Default Extension="xml" ContentType="application/xml"/>\n'
    b'<Override PartName="/word/document.xml"'
    b' ContentType="application/vnd.openxmlformats-officedocument.'
    b'wordprocessingml.document.main+xml"/>\n'
    b"</Types>"
)

RELS = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    b'<Relationship Id="rId1"'
    b' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
    b' Target="word/document.xml"/>\n'
    b"</Relationships>"
)

PARAS = [
    "Transformer 架构自 2017 年提出以来，已成为大语言模型的核心基石。",
    "其核心是自注意力机制：每个位置的表示由序列中所有位置加权求和得到。",
    "多头注意力把输入投影到多组子空间，允许模型同时关注不同的语义关系。",
]

DOC_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    + "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in PARAS)
    + "</w:body></w:document>"
).encode("utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/document.xml", DOC_XML)
    (OUT / "Transformer：AI 大模型的核心基石.docx").write_bytes(buf.getvalue())

    image = Image.new("RGB", (64, 48), (186, 12, 47))
    image.save(OUT / "巴巴博一.jpg", "JPEG", quality=85)

    for path in sorted(OUT.iterdir()):
        data = path.read_bytes()
        print(repr(path.name), len(data), "bytes")


if __name__ == "__main__":
    main()
