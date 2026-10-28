"""Isolated, bounded knowledge ingestion helpers.

Agno's default Markdown reader uses ``unstructured`` and may attempt to fetch
NLTK resources at runtime. That is unsuitable for an offline deployment and
can leave an upload request blocked indefinitely. Text-like formats use the
dependency-free text reader here, while binary document formats continue to
use Agno's format-specific readers.
"""

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from agno.knowledge.document import Document
from agno.knowledge.reader.text_reader import TextReader

from auditbox_agent.chunking import StructureAwareChunking
from auditbox_agent.config import Settings
from auditbox_agent.runtime import AgentRuntime


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        elif tag in {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


class HTMLTextReader(TextReader):
    """Index rendered HTML text instead of tags, CSS and JavaScript."""

    def read(
        self,
        file: Any,
        name: str | None = None,
        password: str | None = None,
    ) -> list[Document]:
        del password
        if isinstance(file, Path):
            raw = file.read_text(encoding=self.encoding or "utf-8")
            file_name = name or file.stem
        else:
            file.seek(0)
            value = file.read()
            raw = value.decode(self.encoding or "utf-8") if isinstance(value, bytes) else value
            file_name = name or Path(getattr(file, "name", "html_file")).stem
        parser = _VisibleTextParser()
        parser.feed(raw)
        document = Document(name=file_name, content="".join(parser.parts))
        return self.chunk_document(document) if self.chunk else [document]


def build_file_reader(suffix: str, settings: Settings) -> Any:
    """Return a format-aware reader sharing one deterministic chunking policy."""
    chunker = StructureAwareChunking(
        chunk_size=settings.knowledge_chunk_size,
        overlap=settings.knowledge_chunk_overlap,
    )
    readers: dict[str, Any] = {
        ".txt": TextReader(chunking_strategy=chunker),
        ".md": TextReader(chunking_strategy=chunker),
        ".html": HTMLTextReader(chunking_strategy=chunker),
        ".htm": HTMLTextReader(chunking_strategy=chunker),
    }
    if suffix.casefold() == ".pdf":
        try:
            from agno.knowledge.reader.pdf_reader import PDFReader
        except ImportError as exc:
            raise RuntimeError(
                "PDF 知识解析依赖未安装，请先执行 uv sync --extra knowledge"
            ) from exc
        return PDFReader(split_on_pages=False, chunking_strategy=chunker)
    try:
        return readers[suffix.casefold()]
    except KeyError as exc:
        raise ValueError(f"不支持的知识文件类型：{suffix or '无扩展名'}") from exc


def ingest_knowledge_file(
    path: str,
    name: str,
    description: str | None,
    metadata: dict[str, Any],
) -> None:
    """Index one file in a disposable worker process."""

    settings = Settings()
    runtime = AgentRuntime(settings)
    try:
        if runtime.knowledge is None:
            raise RuntimeError("知识库未启用")
        suffix = Path(path).suffix.casefold()
        reader = build_file_reader(suffix, settings)
        runtime.knowledge.insert(
            name=name,
            description=description,
            path=path,
            metadata=metadata,
            reader=reader,
        )
    finally:
        runtime.close()
