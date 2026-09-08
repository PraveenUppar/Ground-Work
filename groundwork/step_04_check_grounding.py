"""
STEP 4 — prove every fact came from real text in a real document.

Run it with:
    .\\.venv\\Scripts\\python.exe -m groundwork.step_04_check_grounding

---------------------------------------------------------------------------
THIS IS THE STEP THE WHOLE PROJECT RESTS ON
---------------------------------------------------------------------------
Up to now every fact is only "a model said so". This step turns as many of
them as possible into "the document says so, on this page, at this character".

The check itself is almost embarrassingly simple: take the quote the model
copied, and look for it in the passage the model was given. Either it is there
or it is not.

Simple, and worth more than any amount of prompt engineering. A model that
invents a plausible sentence gets caught here. A model that half-remembers a
number gets caught here. And the facts it rejects are not waste — they are the
"extraction failure" case the brief asks us to demonstrate, arriving with
evidence attached.

---------------------------------------------------------------------------
THREE WAYS OF LOOKING, AND WHY THE THIRD IS NOT CHEATING
---------------------------------------------------------------------------
1. EXACT      the quote is in its own chunk, character for character.

2. FLEXIBLE   the quote is in its own chunk but spaced differently. PDF text
              is full of odd line breaks, and a model that turns a newline
              into a space has not invented anything. We match the words in
              order while allowing any whitespace between them, so the
              recovered position is still exact.

3. SAME PAGE  the quote is not in the chunk the fact claimed, but it is
              elsewhere on the same page.

The third case is the one batching creates: several passages go in one
request, and the model attaches a fact to the wrong one. The claim is still
real and still traceable — only the label was wrong. Recording HOW each fact
was found keeps that visible rather than hiding it behind a tick.

Searching stops at the page. Going wider would eventually "find" any string
somewhere in 227 pages, and a check that always succeeds is not a check.

---------------------------------------------------------------------------
THE HARDER QUESTION, ASKED SEPARATELY
---------------------------------------------------------------------------
Grounding proves the text exists. It does NOT prove the text supports the
claim. A fact reading "Performance-based ESOPs = 15,060,000" can quote a real
table row that contains three different numbers and no column headings. The
quote is honest; the pairing may still be wrong.

So we ask a second question of every fact: does the claimed VALUE actually
appear inside its own evidence? A fact whose number cannot be found in its own
quote is not necessarily wrong, but it is a fact nobody can check — and those
should be visible, not silently mixed in with the rest.
"""

import re
import sys

from groundwork.shared import config, database


# ===========================================================================
# Tuning numbers
# ===========================================================================

# A quote shorter than this is not evidence of anything. "5" appears on almost
# every page of a financial document, so finding it proves nothing at all.
SHORTEST_EVIDENCE_WORTH_CHECKING = 3

# How many rejected facts to print at the end, for reading by eye.
HOW_MANY_REJECTIONS_TO_SHOW = 12


# Characters to ignore when asking whether a value appears in its evidence.
# "1,008" and "1008" are the same number written twice, and a thousands
# separator should not decide whether a fact is checkable.
CHARACTERS_TO_IGNORE_WHEN_COMPARING_VALUES = re.compile(r"[\s,]")


# ===========================================================================
# Looking for the evidence
# ===========================================================================

def build_a_pattern_that_ignores_whitespace(evidence_text: str):
    """
    Turn a quote into a pattern matching the same words with any spacing.

    Why this is safe rather than lenient: every non-whitespace character still
    has to match exactly and in order. All we forgive is a newline where the
    model wrote a space — which is a difference in how PDF text was read, not
    a difference in what the document says.

    Returns None when the quote has nothing solid in it to match.
    """
    pieces_of_the_quote = evidence_text.split()
    if not pieces_of_the_quote:
        return None
    pattern_text = r"\s+".join(re.escape(piece) for piece in pieces_of_the_quote)
    return re.compile(pattern_text)


def find_evidence_in_text(evidence_text: str, text_to_search: str) -> tuple[int, int] | None:
    """
    Find a quote in a body of text. Returns its position, or None.

    Tries an exact search first because it is instant and gives an exact
    position; only falls back to the whitespace-forgiving pattern if that
    fails.
    """
    exact_position = text_to_search.find(evidence_text)
    if exact_position != -1:
        return exact_position, exact_position + len(evidence_text)

    flexible_pattern = build_a_pattern_that_ignores_whitespace(evidence_text)
    if flexible_pattern is None:
        return None

    match = flexible_pattern.search(text_to_search)
    if match is None:
        return None
    return match.start(), match.end()


