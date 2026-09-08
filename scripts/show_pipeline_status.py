"""
Show what is actually in the database right now.

    .\\.venv\\Scripts\\python.exe scripts\\show_pipeline_status.py

Answers "where are we?" from the data itself rather than from memory or from
what a script printed an hour ago.
"""

import sys
from pathlib import Path

PROJECT_ROOT_FOLDER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT_FOLDER))

from groundwork.shared import database, llm_client


def print_overall_counts() -> None:
    print("=" * 78)
    print("WHAT IS IN THE DATABASE")
    print("=" * 78)
    for table_name in (
        "documents", "pages", "chunks", "facts",
        "entities", "attribute_families", "relations", "failures",
    ):
        print(f"  {table_name:<20} {database.count_rows_in_table(table_name):>8,}")


def print_per_document_progress() -> None:
    rows = database.fetch_all_rows(
        """
        SELECT d.filename,
               d.page_count,
               (SELECT count(*) FROM pages  p WHERE p.doc_id  = d.doc_id) AS pages,
               (SELECT count(*) FROM chunks c WHERE c.doc_id  = d.doc_id) AS chunks,
               (SELECT count(*) FROM facts  f WHERE f.doc_id  = d.doc_id) AS facts,
               (SELECT count(*) FROM facts  f WHERE f.doc_id  = d.doc_id
                                              AND f.grounded) AS grounded
        FROM documents d
        ORDER BY d.filename
        """
    )
    print()
    print("=" * 78)
    print("PROGRESS BY DOCUMENT")
    print("=" * 78)
    print(f"  {'document':<46}{'pages':>7}{'chunks':>8}{'facts':>8}{'grounded':>10}")
    for row in rows:
        name = row["filename"]
        if len(name) > 44:
            name = name[:41] + "..."
        print(
            f"  {name:<46}{row['pages']:>7}{row['chunks']:>8}"
            f"{row['facts']:>8,}{row['grounded']:>10,}"
        )


def print_what_went_wrong() -> None:
    rows = database.fetch_all_rows(
        """
        SELECT stage, kind, count(*) AS how_many
        FROM failures GROUP BY stage, kind ORDER BY how_many DESC
        """
    )
    print()
    print("=" * 78)
    print("FAILURES RECORDED")
    print("=" * 78)
    if not rows:
        print("  none")
        return
    for row in rows:
        print(f"  {row['stage']:<12} {row['kind']:<28} {row['how_many']:>6}")


def print_fact_quality() -> None:
    row = database.fetch_one_row(
        """
        SELECT count(*)                                        AS total,
               count(*) FILTER (WHERE period_raw  IS NOT NULL) AS with_period,
               count(*) FILTER (WHERE as_of_raw   IS NOT NULL) AS with_as_of,
               count(*) FILTER (WHERE scope_raw   IS NOT NULL) AS with_scope,
               count(*) FILTER (WHERE subject_raw IS NOT NULL) AS with_subject,
               count(*) FILTER (WHERE grounded)                AS grounded
        FROM facts
        """
    )
    if not row or not row["total"]:
        return

    print()
    print("=" * 78)
    print("FACT QUALITY")
    print("=" * 78)
    total = row["total"]
    for label, key in (
        ("carrying a period", "with_period"),
        ("carrying an as-of date", "with_as_of"),
        ("carrying a scope", "with_scope"),
        ("naming their subject", "with_subject"),
        ("grounded (proved real)", "grounded"),
    ):
        count = row[key]
        print(f"  {label:<28} {count:>7,}  ({count / total:.0%})")


def print_next_step() -> None:
    facts = database.count_rows_in_table("facts")
    grounded = database.fetch_one_row(
        "SELECT count(*) AS n FROM facts WHERE grounded"
    )["n"]
    relations = database.count_rows_in_table("relations")

    print()
    print("=" * 78)
    print("WHERE WE ARE")
    print("=" * 78)
    if facts == 0:
        print("  Next: Step 5 — extract facts")
    elif grounded == 0:
        print("  Next: Step 6 — grounding. No fact has been proved real yet.")
    elif relations == 0:
        print("  Next: Step 7 — normalise, then Step 8 pair, then Step 9 compare")
    else:
        print("  Next: Step 10 — the interface")


def main() -> int:
    print_overall_counts()
    print_per_document_progress()
    print_fact_quality()
    print_what_went_wrong()
    print()
    print("=" * 78)
    print("LLM CACHE")
    print("=" * 78)
    print(f"  {llm_client.describe_the_cache()}")
    print_next_step()
    return 0


if __name__ == "__main__":
    sys.exit(main())
