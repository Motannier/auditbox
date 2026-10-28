from unittest.mock import MagicMock, patch

import pytest

from auditbox_agent.config import Settings
from auditbox_agent.knowledge_ingestion import build_file_reader, ingest_knowledge_file


def test_markdown_ingestion_uses_dependency_free_text_reader(tmp_path) -> None:
    source = tmp_path / "manual.md"
    source.write_text("# 手册\n\n交换机运维内容", encoding="utf-8")
    runtime = MagicMock()
    runtime.knowledge = MagicMock()

    with patch("auditbox_agent.knowledge_ingestion.AgentRuntime", return_value=runtime):
        ingest_knowledge_file(str(source), "manual.md", None, {"source": "file"})

    kwargs = runtime.knowledge.insert.call_args.kwargs
    assert kwargs["reader"].__class__.__name__ == "TextReader"
    assert kwargs["reader"].chunking_strategy.chunk_size == 512
    assert kwargs["reader"].chunking_strategy.overlap == 50
    runtime.close.assert_called_once_with()


def test_pdf_ingestion_uses_format_reader_with_unified_chunking(tmp_path) -> None:
    pytest.importorskip("pypdf")
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"%PDF-1.7")
    runtime = MagicMock()
    runtime.knowledge = MagicMock()

    with patch("auditbox_agent.knowledge_ingestion.AgentRuntime", return_value=runtime):
        ingest_knowledge_file(str(source), "manual.pdf", None, {"source": "file"})

    reader = runtime.knowledge.insert.call_args.kwargs["reader"]
    assert reader.__class__.__name__ == "PDFReader"
    assert reader.chunking_strategy.chunk_size == 512
    assert reader.chunking_strategy.overlap == 50


def test_every_supported_format_uses_the_same_chunking_policy() -> None:
    settings = Settings(_env_file=None)
    for suffix in (".txt", ".md", ".html", ".htm"):
        reader = build_file_reader(suffix, settings)
        assert reader.chunking_strategy.chunk_size == 512
        assert reader.chunking_strategy.overlap == 50


def test_pdf_reader_has_actionable_error_without_knowledge_extra() -> None:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="uv sync --extra knowledge"):
            build_file_reader(".pdf", Settings(_env_file=None))