def ground_one_fact(fact_row: dict) -> dict:
    """
    Decide whether one fact's evidence is real, and where exactly it sits.

    Returns what we learned: whether it is grounded, how we found it, its
    position within the page, and whether its own value appears in its own
    quote.
    """
    evidence_text = (fact_row["evidence_text"] or "").strip()
    chunk_text = fact_row["chunk_text"] or ""
    page_text = fact_row["page_text"] or ""
    chunk_starts_at = fact_row["chunk_char_start"] or 0

    result = {
        "fact_id": fact_row["fact_id"],
        "grounded": False,
        "grounding_method": None,
        "char_start": None,
        "char_end": None,
        "value_in_evidence": False,
        "why_rejected": None,
    }

    if len(evidence_text) < SHORTEST_EVIDENCE_WORTH_CHECKING:
        result["why_rejected"] = (
            f"evidence too short to prove anything: {evidence_text!r}"
        )
        return result

    result["value_in_evidence"] = value_appears_in_its_own_evidence(
        fact_row["value_raw"], evidence_text
    )

    # 1 and 2: look inside the chunk the fact says it came from.
    position_in_chunk = find_evidence_in_text(evidence_text, chunk_text)
    if position_in_chunk is not None:
        start_within_chunk, end_within_chunk = position_in_chunk
        found_exactly = chunk_text.find(evidence_text) != -1

        result["grounded"] = True
        result["grounding_method"] = "exact" if found_exactly else "flexible"
        # Positions are stored against the PAGE, not the chunk, because that is
        # what the interface needs in order to show the source.
        result["char_start"] = chunk_starts_at + start_within_chunk
        result["char_end"] = chunk_starts_at + end_within_chunk
        return result

    # 3: the fact may have been attached to the wrong passage of a batched
    # request. Look across the whole page it belongs to.
    position_in_page = find_evidence_in_text(evidence_text, page_text)
    if position_in_page is not None:
        result["grounded"] = True
        result["grounding_method"] = "same_page"
        result["char_start"], result["char_end"] = position_in_page
        return result

    result["why_rejected"] = f"not found on page {fact_row['page_no']}: {evidence_text[:120]!r}"
    return result


def value_appears_in_its_own_evidence(value_raw: str, evidence_text: str) -> bool:
    """
    Ask whether the claimed value can be found inside the quote supporting it.

    Ignores whitespace and thousands separators, so "1,008" still matches
    "1008". Everything else must match, because a value that is merely similar
    is a different value.

    A false here does not mean the fact is wrong. It means nobody can check it
    from its own evidence, which is a different problem and worth separating.
    """
    if not value_raw or not evidence_text:
        return False

    def simplify(text: str) -> str:
        return CHARACTERS_TO_IGNORE_WHEN_COMPARING_VALUES.sub("", text.lower())

    return simplify(value_raw) in simplify(evidence_text)


# ===========================================================================
# Running over everything
# ===========================================================================

def load_facts_with_their_source_text(document_id: str | None) -> list[dict]:
    """
    Fetch every fact alongside the chunk and the page it should have come from.

    One query rather than three thousand. The database is remote, so a query
    per fact would be an hour of waiting.
    """
    # Fetched in three separate queries and joined in Python, rather than one
    # query with two JOINs.
    #
    # The single-query version returned the full text of a page once for EVERY
    # fact on that page, and the full text of a chunk once for every fact in
    # it. Across five thousand facts that is a great deal of duplicated text
    # travelling over the network, and on a long run it was enough to break the
    # connection outright. Fetching each page and each chunk once and matching
    # them up locally sends a fraction of the data.
    where_clause = " WHERE f.doc_id = %s" if document_id else ""
    parameters = (document_id,) if document_id else ()

    fact_rows = database.fetch_all_rows(
        "SELECT fact_id, doc_id, page_no, chunk_id, evidence_text, value_raw "
        "FROM facts f" + where_clause,
        parameters,
    )

    chunk_text_by_id = {
        row["chunk_id"]: (row["text"], row["char_start"])
        for row in database.fetch_all_rows(
            "SELECT chunk_id, text, char_start FROM chunks c"
            + (" WHERE c.doc_id = %s" if document_id else ""),
            parameters,
        )
    }
    page_text_by_id = {
        (row["doc_id"], row["page_no"]): row["raw_text"]
        for row in database.fetch_all_rows(
            "SELECT doc_id, page_no, raw_text FROM pages p"
            + (" WHERE p.doc_id = %s" if document_id else ""),
            parameters,
        )
    }

    for fact_row in fact_rows:
        chunk_text, chunk_starts_at = chunk_text_by_id.get(fact_row["chunk_id"], ("", 0))
        fact_row["chunk_text"] = chunk_text
        fact_row["chunk_char_start"] = chunk_starts_at
        fact_row["page_text"] = page_text_by_id.get(
            (fact_row["doc_id"], fact_row["page_no"]), ""
        )

    return fact_rows


