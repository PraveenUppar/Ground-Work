"""
STEP 2 — cut pages into passages an LLM can read carefully.

Run it with:
    .\\.venv\\Scripts\\python.exe -m groundwork.step_02_build_chunks

---------------------------------------------------------------------------
WHY CHUNK AT ALL
---------------------------------------------------------------------------
The obvious answer is "models have a size limit", and that is the least
important reason. Three others matter more:

  GROUNDING NEEDS AN ADDRESS. Our whole claim is "this fact came from page 2,
  characters 1180 to 1265". Hand a model a whole document and a returned quote
  has no address. A chunk carries its exact position, so anything extracted
  from it inherits that position.

  CACHING NEEDS A UNIT. The cache key is a hash of the chunk. Fixing a prompt
  or re-reading one page then costs one call, not a whole document.

  FAILURE NEEDS ISOLATION. One malformed passage fails on its own instead of
  killing a 227-page run.

---------------------------------------------------------------------------
THE PART THAT ACTUALLY MATTERS: THE CONTEXT HEADER
---------------------------------------------------------------------------
Here is a real passage from the annual report:

    98,135
    (1,5)
    Workforce strength

Read on its own that is a number, a marker and a label. No date. No
definition. The best a model can do is return a fact with empty qualifiers.

An unqualified fact is worse than no fact. In Step 7 it meets another
headcount figure, also unqualified, and with no dates and no scopes to compare
the system has no choice but to call it a contradiction. It would be wrong,
confidently, with a real quote attached.

So we staple the missing context onto the front before sending:

    [SECTION] Delhivery In Numbers
    [FOOTNOTE 1] As of March 31, 2024
    [FOOTNOTE 5] Includes permanent employees, contractual workers and
                 last mile delivery partner agents

Now the model can fill in the as-of date and the scope. THIS STEP IS WHAT
MAKES THE "APPARENT CONTRADICTION EXPLAINED BY CONTEXT" CASE POSSIBLE. Without
it, that required deliverable cannot be produced at all, because the context
was never captured.

Note the precision: page 2 carries five footnotes, but this passage refers to
(1,5), so it gets one and five and not the other three. Attaching all of them
would invite the model to reach for the wrong qualifier.

---------------------------------------------------------------------------
WHY THE HEADER IS STORED SEPARATELY FROM THE TEXT
---------------------------------------------------------------------------
Step 4 will check that a quoted piece of evidence genuinely exists in the
source. If the header were mixed into the text, we would be verifying our own
scaffolding as though the document had said it.

Two columns keeps one question always answerable: did the document say this,
or did we? That distinction is what stops grounding from becoming theatre.
"""

import re
import statistics
import sys

from groundwork.shared import config, database
from groundwork.step_01_ingest_pdf import (
    FOOTNOTE_SECTION_MARKER,
    TABLE_SECTION_MARKER,
    find_footnote_markers_in_text,
)


# ===========================================================================
# Tuning numbers
# ===========================================================================

# Roughly how many words we aim to put in one chunk.
#
# This number is not a style choice, it is a budget. One chunk means one API
# call, and the free tier allows a few hundred calls a day. At 1,000 words our
# 227 pages come to somewhere around 200 chunks, so a complete run fits inside
# a single day with room left to retry. Halving it would double the cost and
# put a full re-run out of reach.
TARGET_WORDS_PER_CHUNK = 1000

# A single paragraph longer than this is broken up at sentence boundaries.
# Without this, one enormous block of legal prose becomes one enormous chunk.
LONGEST_A_SINGLE_PARAGRAPH_MAY_BE = int(TARGET_WORDS_PER_CHUNK * 1.5)

# Passages shorter than this are page scraps — a stray number, a lone caption.
FEWEST_WORDS_WORTH_AN_LLM_CALL = 5

# A passage with no digits needs at least this many capitalised words before we
# will spend a call on it. Facts without numbers do exist ("Mr X resigned as a
# director"), and they are always about a named thing, so capitalised words are
# the signal that something nameable is present.
FEWEST_CAPITALISED_WORDS_WORTH_AN_LLM_CALL = 2

# How many stored chunks to verify against their pages after saving.
HOW_MANY_CHUNKS_TO_VERIFY = 40


