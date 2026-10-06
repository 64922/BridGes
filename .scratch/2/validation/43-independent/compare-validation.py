"""比较两树验证身份；行号变化不算新增错误，保留数量和实际失败列表。"""
import json
import re
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
MAIN = ROOT  # 主线原始报告已按字节归档，同目录比较可在清理工作树后复现。


def junit(*paths):
    by_id = {}
    for path in paths:
        for case in ET.parse(path).getroot().iter('testcase'):
            by_id[f"{case.get('classname')}::{case.get('name')}"] = case
    cases = list(by_id.values())
    failures = {
        f"{case.get('classname')}::{case.get('name')}"
        for case in cases if case.find('failure') is not None or case.find('error') is not None
    }
    return failures, {
        'tests': len(cases), 'failed_or_error': len(failures),
        'skipped': sum(case.find('skipped') is not None for case in cases),
        'passed': sum(case.find('failure') is None and case.find('error') is None
                      and case.find('skipped') is None for case in cases),
    }


def errors(path):
    lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
    return Counter(re.sub(r':\d+(?::\d+)?(?=: error:|\): error)', '', line)
                   for line in lines if ': error:' in line or '): error ' in line)


def ruff(path):
    return Counter((item['filename'].replace('\\', '/').split('/src/', 1)[-1]
                    if '/src/' in item['filename'].replace('\\', '/')
                    else item['filename'].replace('\\', '/').split('/tests/', 1)[-1]
                    if '/tests/' in item['filename'].replace('\\', '/')
                    else item['filename'].replace('\\', '/').split('/scripts/', 1)[-1],
                    item['code'], item['message'])
                   for item in json.loads(path.read_text(encoding='utf-8-sig')))


def web_errors(path):
    return Counter((line.split('(', 1)[0], re.search(r'error (TS\d+)', line).group(1))
                   for line in path.read_text(encoding='utf-8-sig').splitlines()
                   if re.search(r'error TS\d+', line))


branch_fail, branch_stats = junit(ROOT / 'branch-full.xml', ROOT / 'branch-runtime.xml',
                                  ROOT / 'smoke-final.xml')
main_fail, main_stats = junit(MAIN / 'main-full.xml', MAIN / 'main-runtime.xml')
result = {
    'main_commit': 'a9dda10af5ebe11fb4c7ba33fd74ab0cb8d2bf8a',
    'implementation_commit': 'fe432411266469e0f1e5fb5a527231c00024a5fd',
    'environment': 'conda agent; Python 3.11; Windows; per-tree PYTHONPATH/TEMP/TMP; pytest -n 6',
    'coverage': '全部测试；12项runtime单独串行以隔离生产构建，按节点身份覆盖合并统计',
    'overrides': '三个旧start凭据测试错误使用桌面默认入口；原全量对应2子进程手动收敛，修正显式development后完整smoke12项替换统计，原始结果保留',
    'branch': branch_stats, 'main': main_stats,
    'only_on_branch': sorted(branch_fail - main_fail),
    'only_on_main': sorted(main_fail - branch_fail),
    'shared_failures': sorted(branch_fail & main_fail),
}
for name, branch_path, main_path, parse in (
    ('ruff', ROOT / 'ruff.json', MAIN / 'main-ruff.json', ruff),
    ('mypy', ROOT / 'mypy.txt', MAIN / 'main-mypy.txt', errors),
    ('web_typecheck', ROOT / 'web-typecheck.txt', MAIN / 'main-web-typecheck.txt', web_errors),
):
    branch, main = parse(branch_path), parse(main_path)
    result[name] = {
        'branch_count': sum(branch.values()), 'main_count': sum(main.values()),
        'new_errors': list((branch - main).elements()),
        'resolved_errors': list((main - branch).elements()),
    }
(ROOT / 'comparison.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8'
)
print(json.dumps({key: value for key, value in result.items()
                  if key != 'shared_failures'}, ensure_ascii=False, indent=2))
