from typing import Any

from agno.knowledge.document import Document


def document_dict(document: Document) -> dict[str, Any]:
    return {
        "name": document.name,
        "content": document.content,
        "meta_data": document.meta_data,
    }
