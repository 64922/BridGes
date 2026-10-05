"""验收日志脱敏：使用仓库规则，只记录文件名和替换数。"""

import xml.etree.ElementTree as ET
from pathlib import Path

from tests.security.test_secret_scan import _FAKE_VALUE_MARKERS, _SECRET_PATTERNS


def sanitize(text):
    count = 0

    def replace(match):
        nonlocal count
        if any(marker in match.group(0).lower() for marker in _FAKE_VALUE_MARKERS):
            return match.group(0)
        count += 1
        return "<redacted>"

    for _, pattern in _SECRET_PATTERNS:
        text = pattern.sub(replace, text)
    return text, count


for path in Path(__file__).parent.iterdir():
    count = 0
    if path.suffix == ".txt":
        text, count = sanitize(path.read_text(encoding="utf-8"))
        if count:
            path.write_text(text, encoding="utf-8")
    elif path.suffix == ".xml":
        tree = ET.parse(path)
        for element in tree.iter():
            for key, value in list(element.attrib.items()):
                element.attrib[key], replaced = sanitize(value)
                count += replaced
            if element.text:
                element.text, replaced = sanitize(element.text)
                count += replaced
        if count:
            tree.write(path, encoding="utf-8", xml_declaration=True)
    if count:
        print(f"{path.name}: 已脱敏 {count} 处")