# Sentence endings, used only when one paragraph is too big to keep whole.
SENTENCE_ENDING_PATTERN = re.compile(r"(?<=[.!?])\s+")

# A word that starts with a capital letter and is not simply shouting.
CAPITALISED_WORD_PATTERN = re.compile(r"\b[A-Z][a-z]{2,}\b")

ANY_DIGIT_PATTERN = re.compile(r"\d")

# Footnotes marked with a symbol rather than a number.
#
# These need different handling. A bare "*" cannot be searched for in running
# text the way "(1,5)" can — it would match ordinary punctuation and typographic
# noise. But a symbol-marked footnote is always the only one of its kind on the
# page, and it always qualifies whatever figure it sits beneath. So rather than
# hunt for a reference we cannot find reliably, we attach it to every passage on
# its page.
#
# Nine pages of the annual report use one. Without this they contributed no
# qualifier at all, which is exactly how a scope difference gets mistaken for a
# contradiction.
FOOTNOTE_MARKERS_THAT_ARE_SYMBOLS = {"*", "†", "‡", "§"}


# ===========================================================================
# 1. Finding the three regions of a page
# ===========================================================================

def find_the_three_regions(page_text: str) -> dict[str, tuple[int, int] | None]:
    """
    Work out where the prose, the tables and the footnotes each begin and end.

    Step 1 assembled every page in the same order — prose, then tables, then
    footnotes — separating them with marker lines. Those markers are our own
    scaffolding, so the regions returned here start after them: the marker text
    itself never ends up inside a chunk.

    Any region can be missing. Most pages have no tables; most have no
    footnotes.
    """
    where_tables_begin = page_text.find(TABLE_SECTION_MARKER)
    where_footnotes_begin = page_text.find(FOOTNOTE_SECTION_MARKER)

    # Prose runs from the top of the page to whichever marker comes first.
    end_of_prose = len(page_text)
    if where_tables_begin != -1:
        end_of_prose = where_tables_begin
    elif where_footnotes_begin != -1:
        end_of_prose = where_footnotes_begin

    regions: dict[str, tuple[int, int] | None] = {
        "prose": (0, end_of_prose) if end_of_prose > 0 else None,
        "table": None,
        "footnotes": None,
    }

    if where_tables_begin != -1:
        start_of_table_content = where_tables_begin + len(TABLE_SECTION_MARKER)
        end_of_tables = (
            where_footnotes_begin if where_footnotes_begin != -1 else len(page_text)
        )
        regions["table"] = (start_of_table_content, end_of_tables)

    if where_footnotes_begin != -1:
        start_of_footnote_content = where_footnotes_begin + len(FOOTNOTE_SECTION_MARKER)
        regions["footnotes"] = (start_of_footnote_content, len(page_text))

    return regions


# ===========================================================================
# 2. Splitting a region into pieces, keeping track of exactly where each is
# ===========================================================================

def trim_a_span(page_text: str, span_start: int, span_end: int) -> tuple[int, int]:
    """
    Move a span's edges inward past any surrounding whitespace.

    Offsets are the whole point of this file, so trimming has to move the
    NUMBERS, not just the text. Calling .strip() on the text and keeping the
    original offsets is exactly the kind of quiet mistake that makes the
    interface highlight the wrong sentence three steps later.
    """
    piece = page_text[span_start:span_end]
    how_much_whitespace_at_the_front = len(piece) - len(piece.lstrip())
    how_much_whitespace_at_the_end = len(piece) - len(piece.rstrip())
    return (
        span_start + how_much_whitespace_at_the_front,
        span_end - how_much_whitespace_at_the_end,
    )


def split_region_into_paragraphs(
    page_text: str, region_start: int, region_end: int
) -> list[tuple[int, int]]:
    """
    Cut a region at blank lines, returning a span for each paragraph.

    Blank lines are meaningful here rather than arbitrary: Step 1 used them to
    separate the visual blocks it worked so hard to group correctly. Cutting
    anywhere else would undo that work — slicing through a table row, or
    separating a number from its label.
    """
    region_text = page_text[region_start:region_end]

    paragraph_spans = []
    position_within_region = 0

    for piece in region_text.split("\n\n"):
        piece_start = region_start + position_within_region
        piece_end = piece_start + len(piece)

        # Two characters for the "\n\n" we just split on.
        position_within_region += len(piece) + 2

        trimmed_start, trimmed_end = trim_a_span(page_text, piece_start, piece_end)
        if trimmed_end > trimmed_start:
            paragraph_spans.append((trimmed_start, trimmed_end))

    return paragraph_spans


