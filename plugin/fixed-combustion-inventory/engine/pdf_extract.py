"""Extract the frozen Stage-R PDF scope without interpreting it."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from pypdf import PdfReader


DEFAULT_PAGES = list(range(3, 16)) + list(range(29, 56))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    request = json.loads(sys.stdin.read())
    source = Path(request["source_pdf_path"]).expanduser().resolve()
    output = Path(request["output_path"]).expanduser().resolve()
    pages = sorted(set(request.get("pages") or DEFAULT_PAGES))
    if not source.is_file():
        raise FileNotFoundError(source)
    reader = PdfReader(str(source))
    invalid = [page for page in pages if page < 1 or page > len(reader.pages)]
    if invalid:
        raise ValueError(f"invalid physical PDF pages: {invalid}")
    blocks = []
    for page_number in pages:
        text = reader.pages[page_number - 1].extract_text() or ""
        blocks.append(f"\n\n<!-- physical_pdf_page:{page_number} -->\n\n{text.strip()}\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(blocks), encoding="utf-8")
    print(json.dumps({
        "success": True,
        "stage": "pdf_scope_extraction",
        "source_pdf_path": str(source),
        "source_pdf_sha256": sha256(source),
        "physical_pages": pages,
        "page_count": len(pages),
        "output_path": str(output),
        "output_sha256": sha256(output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
