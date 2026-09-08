"""
Print a page exactly as it is stored in the database.

    .\\.venv\\Scripts\\python.exe scripts\\show_stored_page.py <document> <page>

<document> is any part of the filename or document id, so "annual" or "03" is
enough. <page> is the page number as printed in the PDF reader.

Why this exists: there is no test suite, so checking a step means reading its
output next to the real PDF. This is the tool for doing that, and it stays
useful for the rest of the project — every later step is easier to debug when
you can see the exact text it was given.
"""

import sys
from pathlib import Path

PROJECT_ROOT_FOLDER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT_FOLDER))

from groundwork.shared import database


def find_matching_documents(search_text: str) -> list[dict]:
    """Find documents whose id or filename contains the search text."""
    return database.fetch_all_rows(
        """
        SELECT doc_id, filename, page_count
        FROM documents
        WHERE doc_id ILIKE %s OR filename ILIKE %s
        ORDER BY filename
        """,
        (f"%{search_text}%", f"%{search_text}%"),
    )


def print_the_documents_we_have() -> None:
    """List every document in the database, for when the search finds nothing."""
    all_documents = database.fetch_all_rows(
        "SELECT doc_id, filename, page_count FROM documents ORDER BY filename"
    )
    if not all_documents:
        print("There are no documents in the database. Run step_01_ingest_pdf first.")
        return

    print("Documents currently stored:")
    for document in all_documents:
        print(f"  {document['filename']}  ({document['page_count']} pages)")


def show_one_page(doc_id: str, filename: str, page_number: int) -> int:
    """Print one stored page in full. Returns 0 on success."""
    page_row = database.fetch_one_row(
        "SELECT raw_text, footnotes FROM pages WHERE doc_id = %s AND page_no = %s",
        (doc_id, page_number),
    )
    if page_row is None:
        print(f"Page {page_number} of {filename} is not in the database.")
        return 1

    page_text = page_row["raw_text"] or ""
    footnotes = page_row["footnotes"] or {}

    print("=" * 78)
    print(f"{filename}  —  page {page_number}")
    print(f"{len(page_text):,} characters stored")
    print("=" * 78)
    print(page_text)

    print()
    print("-" * 78)
    if footnotes:
        print(f"FOOTNOTES CAPTURED ({len(footnotes)}):")
        for marker, body in sorted(footnotes.items()):
            print(f"  ({marker}) {body}")
    else:
        print("FOOTNOTES CAPTURED: none on this page")
    return 0


def main() -> int:
    command_line_arguments = sys.argv[1:]
    if len(command_line_arguments) < 2:
        print(__doc__)
        print()
        print_the_documents_we_have()
        return 1

    search_text = command_line_arguments[0]
    page_number = int(command_line_arguments[1])

    matching_documents = find_matching_documents(search_text)

    if not matching_documents:
        print(f"No document matches {search_text!r}.\n")
        print_the_documents_we_have()
        return 1

    if len(matching_documents) > 1:
        print(f"{search_text!r} matches more than one document. Be more specific:\n")
        for document in matching_documents:
            print(f"  {document['filename']}")
        return 1

    only_match = matching_documents[0]
    return show_one_page(only_match["doc_id"], only_match["filename"], page_number)


if __name__ == "__main__":
    sys.exit(main())