def split_one_paragraph_at_sentences(
    page_text: str, paragraph_start: int, paragraph_end: int
) -> list[tuple[int, int]]:
    """Break a single over-long paragraph into sentence-sized spans."""
    paragraph_text = page_text[paragraph_start:paragraph_end]

    sentence_spans = []
    position_within_paragraph = 0

    for sentence in SENTENCE_ENDING_PATTERN.split(paragraph_text):
        sentence_start = paragraph_start + position_within_paragraph
        sentence_end = sentence_start + len(sentence)

        # Step past the sentence and whatever whitespace followed it. Measuring
        # from the real text rather than assuming one space keeps the offsets
        # honest when a sentence ends at a line break.
        position_within_paragraph += len(sentence)
        while (
            paragraph_start + position_within_paragraph < paragraph_end
            and page_text[paragraph_start + position_within_paragraph].isspace()
        ):
            position_within_paragraph += 1

        trimmed_start, trimmed_end = trim_a_span(page_text, sentence_start, sentence_end)
        if trimmed_end > trimmed_start:
            sentence_spans.append((trimmed_start, trimmed_end))

    return sentence_spans


def count_words(some_text: str) -> int:
    return len(some_text.split())


def break_up_any_oversized_paragraphs(
    page_text: str, paragraph_spans: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Replace any paragraph that is too big to keep whole with its sentences."""
    resulting_spans = []
    for span_start, span_end in paragraph_spans:
        how_many_words = count_words(page_text[span_start:span_end])
        if how_many_words <= LONGEST_A_SINGLE_PARAGRAPH_MAY_BE:
            resulting_spans.append((span_start, span_end))
            continue
        resulting_spans.extend(
            split_one_paragraph_at_sentences(page_text, span_start, span_end)
        )
    return resulting_spans


def group_spans_into_chunks(
    page_text: str, spans: list[tuple[int, int]], target_words: int
) -> list[tuple[int, int]]:
    """
    Gather whole pieces together until a chunk is about the target size.

    Pieces are never split here, only grouped, so a chunk always begins and
    ends on a boundary the document itself provided.

    Because a chunk runs from the start of its first piece to the end of its
    last, the whitespace between pieces stays inside it. That is deliberate:
    the chunk is then one continuous slice of the page, and its two offsets
    describe it completely.
    """
    chunk_spans = []

    chunk_start = None
    chunk_end = None
    words_in_the_current_chunk = 0

    for span_start, span_end in spans:
        words_in_this_piece = count_words(page_text[span_start:span_end])

        if chunk_start is None:
            chunk_start, chunk_end = span_start, span_end
            words_in_the_current_chunk = words_in_this_piece
            continue

        would_overflow = (
            words_in_the_current_chunk + words_in_this_piece > target_words
        )
        if would_overflow:
            chunk_spans.append((chunk_start, chunk_end))
            chunk_start, chunk_end = span_start, span_end
            words_in_the_current_chunk = words_in_this_piece
            continue

        chunk_end = span_end
        words_in_the_current_chunk += words_in_this_piece

    if chunk_start is not None:
        chunk_spans.append((chunk_start, chunk_end))

    return chunk_spans


# ===========================================================================
# 3. The context header
# ===========================================================================

def build_context_header(
    section_heading: str,
    footnotes_on_this_page: dict,
    chunk_text: str,
    chunk_kind: str,
) -> str:
    """
    Assemble the context a passage needs in order to be understood alone.

    Two ingredients: what section of the document this is, and the bodies of
    the footnotes this passage actually refers to.

    Only the footnotes it refers to. A page may carry five; a passage citing
    "(1,5)" gets one and five. Handing over all of them would let the model
    attach a scope note belonging to a different number entirely.

    Deliberately NOT included: the filename. It would be easy to pass
    "annual-report-fy24" along and let the model infer a period from it, and
    that is precisely the habit we do not want. A qualifier that the document
    did not state must stay empty — a guessed period invents contradictions
    that were never there.
    """
    header_lines = []

    if section_heading:
        header_lines.append(f"[SECTION] {section_heading}")

    # A chunk that IS the footnotes does not need the footnotes explaining
    # to it.
    if chunk_kind != "footnotes" and footnotes_on_this_page:
        markers_this_chunk_refers_to = find_footnote_markers_in_text(chunk_text)

        for marker, footnote_body in sorted(footnotes_on_this_page.items()):
            if not footnote_body:
                continue

            this_passage_refers_to_it = marker in markers_this_chunk_refers_to
            it_is_a_symbol_that_applies_to_the_whole_page = (
                marker in FOOTNOTE_MARKERS_THAT_ARE_SYMBOLS
            )

            if this_passage_refers_to_it or it_is_a_symbol_that_applies_to_the_whole_page:
                header_lines.append(f"[FOOTNOTE {marker}] {footnote_body}")

    return "\n".join(header_lines)


# ===========================================================================
# 4. Deciding whether a passage deserves an API call
# ===========================================================================

def decide_whether_a_chunk_is_worth_an_llm_call(chunk_text: str) -> tuple[bool, str]:
    """
    Return whether to spend a call on this passage, and why not if not.

    We are on a free tier with a daily cap, so a passage that plainly holds no
    comparable claim should not cost us one of our calls.

    The rule is deliberately generous, because the cost of the two mistakes is
    not equal. Skipping a passage that held a fact loses that fact forever.
    Sending a passage that held nothing costs one call. So anything with a
    number in it goes, and anything with named things in it goes.

    Whatever we skip is recorded rather than quietly dropped, so the honest
    statement is "we chose not to spend a call on these, by this rule" instead
    of silence.
    """
    if count_words(chunk_text) < FEWEST_WORDS_WORTH_AN_LLM_CALL:
        return False, "too short to hold a claim"

    if ANY_DIGIT_PATTERN.search(chunk_text):
        return True, ""

    how_many_capitalised_words = len(CAPITALISED_WORD_PATTERN.findall(chunk_text))
    if how_many_capitalised_words >= FEWEST_CAPITALISED_WORDS_WORTH_AN_LLM_CALL:
        return True, ""

    return False, "no numbers and nothing named"


# ===========================================================================
# 5. Building every chunk of one page
# ===========================================================================

def build_chunks_for_one_page(page_row: dict) -> list[dict]:
    """Turn one stored page into its chunk rows, ready for the database."""
    document_id = page_row["doc_id"]
    page_number = page_row["page_no"]
    page_text = page_row["raw_text"] or ""
    section_heading = page_row["section_heading"] or ""
    footnotes_on_this_page = page_row["footnotes"] or {}

    if not page_text.strip():
        return []

    regions = find_the_three_regions(page_text)

    chunk_rows = []
    position_on_this_page = 0

    for region_name in ("prose", "table", "footnotes"):
        region_span = regions[region_name]
        if region_span is None:
            continue

        region_start, region_end = region_span
        paragraph_spans = split_region_into_paragraphs(page_text, region_start, region_end)
        paragraph_spans = break_up_any_oversized_paragraphs(page_text, paragraph_spans)

        chunk_spans = group_spans_into_chunks(
            page_text, paragraph_spans, TARGET_WORDS_PER_CHUNK
        )

        for chunk_start, chunk_end in chunk_spans:
            chunk_text = page_text[chunk_start:chunk_end]

            context_header = build_context_header(
                section_heading=section_heading,
                footnotes_on_this_page=footnotes_on_this_page,
                chunk_text=chunk_text,
                chunk_kind=region_name,
            )

            worth_a_call, reason_it_is_not = decide_whether_a_chunk_is_worth_an_llm_call(
                chunk_text
            )

            chunk_rows.append({
                "chunk_id": f"{document_id}__p{page_number:04d}__c{position_on_this_page:02d}",
                "doc_id": document_id,
                "page_no": page_number,
                "kind": region_name,
                "char_start": chunk_start,
                "char_end": chunk_end,
                "context_header": context_header,
                "text": chunk_text,
                "was_sent_to_llm": worth_a_call,
            })
            position_on_this_page += 1

            if not worth_a_call:
                database.record_failure(
                    stage="chunk",
                    kind="skipped_by_prefilter",
                    detail=f"{reason_it_is_not}: {chunk_text[:120]!r}",
                    doc_id=document_id,
                    page_no=page_number,
                )

    return chunk_rows


# ===========================================================================
# 6. Checking the offsets survived the round trip
# ===========================================================================

def verify_stored_chunks_can_be_found_in_their_pages(how_many: int) -> list[str]:
    """
    Read chunks back out of the database and confirm their offsets still work.

    This is the one hard check kept in this step, and it is deliberately done
    AFTER storage rather than in memory. Checking in memory would be circular —
    we cut the text out of the page using those very offsets, so of course they
    match. The question worth asking is whether they still match once the text
    has been through a database driver, an encoding, and a network.

    The failure this guards against is silent. A drifted offset does not
    crash. It makes "show me the source" land on the wrong sentence, and you
    would not notice until the interface existed in Step 10, with everything
    already built on top.
    """
    sampled_rows = database.fetch_all_rows(
        """
        SELECT c.chunk_id, c.char_start, c.char_end, c.text, p.raw_text
        FROM chunks c
        JOIN pages p ON p.doc_id = c.doc_id AND p.page_no = c.page_no
        ORDER BY random()
        LIMIT %s
        """,
        (how_many,),
    )

    complaints = []
    for row in sampled_rows:
        what_the_offsets_point_at = row["raw_text"][row["char_start"]:row["char_end"]]
        if what_the_offsets_point_at != row["text"]:
            complaints.append(
                f"{row['chunk_id']}: offsets {row['char_start']}-{row['char_end']} "
                f"point at {what_the_offsets_point_at[:60]!r} "
                f"but the chunk holds {row['text'][:60]!r}"
            )
    return complaints


# ===========================================================================
# 7. Running it
# ===========================================================================

def build_chunks_for_one_document(document_row: dict) -> dict:
    """Chunk every page of one document and save the results."""
    document_id = document_row["doc_id"]

    page_rows = database.fetch_all_rows(
        """
        SELECT doc_id, page_no, raw_text, section_heading, footnotes
        FROM pages WHERE doc_id = %s ORDER BY page_no
        """,
        (document_id,),
    )

    # Re-chunking replaces what was there. The offsets are only meaningful
    # against the page text they were cut from, so leaving old chunks beside
    # new ones would mix two sets of coordinates.
    database.execute_sql("DELETE FROM chunks WHERE doc_id = %s", (document_id,))
    database.execute_sql(
        "DELETE FROM failures WHERE doc_id = %s AND stage = 'chunk'", (document_id,)
    )

    all_chunk_rows = []
    for page_row in page_rows:
        all_chunk_rows.extend(build_chunks_for_one_page(page_row))

    database.insert_rows_in_batches("chunks", all_chunk_rows)

    words_per_chunk = [count_words(row["text"]) for row in all_chunk_rows]
    chunks_by_kind: dict[str, int] = {}
    for row in all_chunk_rows:
        chunks_by_kind[row["kind"]] = chunks_by_kind.get(row["kind"], 0) + 1

    return {
        "filename": document_row["filename"],
        "pages": len(page_rows),
        "chunks": len(all_chunk_rows),
        "chunks_by_kind": chunks_by_kind,
        "chunks_worth_a_call": sum(1 for r in all_chunk_rows if r["was_sent_to_llm"]),
        "median_words": int(statistics.median(words_per_chunk)) if words_per_chunk else 0,
        "largest_chunk_words": max(words_per_chunk) if words_per_chunk else 0,
        "chunks_with_footnote_context": sum(
            1 for r in all_chunk_rows if "[FOOTNOTE" in r["context_header"]
        ),
    }


def print_a_sample_chunk(chunk_row: dict, why_this_one: str) -> None:
    """Print one chunk exactly as the LLM will receive it."""
    print()
    print("=" * 78)
    print(f"SAMPLE CHUNK — {why_this_one}")
    print(f"{chunk_row['chunk_id']}   page {chunk_row['page_no']}   "
          f"kind={chunk_row['kind']}   "
          f"chars {chunk_row['char_start']}-{chunk_row['char_end']}")
    print("=" * 78)
    if chunk_row["context_header"]:
        print("--- CONTEXT HEADER (added by us, not from the document) ---")
        print(chunk_row["context_header"])
        print("--- PASSAGE (the document's own words) ---")
    else:
        print("--- PASSAGE (no context header for this one) ---")
    passage = chunk_row["text"]
    if len(passage) > 1400:
        passage = passage[:1400] + "\n... (truncated for display)"
    print(passage)


def main() -> int:
    print("=" * 78)
    print("STEP 2 — cutting pages into chunks")
    print("=" * 78)

    config.stop_unless_these_settings_are_filled_in(["DATABASE_URL"])

    document_rows = database.fetch_all_rows(
        "SELECT doc_id, filename FROM documents ORDER BY filename"
    )
    if not document_rows:
        print("No documents in the database. Run step_01_ingest_pdf first.")
        return 1

    print(f"\nTarget chunk size: {TARGET_WORDS_PER_CHUNK} words")

    all_summaries = []
    for document_row in document_rows:
        summary = build_chunks_for_one_document(document_row)
        all_summaries.append(summary)

        print(f"\n{summary['filename']}")
        print(f"  pages                    : {summary['pages']}")
        print(f"  chunks created           : {summary['chunks']}")
        print(f"  by kind                  : {summary['chunks_by_kind']}")
        print(f"  median words per chunk   : {summary['median_words']}")
        print(f"  largest chunk            : {summary['largest_chunk_words']} words")
        print(f"  carrying footnote context: {summary['chunks_with_footnote_context']}")
        print(f"  worth an LLM call        : {summary['chunks_worth_a_call']}")

    total_chunks = sum(s["chunks"] for s in all_summaries)
    total_worth_a_call = sum(s["chunks_worth_a_call"] for s in all_summaries)
    total_with_footnotes = sum(s["chunks_with_footnote_context"] for s in all_summaries)

    print()
    print("=" * 78)
    print("THE NUMBER THAT DECIDES OUR API BUDGET")
    print("=" * 78)
    print(f"  chunks created                : {total_chunks}")
    print(f"  pre-filter skipped            : {total_chunks - total_worth_a_call}")
    print(f"  LLM CALLS ONE FULL RUN COSTS  : {total_worth_a_call}")
    print(f"  chunks carrying footnote scope: {total_with_footnotes}")

    print()
    print("=" * 78)
    print(f"VERIFYING OFFSETS ON {HOW_MANY_CHUNKS_TO_VERIFY} RANDOM STORED CHUNKS")
    print("=" * 78)
    complaints = verify_stored_chunks_can_be_found_in_their_pages(
        HOW_MANY_CHUNKS_TO_VERIFY
    )
    if complaints:
        print(f"  {len(complaints)} PROBLEMS FOUND:")
        for complaint in complaints[:10]:
            print(f"    {complaint}")
    else:
        print("  all sampled chunks can be found again at their stored offsets.")

    # Show the passage that motivated the whole context-header design, so it
    # can be checked by eye.
    chunk_that_needs_its_footnotes = database.fetch_one_row(
        """
        SELECT chunk_id, page_no, kind, char_start, char_end, context_header, text
        FROM chunks
        WHERE context_header LIKE %s
        ORDER BY length(context_header) DESC
        LIMIT 1
        """,
        # The pattern travels as a parameter rather than inside the query text.
        # psycopg scans the query for placeholders and reads a literal "%[" as
        # a malformed one, so a LIKE pattern written inline fails to parse.
        ("%[FOOTNOTE%",),
    )
    if chunk_that_needs_its_footnotes:
        print_a_sample_chunk(
            chunk_that_needs_its_footnotes,
            "the one carrying the most footnote context",
        )

    a_table_chunk = database.fetch_one_row(
        """
        SELECT chunk_id, page_no, kind, char_start, char_end, context_header, text
        FROM chunks WHERE kind = 'table' ORDER BY length(text) DESC LIMIT 1
        """
    )
    if a_table_chunk:
        print_a_sample_chunk(a_table_chunk, "the largest table chunk")

    print()
    if complaints:
        print("FINISHED WITH PROBLEMS — the offset check failed. Read the output above.")
        return 1

    print("SUCCESS — chunks built and their offsets verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
