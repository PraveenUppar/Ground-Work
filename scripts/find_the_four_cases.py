"""
Find the four cases the task brief requires, in the results we produced.

    .\\.venv\\Scripts\\python.exe scripts\\find_the_four_cases.py

The brief asks for at least one example of each:

  1. a fact corroborated across documents, even if expressed differently
  2. a genuine or likely contradiction
  3. an apparent contradiction explained by context
  4. an extraction or reasoning failure, and how it was handled

For the first three it also asks for the source evidence and the system's
reasoning, so this prints both rather than just naming the verdict.

WHY THIS IS A SCRIPT AND NOT A HAND-WRITTEN LIST. Picking examples by hand
invites picking flattering ones. This queries the database with stated
criteria, so the examples are whatever the system actually produced, and the
criteria can be argued with.
"""

import sys
from pathlib import Path

PROJECT_ROOT_FOLDER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT_FOLDER))

from groundwork.shared import database


def print_a_heading(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def print_one_fact(label: str, fact: dict) -> None:
    """Print a fact with everything needed to check it against the document."""
    subject = fact["subject_raw"] or "(the document's own subject)"
    value = fact["value_raw"]
    if fact["unit"]:
        value += f" {fact['unit']}"
    if fact["currency"]:
        value = f"{fact['currency']} {value}"

    print(f"  {label}")
    print(f"    {subject} — {fact['attribute_raw']} = {value}")

    qualifiers = []
    if fact["period_raw"]:
        qualifiers.append(f"period: {fact['period_raw']}")
    if fact["as_of_raw"]:
        qualifiers.append(f"as at: {fact['as_of_raw']}")
    if fact["scope_raw"]:
        qualifiers.append(f"scope: {fact['scope_raw'][:80]}")
    print(f"    {' | '.join(qualifiers) if qualifiers else 'no qualifiers stated'}")

    evidence = " ".join((fact["evidence_text"] or "").split())
    if len(evidence) > 150:
        evidence = evidence[:147] + "..."
    print(f"    source   : {fact['filename']}, page {fact['page_no']}, "
          f"chars {fact['char_start']}-{fact['char_end']}")
    print(f"    evidence : \"{evidence}\"")


def print_a_relation(row: dict, number: int) -> None:
    """Print one adjudicated pair in full: both facts, the verdict, the reasoning."""
    print(f"\n--- example {number} " + "-" * 58)
    print_one_fact("FACT A", {k[2:]: v for k, v in row.items() if k.startswith("a_")})
    print()
    print_one_fact("FACT B", {k[2:]: v for k, v in row.items() if k.startswith("b_")})
    print()
    print(f"  VERDICT   : {row['verdict'].upper()}   (decided by: {row['method']})")
    explanation = " ".join((row["explanation"] or "").split())
    print(f"  REASONING : {explanation}")


# The columns we need for both sides of every pair, aliased so one row carries
# two facts.
FACT_COLUMNS = """
    {t}.subject_raw AS {p}subject_raw, {t}.attribute_raw AS {p}attribute_raw,
    {t}.value_raw AS {p}value_raw, {t}.unit AS {p}unit, {t}.currency AS {p}currency,
    {t}.period_raw AS {p}period_raw, {t}.as_of_raw AS {p}as_of_raw,
    {t}.scope_raw AS {p}scope_raw, {t}.evidence_text AS {p}evidence_text,
    {t}.page_no AS {p}page_no, {t}.char_start AS {p}char_start,
    {t}.char_end AS {p}char_end, {t}.source_kind AS {p}source_kind,
    {t}.confidence AS {p}confidence, d{t}.filename AS {p}filename
"""


def build_query(where_clause: str, order_clause: str, limit: int) -> str:
    return f"""
        SELECT r.verdict, r.method, r.explanation,
               {FACT_COLUMNS.format(t='a', p='a_')},
               {FACT_COLUMNS.format(t='b', p='b_')}
        FROM relations r
        JOIN facts a ON a.fact_id = r.fact_a
        JOIN facts b ON b.fact_id = r.fact_b
        JOIN documents da ON da.doc_id = a.doc_id
        JOIN documents db ON db.doc_id = b.doc_id
        WHERE {where_clause}
        ORDER BY {order_clause}
        LIMIT {limit}
    """


def case_one_corroborated_across_documents() -> None:
    print_a_heading("CASE 1 — a fact corroborated across documents, worded differently")
    print("\nCriteria: verdict is 'corroborates', the two facts come from")
    print("DIFFERENT documents, and their attribute wording is not identical.")

    rows = database.fetch_all_rows(
        build_query(
            where_clause="""
                r.verdict = 'corroborates'
                AND a.doc_id <> b.doc_id
                AND lower(a.attribute_raw) <> lower(b.attribute_raw)
            """,
            # Prefer trustworthy sources and the biggest wording difference.
            order_clause="""
                (a.confidence + b.confidence) DESC,
                abs(length(a.attribute_raw) - length(b.attribute_raw)) DESC
            """,
            limit=3,
        )
    )
    if not rows:
        print("\n  NONE FOUND with different wording. Falling back to any")
        print("  cross-document corroboration.")
        rows = database.fetch_all_rows(
            build_query(
                "r.verdict = 'corroborates' AND a.doc_id <> b.doc_id",
                "(a.confidence + b.confidence) DESC",
                3,
            )
        )
    for number, row in enumerate(rows, start=1):
        print_a_relation(row, number)


def case_two_a_genuine_contradiction() -> None:
    print_a_heading("CASE 2 — a genuine or likely contradiction")
    print("\nCriteria, and each one exists to exclude a way of being wrong:")
    print("  both state a moment in time, AND THOSE MOMENTS ARE THE SAME")
    print("      -> a genuine disagreement is two claims about one moment")
    print("  the values differ by between 1% and 50%")
    print("      -> a 99% gap almost always means we compared unrelated things,")
    print("         not that the documents disagree")
    print("  both from sources we trust, and both values appear in their own")
    print("  evidence, so neither side is a suspected extraction error")

    rows = database.fetch_all_rows(
        build_query(
            where_clause="""
                r.verdict = 'contradicts'
                AND a.confidence >= 0.75 AND b.confidence >= 0.75
                AND a.value_in_evidence AND b.value_in_evidence
                AND a.value_num IS NOT NULL AND b.value_num IS NOT NULL
                AND coalesce(a.period_end, a.as_of_date) IS NOT NULL
                AND coalesce(a.period_end, a.as_of_date)
                    = coalesce(b.period_end, b.as_of_date)
                AND abs(a.value_num - b.value_num)
                    / greatest(abs(a.value_num), abs(b.value_num), 1) BETWEEN 0.01 AND 0.5
            """,
            # Cross-document disagreements first: a difference inside one
            # document is more often our own extraction error than a finding.
            order_clause="""
                (a.doc_id <> b.doc_id) DESC,
                (a.confidence + b.confidence) DESC
            """,
            limit=3,
        )
    )
    if not rows:
        print("\n  None across documents met every criterion. Relaxing to")
        print("  contradictions within one document.")
        rows = database.fetch_all_rows(
            build_query(
                """r.verdict = 'contradicts'
                   AND a.confidence >= 0.75 AND b.confidence >= 0.75
                   AND a.value_in_evidence AND b.value_in_evidence""",
                "(a.confidence + b.confidence) DESC",
                3,
            )
        )
    for number, row in enumerate(rows, start=1):
        print_a_relation(row, number)


def case_three_explained_by_context() -> None:
    print_a_heading("CASE 3 — an apparent contradiction explained by context")

    print("\n3a. Explained by ARITHMETIC — the strongest form. Two figures")
    print("differ, and a third documented quantity accounts for exactly the gap.")
    rows = database.fetch_all_rows(
        build_query(
            "r.method = 'derivation'",
            "(a.confidence + b.confidence) DESC, abs(a.value_num - b.value_num) DESC",
            2,
        )
    )
    for number, row in enumerate(rows, start=1):
        print_a_relation(row, number)

    print("\n\n3b. Explained by SCOPE — the two figures count different things.")
    rows = database.fetch_all_rows(
        build_query(
            """r.verdict = 'reconciled' AND r.method = 'rule'
               AND a.scope_raw IS NOT NULL AND b.scope_raw IS NOT NULL
               AND a.doc_id <> b.doc_id""",
            "(a.confidence + b.confidence) DESC",
            2,
        )
    )
    if not rows:
        rows = database.fetch_all_rows(
            build_query(
                """r.verdict = 'reconciled' AND r.method = 'rule'
                   AND a.scope_raw IS NOT NULL AND b.scope_raw IS NOT NULL""",
                "(a.confidence + b.confidence) DESC",
                2,
            )
        )
    for number, row in enumerate(rows, start=1):
        print_a_relation(row, number)

    print("\n\n3c. A STATE THAT CHANGED — not a contradiction at all.")
    print("The documents agree; the world moved between them.")
    rows = database.fetch_all_rows(
        build_query("r.verdict = 'superseded'", "a.as_of_date DESC", 3)
    )
    for number, row in enumerate(rows, start=1):
        print_a_relation(row, number)


def case_four_our_own_failures() -> None:
    print_a_heading("CASE 4 — extraction and reasoning failures we found")

    totals = database.fetch_one_row(
        """
        SELECT count(*) AS extracted,
               count(*) FILTER (WHERE grounded) AS grounded,
               count(*) FILTER (WHERE NOT grounded) AS rejected
        FROM facts
        """
    )
    print(f"\n  facts extracted : {totals['extracted']:,}")
    print(f"  proved against source : {totals['grounded']:,} "
          f"({totals['grounded'] / totals['extracted']:.1%})")
    print(f"  REJECTED as ungrounded : {totals['rejected']:,} "
          f"({totals['rejected'] / totals['extracted']:.1%})")

    print("\n  Every rejected fact quoted evidence that does not exist in the")
    print("  document. Examples of what the model produced:")
    for row in database.fetch_all_rows(
        """
        SELECT detail FROM failures
        WHERE stage = 'ground' AND detail LIKE %s
        ORDER BY random() LIMIT 5
        """,
        ("%not found on page%",),
    ):
        detail = row["detail"]
        print(f"    {detail[detail.find('not found'):][:120]}")

    print("\n  The full write-up of this and every other failure, including two")
    print("  we caused ourselves and fixed, is in docs/04_FAILURES.md.")


def main() -> int:
    print("=" * 78)
    print("THE FOUR CASES THE BRIEF ASKS FOR")
    print("=" * 78)
    print("\nEach example below is selected by a stated query, not chosen by hand.")

    case_one_corroborated_across_documents()
    case_two_a_genuine_contradiction()
    case_three_explained_by_context()
    case_four_our_own_failures()
    return 0


if __name__ == "__main__":
    sys.exit(main())
