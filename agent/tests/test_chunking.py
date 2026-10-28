from agno.knowledge.document import Document

from auditbox_agent.chunking import StructureAwareChunking


def test_structure_aware_chunking_is_bounded_and_overlapping() -> None:
    content = "".join(f"第{i:03d}项运维数据正常。" for i in range(160))
    chunks = StructureAwareChunking(chunk_size=512, overlap=50).chunk(
        Document(id="manual", name="手册", content=content)
    )
    assert len(chunks) > 3
    assert all(0 < len(chunk.content) <= 512 for chunk in chunks)
    assert [chunk.meta_data["chunk"] for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert all(chunk.meta_data["chunk_total"] == len(chunks) for chunk in chunks)
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert any(previous.content[-size:] == current.content[:size] for size in range(45, 51))


def test_structure_aware_chunking_prefers_chinese_sentence_boundary() -> None:
    chunks = StructureAwareChunking(chunk_size=100, overlap=10).chunk(
        Document(name="测试", content="甲" * 65 + "。" + "乙" * 80)
    )
    assert chunks[0].content.endswith("。")
    assert all(len(chunk.content) <= 100 for chunk in chunks)