def save_what_we_learned(results: list[dict]) -> int:
    """Write the grounding outcome back to every fact, in batches."""
    values_for_each_row = [
        (
            result["grounded"],
            result["grounding_method"],
            result["char_start"],
            result["char_end"],
            result["value_in_evidence"],
            result["fact_id"],
        )
        for result in results
    ]
    return database.update_rows_in_batches(
        """
        UPDATE facts
        SET grounded = %s,
            grounding_method = %s,
            char_start = %s,
            char_end = %s,
            value_in_evidence = %s
        WHERE fact_id = %s
        """,
        values_for_each_row,
    )


def record_the_rejections(results: list[dict], fact_rows_by_id: dict) -> None:
    """
    Write every rejected fact to the failures table.

    These are not noise to be swept up. The brief asks for an extraction
    failure with evidence, and this is a queryable list of them, each with the
    quote that could not be found and the page it claimed to be on.
    """
    rejection_rows = []
    for result in results:
        if result["grounded"] or not result["why_rejected"]:
            continue
        fact_row = fact_rows_by_id[result["fact_id"]]
        rejection_rows.append({
            "doc_id": None,
            "page_no": fact_row["page_no"],
            "chunk_id": None,
            "stage": "ground",
            "kind": "evidence_not_found",
            "detail": f"{result['fact_id']}: {result['why_rejected']}"[:1500],
        })
    database.insert_rows_in_batches("failures", rejection_rows)


def main() -> int:
    command_line_arguments = sys.argv[1:]

    which_document = None
    if "--document" in command_line_arguments:
        which_document = command_line_arguments[
            command_line_arguments.index("--document") + 1
        ]

    print("=" * 78)
    print("STEP 4 — grounding: proving each fact against its source")
    print("=" * 78)

    config.stop_unless_these_settings_are_filled_in(["DATABASE_URL"])

    document_id = None
    if which_document:
        document_row = database.fetch_one_row(
            "SELECT doc_id, filename FROM documents WHERE filename ILIKE %s",
            (f"%{which_document}%",),
        )
        if document_row is None:
            print(f"No document matched {which_document!r}.")
            return 1
        document_id = document_row["doc_id"]
        print(f"\nOnly: {document_row['filename']}")

    print("\nLoading facts with their source text...")
    fact_rows = load_facts_with_their_source_text(document_id)
    if not fact_rows:
        print("No facts to check. Run step_03_extract_facts first.")
        return 1
    print(f"  {len(fact_rows):,} facts to check")

    database.execute_sql("DELETE FROM failures WHERE stage = 'ground'")

    results = [ground_one_fact(fact_row) for fact_row in fact_rows]

    print("\nSaving results...")
    save_what_we_learned(results)
    record_the_rejections(results, {row["fact_id"]: row for row in fact_rows})

    how_many_grounded = sum(1 for r in results if r["grounded"])
    how_many_rejected = len(results) - how_many_grounded
    how_many_with_checkable_value = sum(1 for r in results if r["value_in_evidence"])

    methods: dict[str, int] = {}
    for result in results:
        if result["grounding_method"]:
            methods[result["grounding_method"]] = methods.get(result["grounding_method"], 0) + 1

    print()
    print("=" * 78)
    print("RESULT")
    print("=" * 78)
    print(f"  facts checked            : {len(results):,}")
    print(f"  GROUNDED                 : {how_many_grounded:,}  "
          f"({how_many_grounded / len(results):.1%})")
    print(f"  rejected                 : {how_many_rejected:,}  "
          f"({how_many_rejected / len(results):.1%})")
    print()
    print("  how the grounded ones were found:")
    print(f"    exact quote in its own chunk    : {methods.get('exact', 0):,}")
    print(f"    same words, different spacing   : {methods.get('flexible', 0):,}")
    print(f"    elsewhere on the same page      : {methods.get('same_page', 0):,}")
    print("      ^ these were attached to the wrong passage of a batched")
    print("        request. The claim is real; only the label was wrong.")
    print()
    print("  the harder question:")
    print(f"    value found in its own evidence : {how_many_with_checkable_value:,}  "
          f"({how_many_with_checkable_value / len(results):.1%})")
    print("      ^ grounding proves the quote is real. This asks whether the")
    print("        quote actually supports the number attached to it.")

    if how_many_rejected:
        print()
        print("=" * 78)
        print(f"UP TO {HOW_MANY_REJECTIONS_TO_SHOW} REJECTED FACTS — case 4 material")
        print("=" * 78)
        shown = 0
        for result in results:
            if result["grounded"] or shown >= HOW_MANY_REJECTIONS_TO_SHOW:
                continue
            print(f"  {result['why_rejected']}")
            shown += 1

    print()
    print("Grounded facts now carry a page and a character range.")
    print("Next: Step 5 — normalise values so they can be compared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
