"""提取学校通知正文容器，避免把菜单与上一篇／下一篇当作通知内容。"""

from html.parser import HTMLParser


class _NoticeBody(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.root: int | None = None
        self.parts: list[str] = []
        self.ignored = 0
        self.finished = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if self.root is None and not self.finished and (
            tag in {"article", "main"}
            or attributes.get("id") == "vsb_content"
            or "v_news_content" in (attributes.get("class") or "").split()
        ):
            self.root = len(self.stack)
        if self.root is not None:
            if tag in {"script", "style", "nav", "aside"}:
                self.ignored += 1
            if tag in {"p", "div", "br", "li", "tr"}:
                self.parts.append("\n")
        if tag not in {"br", "img", "hr", "input", "meta", "link", "wbr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.stack:
            return
        position = len(self.stack) - 1 - self.stack[::-1].index(tag)
        if self.root is not None:
            if tag in {"script", "style", "nav", "aside"}:
                self.ignored = max(0, self.ignored - 1)
            if tag in {"p", "div", "li", "tr"}:
                self.parts.append("\n")
            if position <= self.root:
                self.root = None
                self.finished = True
        del self.stack[position:]

    def handle_data(self, data: str) -> None:
        if self.root is not None and not self.ignored:
            self.parts.append(data)


def notice_body(html: str) -> str | None:
    parser = _NoticeBody()
    parser.feed(html)
    return "".join(parser.parts) if parser.parts else None
