"""``github.inspect``：取 API 元数据、README、许可与**实际读取到的文件**。

证据分三级（``contracts.GithubEvidenceKind``）：元数据 < README 自述 < 实际读取
的实现文件。节点的职责就是尽量把证据抬到第三级，抬不上去时如实标出停在哪一级：

- README 缺失、超限或未取得都是**真实结果**，不当作失败；
- 许可优先看仓库根目录里真实存在的许可文件，并读出正文片段；元数据字段只作
  补充——没有读到许可文件时不声称代码可自由复用；
- README 里点名的路径拿根目录清单一核对：顶层路径直接由清单一判定（存在／
  不存在都不额外发请求），嵌套路径才真的去读一次目录或文件，读到的片段就是
  「实现文件证据」。未读取的实现细节一律不断言。

上游额度用尽是常态：撞上即停止继续读取并标记 ``rate_limited``，把已取得的
证据如实带回，由 ``github.present`` 展示实际结果与缺口。
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.github.client import GithubApiClient, GithubResponse
from bridges.github.contracts import (
    GithubFileRead,
    GithubImplementationCheck,
    GithubLicenseCheck,
    GithubReadmeStatus,
    GithubRepositoryCandidate,
    GithubRepositoryEvidence,
)
from bridges.github.lexicon import INSPECT_SOURCE
from bridges.github.searching import query_record

#: 单轮最多检查的候选仓库数（默认推荐 2–3 个，检查有界）。
INSPECT_LIMIT = 3

#: 每个仓库最多为「嵌套路径」额外读取的文件／目录数（有界外发）。
IMPLEMENTATION_READ_LIMIT = 2

#: README 取得长度上限与投影片段上限（超长只截断，不丢状态）。
README_MAX_CHARS = 6000
README_EXCERPT_CHARS = 1200

#: 单个文件读到的正文片段上限。
FILE_EXCERPT_CHARS = 400

#: 许可名称为空时从正文首行取的标题（扫描范围与长度都有界）。
LICENSE_TITLE_SCAN_CHARS = 300
LICENSE_TITLE_CHARS = 60

#: 目录清单保留的直接子项数上限（条目总数仍如实统计）。
DIR_ENTRY_LIMIT = 30

#: 根目录里被认作「可运行线索」的清单文件名（只认实际存在的条目）。
MANIFEST_FILES: frozenset[str] = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "go.mod",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
        "gemfile",
        "pubspec.yaml",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "makefile",
        "cmakelists.txt",
    }
)

#: 许可文件名形态（顶层条目匹配即认为仓库里有许可文件）。
_LICENSE_NAME = re.compile(
    r"^(licen[cs]e|copying|notice)(\.(md|txt|rst|html))?$", re.IGNORECASE
)

#: 明显不像仓库内目录的顶层名（对「缺失」判定要求更严格，宁可不下结论）。
_DIR_LIKE = frozenset(
    {
        "src",
        "source",
        "app",
        "apps",
        "lib",
        "libs",
        "core",
        "server",
        "client",
        "web",
        "mobile",
        "api",
        "backend",
        "frontend",
        "packages",
        "package",
        "modules",
        "module",
        "services",
        "service",
        "components",
        "handlers",
        "routes",
        "models",
        "views",
        "controllers",
        "utils",
        "helpers",
        "scripts",
        "tools",
        "cmd",
        "pkg",
        "internal",
        "tests",
        "test",
        "docs",
        "public",
        "static",
        "assets",
        "config",
        "docker",
        "deploy",
        "migrations",
        "database",
        "db",
    }
)

#: README 里像仓库内路径的 token（含斜杠的路径，或带源码/清单扩展名的文件名）。
_PATH_TOKEN = re.compile(
    r"(?<![\w/.-])("
    r"[\w.\-]+(?:/[\w.\-]+)+"
    r"|[\w.\-]+\.(?:py|js|jsx|ts|tsx|go|rs|java|kt|kts|rb|php|cs|cpp|c|h|sh|sql|vue|svelte"
    r"|json|toml|yml|yaml|cfg|ini|txt|md)"
    r")(?![\w/])"
)

#: README 里带源码扩展名的文件名（用于判定「缺失」，比目录名更可靠）。
_CODE_EXT = re.compile(
    r"\.(?:py|js|jsx|ts|tsx|go|rs|java|kt|kts|rb|php|cs|cpp|c|h|sh|sql|vue|svelte"
    r"|json|toml|yml|yaml)(?:$|\?)",
    re.IGNORECASE,
)

#: 明显不是仓库内路径的 token（域名、徽章、外部链接与依赖目录）。
_NOT_A_PATH = re.compile(
    r"^(?:https?|ftp|mailto|www)$|"
    r"\.(?:com|org|net|io|cn|dev|app)$|"
    r"(?:^|/)(?:node_modules|\.git|\.github)/",
    re.IGNORECASE,
)

@dataclass(frozen=True)
class InspectionOutcome:
    """一次候选检查的真实产出：证据、统一查询记录与是否撞上额度限制。"""

    evidence: list[GithubRepositoryEvidence] = field(default_factory=list)
    records: list[ModuleQueryRecord] = field(default_factory=list)
    rate_limited: bool = False


class GithubRepositoryReader:
    """候选仓库证据读取器（真实 HTTP；测试用 ``httpx.MockTransport`` 注入）。"""

    def __init__(
        self,
        client: GithubApiClient,
        *,
        implementation_read_limit: int = IMPLEMENTATION_READ_LIMIT,
        readme_max_chars: int = README_MAX_CHARS,
    ) -> None:
        self._client = client
        self._implementation_read_limit = max(0, implementation_read_limit)
        self._readme_max_chars = max(200, readme_max_chars)

    def inspect_candidates(
        self,
        account_id: str,
        candidates: list[GithubRepositoryCandidate],
        *,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> InspectionOutcome:
        """按检索顺序逐个读取候选；额度用尽即停止并如实记账。"""
        evidence: list[GithubRepositoryEvidence] = []
        records: list[ModuleQueryRecord] = []
        limited = False
        for candidate in candidates[:INSPECT_LIMIT]:
            if stop_event is not None and stop_event.is_set():
                break
            if deadline is not None and time.monotonic() >= deadline:
                records.append(
                    query_record(
                        source=INSPECT_SOURCE,
                        query=candidate.full_name,
                        status=ModuleQueryStatus.SKIPPED,
                        evidence_count=0,
                        detail="本轮读取达到时间预算，剩余候选未取证据。",
                    )
                )
                break
            result, repo_records, repo_limited = self.inspect(
                account_id,
                candidate,
                stop_event=stop_event,
                deadline=deadline,
            )
            records.extend(repo_records)
            if result is not None:
                evidence.append(result)
            if repo_limited:
                limited = True
                break
        return InspectionOutcome(evidence=evidence, records=records, rate_limited=limited)

    def inspect(
        self,
        account_id: str,
        candidate: GithubRepositoryCandidate,
        *,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> tuple[GithubRepositoryEvidence | None, list[ModuleQueryRecord], bool]:
        """读取一个仓库的 README、许可与实现文件证据。"""
        del stop_event  # 逐个 HTTP 调用已经是可取消边界（deadline 与额度即停止条件）
        records: list[ModuleQueryRecord] = []
        retrieved_at = datetime.now(UTC)
        readme_status, readme_url, readme_text, readme_record = self._read_readme(
            account_id, candidate
        )
        records.append(readme_record)
        if readme_record.status is ModuleQueryStatus.RATE_LIMITED:
            return None, records, True
        root_entries, root_record, root_limited = self._read_root(account_id, candidate)
        records.append(root_record)
        if root_limited:
            return None, records, True
        license_check, license_records, license_limited = self._read_license(
            account_id, candidate, root_entries
        )
        records.extend(license_records)
        if license_limited:
            return None, records, True
        checks, files_read, impl_records, impl_limited = self._read_implementation(
            account_id,
            candidate,
            readme_text=readme_text,
            root_entries=root_entries,
            deadline=deadline,
        )
        records.extend(impl_records)
        if impl_limited:
            return None, records, True
        return (
            GithubRepositoryEvidence(
                full_name=candidate.full_name,
                html_url=candidate.html_url,
                description=candidate.description,
                topics=list(candidate.topics),
                language=candidate.language,
                stars=candidate.stars,
                forks=candidate.forks,
                open_issues=candidate.open_issues,
                pushed_at=candidate.pushed_at,
                created_at=candidate.created_at,
                archived=candidate.archived,
                is_fork=candidate.is_fork,
                default_branch=candidate.default_branch,
                license=license_check,
                readme_status=readme_status,
                readme_url=readme_url,
                readme_text=readme_text,
                files_read=files_read,
                implementation_checks=checks,
                runnable_hints=_runnable_hints(root_entries),
                rate_limited=False,
                matched_query=candidate.matched_query,
                retrieved_at=retrieved_at,
            ),
            records,
            False,
        )

    # -- README ----------------------------------------------------------

    def _read_readme(
        self,
        account_id: str,
        candidate: GithubRepositoryCandidate,
    ) -> tuple[GithubReadmeStatus, str | None, str | None, ModuleQueryRecord]:
        response = self._client.get(
            f"/repos/{candidate.full_name}/readme",
            account_id=account_id,
            reason="GitHub 项目推荐：读取仓库 README 自述",
        )
        if response.ok and isinstance(response.payload, dict):
            text = _decode_content(response.payload)
            url = _clean(response.payload.get("html_url"))
            if text is None:
                return (
                    GithubReadmeStatus.ERROR,
                    url,
                    None,
                    _record(candidate, response, "README 内容无法解码", evidence=0),
                )
            truncated = text[: self._readme_max_chars]
            return (
                GithubReadmeStatus.READ,
                url,
                truncated,
                _record(candidate, response, None, evidence=1),
            )
        if response.status_code == 404:
            return (
                GithubReadmeStatus.NOT_FOUND,
                None,
                None,
                _record(
                    candidate, response, "仓库没有 README", evidence=0, absent_ok=True
                ),
            )
        if response.error_code == "github_too_large":
            return (
                GithubReadmeStatus.TOO_LARGE,
                None,
                None,
                _record(candidate, response, "README 超出接口单文件上限", evidence=0),
            )
        return (
            GithubReadmeStatus.NOT_FETCHED if response.rate_limited else GithubReadmeStatus.ERROR,
            None,
            None,
            _record(candidate, response, None, evidence=0),
        )

    # -- 根目录 ----------------------------------------------------------

    def _read_root(
        self,
        account_id: str,
        candidate: GithubRepositoryCandidate,
    ) -> tuple[list[dict[str, object]], ModuleQueryRecord, bool]:
        response = self._client.get(
            f"/repos/{candidate.full_name}/contents",
            account_id=account_id,
            reason="GitHub 项目推荐：读取仓库根目录清单",
        )
        if response.ok and isinstance(response.payload, list):
            entries = [item for item in response.payload if isinstance(item, dict)]
            return (
                entries,
                _record(candidate, response, None, evidence=len(entries)),
                False,
            )
        return (
            [],
            _record(candidate, response, None, evidence=0),
            response.rate_limited,
        )

    # -- 许可 ------------------------------------------------------------

    def _read_license(
        self,
        account_id: str,
        candidate: GithubRepositoryCandidate,
        root_entries: list[dict[str, object]],
    ) -> tuple[GithubLicenseCheck, list[ModuleQueryRecord], bool]:
        path = _license_path(root_entries)
        spdx = candidate.license_spdx_id
        name = candidate.license_name
        if path is None:
            if spdx or name:
                return (
                    GithubLicenseCheck(
                        detected=True,
                        spdx_id=spdx,
                        name=name,
                        path=None,
                        license_url=_license_url(root_entries),
                        file_read=False,
                        note=(
                            f"上游元数据标注许可为「{spdx or name}」，但根目录里没有读到许可文件；"
                            "本轮按元数据如实标注，未核对许可正文。"
                        ),
                    ),
                    [],
                    False,
                )
            return (
                GithubLicenseCheck(
                    detected=False,
                    spdx_id=None,
                    name=None,
                    path=None,
                    license_url=None,
                    file_read=False,
                    note="仓库元数据未标注许可，根目录也没有许可文件：本轮未见许可证，不声称代码可自由复用。",
                ),
                [],
                False,
            )
        response = self._client.get(
            f"/repos/{candidate.full_name}/contents/{path}",
            account_id=account_id,
            reason="GitHub 项目推荐：读取仓库许可文件",
        )
        if response.ok and isinstance(response.payload, dict):
            text = _decode_content(response.payload)
            # 元数据没给许可名时，用**许可文件正文自身**的首行标题如实补上：
            # 报的是文件里写着什么，不是我们推断出来的结论。
            if not spdx and not name and text:
                name = _license_title(text)
            return (
                GithubLicenseCheck(
                    detected=True,
                    spdx_id=spdx,
                    name=name,
                    path=path,
                    license_url=response.payload.get("html_url")
                    if isinstance(response.payload.get("html_url"), str)
                    else None,
                    file_read=text is not None,
                    excerpt=(text[:FILE_EXCERPT_CHARS] if text else None),
                    note=(
                        f"已读取仓库中的许可文件 {path}"
                        + (f"（元数据标注：{spdx or name}）" if (spdx or name) else "")
                        + "；许可正文片段见 excerpt。"
                        if text is not None
                        else f"仓库中存在许可文件 {path}，但正文未能解码。"
                    ),
                ),
                [_record(candidate, response, None, evidence=1)],
                False,
            )
        if response.rate_limited:
            return (
                _unread_license(spdx, name, path),
                [_record(candidate, response, None, evidence=0)],
                True,
            )
        return (
            _unread_license(spdx, name, path),
            [_record(candidate, response, None, evidence=0)],
            False,
        )

    # -- README 点名路径的实际核对 ---------------------------------------

    def _read_implementation(
        self,
        account_id: str,
        candidate: GithubRepositoryCandidate,
        *,
        readme_text: str | None,
        root_entries: list[dict[str, object]],
        deadline: float | None,
    ) -> tuple[
        list[GithubImplementationCheck], list[GithubFileRead], list[ModuleQueryRecord], bool
    ]:
        names = {
            str(entry.get("name")) for entry in root_entries if entry.get("name") is not None
        }
        checks: list[GithubImplementationCheck] = []
        files_read: list[GithubFileRead] = []
        records: list[ModuleQueryRecord] = []
        nested: list[str] = []
        for path in _readme_paths(readme_text, names):
            top, _, rest = path.partition("/")
            if top not in names:
                checks.append(
                    GithubImplementationCheck(
                        claim=f"README 提到「{path}」",
                        path=path,
                        status="missing",
                        evidence=f"已读取根目录清单，其中没有「{top}」这一项。",
                    )
                )
                continue
            if not rest:
                checks.append(
                    GithubImplementationCheck(
                        claim=f"README 提到「{path}」",
                        path=path,
                        status="confirmed",
                        evidence="根目录清单里实际存在这一项。",
                    )
                )
                continue
            nested.append(path)
        for path in nested[: self._implementation_read_limit]:
            if deadline is not None and time.monotonic() >= deadline:
                checks.append(
                    GithubImplementationCheck(
                        claim=f"README 提到「{path}」",
                        path=path,
                        status="unread",
                        evidence="本轮读取达到时间预算，没有读取该路径。",
                    )
                )
                continue
            response = self._client.get(
                f"/repos/{candidate.full_name}/contents/{path}",
                account_id=account_id,
                reason="GitHub 项目推荐：核对 README 点名的实现路径",
            )
            if response.ok and isinstance(response.payload, list):
                entries = [item for item in response.payload if isinstance(item, dict)]
                entry_names = [
                    str(item.get("name")) for item in entries if item.get("name") is not None
                ]
                files_read.append(
                    GithubFileRead(
                        path=path,
                        kind="dir",
                        entries=entry_names[:DIR_ENTRY_LIMIT],
                    )
                )
                checks.append(
                    GithubImplementationCheck(
                        claim=f"README 提到「{path}」",
                        path=path,
                        status="confirmed",
                        evidence=f"实际读取到该目录，包含 {len(entries)} 个直接子项。",
                    )
                )
                records.append(_record(candidate, response, None, evidence=1))
                continue
            if response.ok and isinstance(response.payload, dict):
                text = _decode_content(response.payload)
                files_read.append(
                    GithubFileRead(
                        path=path,
                        kind="file",
                        size=_as_int(response.payload.get("size")),
                        sha=_clean(response.payload.get("sha")),
                        excerpt=(text[:FILE_EXCERPT_CHARS] if text else None),
                    )
                )
                checks.append(
                    GithubImplementationCheck(
                        claim=f"README 提到「{path}」",
                        path=path,
                        status="confirmed",
                        evidence="实际读取到该文件的内容片段。",
                    )
                )
                records.append(_record(candidate, response, None, evidence=1))
                continue
            if response.status_code == 404:
                checks.append(
                    GithubImplementationCheck(
                        claim=f"README 提到「{path}」",
                        path=path,
                        status="missing",
                        evidence="按该路径读取时上游返回不存在。",
                    )
                )
                records.append(_record(candidate, response, None, evidence=0))
                continue
            if response.rate_limited:
                checks.append(
                    GithubImplementationCheck(
                        claim=f"README 提到「{path}」",
                        path=path,
                        status="unread",
                        evidence="上游额度已用尽，没有读取该路径。",
                    )
                )
                records.append(_record(candidate, response, None, evidence=0))
                return checks, files_read, records, True
            checks.append(
                GithubImplementationCheck(
                    claim=f"README 提到「{path}」",
                    path=path,
                    status="unread",
                    evidence="读取该路径失败，本轮不下结论。",
                )
            )
            records.append(_record(candidate, response, None, evidence=0))
        for path in nested[self._implementation_read_limit :]:
            checks.append(
                GithubImplementationCheck(
                    claim=f"README 提到「{path}」",
                    path=path,
                    status="unread",
                    evidence="本轮读取数量有上限，没有读取该路径。",
                )
            )
        return checks, files_read, records, False


def _unread_license(
    spdx: str | None, name: str | None, path: str
) -> GithubLicenseCheck:
    detail = f"（元数据标注：{spdx or name}）" if (spdx or name) else ""
    return GithubLicenseCheck(
        detected=True,
        spdx_id=spdx,
        name=name,
        path=path,
        license_url=None,
        file_read=False,
        note=f"根目录里有许可文件 {path}{detail}，但本轮未能读取其正文。",
    )


def _record(
    candidate: GithubRepositoryCandidate,
    response: GithubResponse,
    note: str | None,
    *,
    evidence: int,
    absent_ok: bool = False,
) -> ModuleQueryRecord:
    if response.ok:
        return query_record(
            source=INSPECT_SOURCE,
            query=candidate.full_name,
            status=ModuleQueryStatus.SUCCESS,
            evidence_count=evidence,
            retrieved_at=response.requested_at,
            detail=note,
        )
    if absent_ok and response.status_code == 404:
        return query_record(
            source=INSPECT_SOURCE,
            query=candidate.full_name,
            status=ModuleQueryStatus.EMPTY,
            evidence_count=0,
            retrieved_at=response.requested_at,
            detail=note,
        )
    status = ModuleQueryStatus.RATE_LIMITED if response.rate_limited else ModuleQueryStatus.EMPTY
    if response.error_code in {"github_timeout", "github_unavailable"}:
        status = ModuleQueryStatus.ERROR
    return query_record(
        source=INSPECT_SOURCE,
        query=candidate.full_name,
        status=status,
        evidence_count=0,
        retrieved_at=response.requested_at,
        error_code=response.error_code,
        error_message=response.error_message,
        retryable=response.retryable,
        detail=note,
    )


def _readme_paths(text: str | None, root_names: set[str]) -> list[str]:
    """从 README 正文里抽出**值得核对**的仓库内路径。

    只有两类 token 进入核对：顶层名确实存在于根目录的（可确认存在），以及
    带源码扩展名或顶层名像源码目录的（可判定缺失）。其余 token（``and/or``、
    ``CI/CD``、外部域名）一律不下结论——宁可少说，也不把噪声当「文件缺失」。
    """
    if not text:
        return []
    cleaned = re.sub(r"https?://\S+", " ", text)
    found: list[str] = []
    for match in _PATH_TOKEN.finditer(cleaned):
        value = match.group(1).strip().strip(".,;:()[]`'\"")
        if not value or value in found or _NOT_A_PATH.search(value):
            continue
        top = value.partition("/")[0]
        if top not in root_names and not (
            _CODE_EXT.search(value) or top.lower() in _DIR_LIKE
        ):
            continue
        found.append(value)
    return found


def _license_path(root_entries: list[dict[str, object]]) -> str | None:
    for entry in root_entries:
        name = _clean(entry.get("name"))
        if name and _LICENSE_NAME.match(name):
            return name
    return None


def _license_url(root_entries: list[dict[str, object]]) -> str | None:
    path = _license_path(root_entries)
    if path is None:
        return None
    for entry in root_entries:
        if _clean(entry.get("name")) == path:
            return _clean(entry.get("html_url"))
    return None


def _license_title(text: str, *, limit: int = LICENSE_TITLE_CHARS) -> str | None:
    """许可文件正文里的首个非空行（许可名称原文，如 ``MIT License``）。"""
    head = text[:LICENSE_TITLE_SCAN_CHARS]
    for line in head.splitlines():
        title = line.strip().strip("#").strip()
        if title and len(title) <= limit:
            return title
    return None


def _runnable_hints(root_entries: list[dict[str, object]]) -> list[str]:
    hints: list[str] = []
    for entry in root_entries:
        name = _clean(entry.get("name"))
        if name and name.lower() in MANIFEST_FILES and name not in hints:
            hints.append(name)
    return hints


def _decode_content(payload: dict[str, object]) -> str | None:
    """把内容接口返回的 base64 正文解码成文本；不可解码返回 None。"""
    raw = payload.get("content")
    if not isinstance(raw, str) or not raw.strip():
        return None
    encoding = str(payload.get("encoding") or "base64")
    if encoding != "base64":
        return None
    try:
        decoded = base64.b64decode(raw.encode("utf-8"), validate=False)
    except (binascii.Error, ValueError):
        return None
    return decoded.decode("utf-8", errors="replace")


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
