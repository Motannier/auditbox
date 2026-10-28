"""Deterministic, Chinese-friendly chunking for the shared knowledge base."""

from __future__ import annotations

import re

from agno.knowledge.chunking.strategy import ChunkingStrategy
from agno.knowledge.document import Document


class StructureAwareChunking(ChunkingStrategy):
    """Split all supported documents with one bounded, overlapping policy."""

    strategy_name = "structure_aware_character_window"

    def __init__(self, chunk_size: int = 512, overlap: int = 50):
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be non-negative and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t\f\v]+", " ", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _natural_end(self, content: str, start: int, hard_end: int) -> int:
        if hard_end >= len(content):
            return len(content)
        minimum = start + max(1, int(self.chunk_size * 0.6))
        window = content[minimum:hard_end]
        for pattern in (r"\n\n", r"\n", r"[。！？；.!?;]", r"\s"):
            matches = list(re.finditer(pattern, window))
            if matches:
                return minimum + matches[-1].end()
        return hard_end

    def chunk(self, document: Document) -> list[Document]:
        content = self._normalize(document.content or "")
        if not content:
            return []
        slices: list[str] = []
        start = 0
        while start < len(content):
            hard_end = min(start + self.chunk_size, len(content))
            end = self._natural_end(content, start, hard_end)
            value = content[start:end].strip()
            if value:
                slices.append(value)
            if end >= len(content):
                break
            start = max(start + 1, end - self.overlap)
        total = len(slices)
        chunks: list[Document] = []
        for index, value in enumerate(slices, start=1):
            metadata = dict(document.meta_data or {})
            metadata.update(
                {
                    "chunk": index,
                    "chunk_total": total,
                    "chunk_size": len(value),
                    "chunk_overlap": self.overlap,
                    "chunk_strategy": self.strategy_name,
                }
            )
            chunks.append(
                Document(
                    id=self._generate_chunk_id(document, index, value),
                    name=document.name,
                    meta_data=metadata,
                    content=value,
                )
            )
        return chunks
