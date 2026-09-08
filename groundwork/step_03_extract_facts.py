"""
STEP 3 — read each passage and pull structured claims out of it.

Run it with:
    .\\.venv\\Scripts\\python.exe -m groundwork.step_03_extract_facts --document annual
    .\\.venv\\Scripts\\python.exe -m groundwork.step_03_extract_facts            (all)

Useful flags:
    --document <text>     only documents whose filename contains this
    --max-requests <n>    stop after this many API requests
    --dry-run             build and print one request, send nothing

---------------------------------------------------------------------------
WHY THE PROMPT NEVER MENTIONS OUR DOCUMENTS
---------------------------------------------------------------------------
The brief forbids document-specific rules and says the system will be tested
on other PDFs. So the prompt defines a fact in general terms — a claim about a
named thing — and never mentions revenue, headcount, directors or logistics.

Its worked examples are built from invented companies for the same reason.
Using real passages from our three PDFs would quietly teach the model the
answers for this corpus, and we would learn nothing about how it behaves on
the document a reviewer uploads.

---------------------------------------------------------------------------
THE SINGLE MOST IMPORTANT LINE IN THE PROMPT
---------------------------------------------------------------------------
    If a field is not stated, return null. Never infer or guess.

A missing qualifier is correct and harmless. An invented one manufactures
contradictions that were never in the documents — and inventing them is easy,
because a model that has seen a thousand annual reports "knows" that revenue
figures usually cover a financial year and will happily supply one.

Everything Steps 1 and 2 did to capture footnotes is wasted if the model starts
filling in qualifiers from memory rather than from the page.

---------------------------------------------------------------------------
BATCHING, AND WHY THE ORDER IS FIXED
---------------------------------------------------------------------------
One request carries several passages, which takes 347 chunks down to roughly
120 requests and brings a full run inside the free tier's daily allowance.

The packing is deterministic — passages sorted by id, filled in fixed order.
This is not tidiness. The cache key is a hash of the prompt text, so if the
grouping shifted between runs every prompt would be new, every key would miss,
and the cache would never help us again.

The risk batching introduces is mis-attribution: the model tags a fact with the
wrong passage. Step 4 catches that, because the quoted evidence will not be
found in the passage the fact claims to come from.
"""

import concurrent.futures
import json
import sys

from pydantic import ValidationError

from groundwork.shared import config, database, llm_client
from groundwork.shared.models import ExtractedFact


# ===========================================================================
# Versioning
#
# Bump this whenever the prompt text OR the fact schema changes. It is part of
# the cache key, so without bumping it an improved prompt would keep serving
# answers produced by the old one, and you would conclude the improvement did
# nothing.
# ===========================================================================

EXTRACTION_PROMPT_VERSION = "extract-v2"


# ===========================================================================
# Tuning numbers
# ===========================================================================

# How many words of passage text to put in one request.
#
# These were 2500 and 8. A single request then produced 143,000 characters of
# JSON and came back malformed — the answer was longer than the model could
# finish. The limit that bites is not how much we send, it is how much comes
# back, and a dense table page yields far more facts per word than prose does.
#
# Smaller requests cost more of them, which we can afford: the daily allowance
# on Flash Lite is 500, and a full run needs well under a third of that.
MOST_WORDS_IN_ONE_REQUEST = 1200

# Never put more than this many passages in one request, however short they
# are. Beyond a handful, mis-attribution becomes more likely.
MOST_PASSAGES_IN_ONE_REQUEST = 4

# A JSON answer longer than this is almost certainly going to be cut off
# mid-structure. Recognising that gives a far more useful failure message than
# "invalid JSON at character 143127".
LENGTH_AT_WHICH_AN_ANSWER_IS_PROBABLY_TRUNCATED = 100_000

# What our own pipeline already knows about where a passage came from.
#
# Step 2 separated each page into prose, tables and footnotes, so for two of
# those three we do not need to ask the model at all — and should not. Flash
# Lite classified every figure label as "prose", which would have given
# chart-derived numbers the confidence of a written sentence.
#
# The rule: where we know, we decide. Where we do not — within the prose
# region, a sentence and a figure label look alike to us — the model's answer
# stands.
SOURCE_KIND_WE_CAN_DECIDE_OURSELVES = {
    "table": "table_row",
    "footnotes": "footnote",
}

# How much to trust a fact based on where it came from. These are not equal
# sources: a sentence carries its own meaning, a table row depends on our
# having paired it with the right headers, and a figure label was matched to
# its number by position on a page, which is a guess.
CONFIDENCE_BY_SOURCE_KIND = {
    "prose": 0.90,
    "footnote": 0.85,
    "table_row": 0.75,
    "heading": 0.60,
    "figure_label": 0.50,
}
CONFIDENCE_WHEN_SOURCE_IS_UNKNOWN = 0.50


# ===========================================================================
# The prompt
# ===========================================================================

EXTRACTION_INSTRUCTIONS = """\
You extract structured claims from passages of a document.

WHAT COUNTS AS A FACT
A fact is a statement in the passage asserting a value, a status, a date, or a
relationship about a named thing. If a passage asserts nothing about anything
nameable, it contains no facts, and returning none for it is the right answer.

FIELDS TO FILL FOR EACH FACT

  chunk_id      the id of the passage it came from, copied exactly
  subject       the named thing the claim is about, as the passage names it.
                Use null if the passage is plainly about the document's own
                subject without naming it, as a page of highlights usually is.
  attribute     the property being claimed, in the passage's own words
  value_raw     the value exactly as written, keeping commas, symbols and any
                greater-than sign
  value_kind    "number", "date" or "text"
  unit          the unit if stated: people, tonnes, percent, sq ft, crore
  currency      the currency code if the value is money: INR, USD, GBP
  period_text   the span of time the claim covers, copied as written
  as_of_text    the single date at which the claim holds, copied as written
  scope         any stated qualification limiting what the value covers
  evidence      the exact text stating this fact, copied character for character
  source_kind   prose, table_row, footnote, heading, or figure_label

THE RULES

1. IF A FIELD IS NOT STATED, RETURN null. Never infer, calculate, or supply a
   value from your own knowledge. A missing qualifier is correct. An invented
   one is a serious error, because it creates a disagreement that the documents
   never contained.

2. evidence must appear in the passage character for character. Do not tidy
   spacing, expand abbreviations, correct spelling, or shorten. If you cannot
   copy it exactly, do not report the fact at all.

3. FOR A VALUE FROM A TABLE ROW, evidence must be THE WHOLE ROW exactly as
   printed — every separator and every other value in it included.

   Do NOT build a shorter quote by joining the row label to the one value you
   mean. That combined string does not exist anywhere in the document, so it
   cannot be checked, and the fact will be thrown away.

   You do not need the quote to single out your value. Which value you mean is
   already recorded in `attribute`, `period_text` and `as_of_text`. The job of
   `evidence` is only to point at real text on a real page.

4. The CONTEXT block is not part of the passage. Use it to fill period_text,
   as_of_text and scope. Never quote evidence from it, and never extract facts
   from it.

5. One row of a table stating several values produces several facts, one per
   value, each with its own column heading as its period or as-of date. All of
   them share the same evidence: that entire row.

6. Ignore page numbers, navigation, and anything that asserts nothing.

Return every fact from every passage in one flat list.
"""


EXTRACTION_EXAMPLES = """\
WORKED EXAMPLES

These use invented documents. Follow their shape, not their subject matter.

--- Example A: prose with a period and a scope ---

=== PASSAGE ex_a ===
CONTEXT:
[SECTION] Financial Review
TEXT:
Northwind Freight Limited reported revenue of GBP 412.6 million for the year
ended 31 December 2023 on a consolidated basis.

CORRECT ANSWER:
[{"chunk_id":"ex_a","subject":"Northwind Freight Limited","attribute":"revenue",
"value_raw":"412.6","value_kind":"number","unit":"million","currency":"GBP",
"period_text":"year ended 31 December 2023","as_of_text":null,
"scope":"consolidated",
"evidence":"Northwind Freight Limited reported revenue of GBP 412.6 million for the year\\nended 31 December 2023 on a consolidated basis.",
"source_kind":"prose"}]

--- Example B: a figure whose qualifiers live in footnotes ---

=== PASSAGE ex_b ===
CONTEXT:
[SECTION] Our Year In Numbers
[FOOTNOTE 2] As at 30 June 2024
[FOOTNOTE 7] Counts full-time staff and seasonal contractors
TEXT:
4,182
(2,7)
People employed

CORRECT ANSWER:
[{"chunk_id":"ex_b","subject":null,"attribute":"people employed",
"value_raw":"4,182","value_kind":"number","unit":"people","currency":null,
"period_text":null,"as_of_text":"30 June 2024",
"scope":"Counts full-time staff and seasonal contractors",
"evidence":"4,182","source_kind":"figure_label"}]

Note that subject is null because the passage never names the company, and that
the date and the scope came from the CONTEXT block while the evidence came only
from the passage.

--- Example C: one table row, two values, two facts ---

=== PASSAGE ex_c ===
CONTEXT:
[SECTION] Balance Sheet
TEXT:
Assets (EUR thousands) | 2022 | 2023
Total assets | 88,140 | 91,502

CORRECT ANSWER:
[{"chunk_id":"ex_c","subject":null,"attribute":"total assets",
"value_raw":"88,140","value_kind":"number","unit":"thousands","currency":"EUR",
"period_text":null,"as_of_text":"2022","scope":null,
"evidence":"Total assets | 88,140 | 91,502","source_kind":"table_row"},
{"chunk_id":"ex_c","subject":null,"attribute":"total assets",
"value_raw":"91,502","value_kind":"number","unit":"thousands","currency":"EUR",
"period_text":null,"as_of_text":"2023","scope":null,
"evidence":"Total assets | 88,140 | 91,502","source_kind":"table_row"}]

Both facts quote THE SAME WHOLE ROW. Which value each one means is carried by
as_of_text, not by the quote.

WRONG ANSWER for the same passage — do not do this:

    "evidence":"Total assets\\n91,502"
    "evidence":"Total assets 91,502"
    "evidence":"91,502"

The first two join the label to one value; that combined string appears
nowhere in the document. The third is too small to point at anything, since
the same digits may appear many times on a page. All three are thrown away.

--- Example D: a status, not a number ---

=== PASSAGE ex_d ===
CONTEXT:
[SECTION] Board of Directors
TEXT:
Priya Raman (DIN: 07781234) resigned as an Independent Director with effect
from 12 August 2023.

CORRECT ANSWER:
[{"chunk_id":"ex_d","subject":"Priya Raman","attribute":"directorship status",
"value_raw":"resigned as an Independent Director","value_kind":"text",
"unit":null,"currency":null,"period_text":null,
"as_of_text":"12 August 2023","scope":null,
"evidence":"Priya Raman (DIN: 07781234) resigned as an Independent Director with effect\\nfrom 12 August 2023.",
"source_kind":"prose"}]

--- Example E: a passage with nothing in it ---

=== PASSAGE ex_e ===
TEXT:
Contents
Corporate Overview .......... 2
Statutory Reports ........... 14

CORRECT ANSWER:
[]

Returning nothing is the right answer here. Do not invent a fact to fill space.
"""


def build_one_request_prompt(chunk_rows: list[dict]) -> str:
    """Assemble the full prompt for one request carrying several passages."""
    parts = [EXTRACTION_INSTRUCTIONS, "", EXTRACTION_EXAMPLES, ""]
    parts.append("NOW DO THE SAME FOR THESE PASSAGES.")
    parts.append("")

    for chunk_row in chunk_rows:
        parts.append(f"=== PASSAGE {chunk_row['chunk_id']} ===")
        if chunk_row["context_header"]:
            parts.append("CONTEXT:")
            parts.append(chunk_row["context_header"])
        parts.append("TEXT:")
        parts.append(chunk_row["text"])
        parts.append("")

    return "\n".join(parts)


# ===========================================================================
# Packing passages into requests
# ===========================================================================

def count_words(some_text: str) -> int:
    return len(some_text.split())


def pack_chunks_into_requests(chunk_rows: list[dict]) -> list[list[dict]]:
    """
    Group passages into requests, always the same way for the same input.

    Determinism is the whole point. The cache key is a hash of the prompt text,
    so if today's grouping differs from yesterday's, every prompt is new,
    every cache lookup misses, and a re-run costs a full day's allowance again.

    Sorting by chunk id guarantees the same grouping every time — and because
    ids carry document, page and position, it also keeps neighbouring passages
    together, which is what you want when a table spills across two of them.
    """
    chunks_in_a_fixed_order = sorted(chunk_rows, key=lambda row: row["chunk_id"])

    requests = []
    passages_for_this_request = []
    words_in_this_request = 0

    for chunk_row in chunks_in_a_fixed_order:
        words_in_this_passage = count_words(chunk_row["text"])

        request_is_full = passages_for_this_request and (
            words_in_this_request + words_in_this_passage > MOST_WORDS_IN_ONE_REQUEST
            or len(passages_for_this_request) >= MOST_PASSAGES_IN_ONE_REQUEST
        )
        if request_is_full:
            requests.append(passages_for_this_request)
            passages_for_this_request = []
            words_in_this_request = 0

        passages_for_this_request.append(chunk_row)
        words_in_this_request += words_in_this_passage

    if passages_for_this_request:
        requests.append(passages_for_this_request)

    return requests


# ===========================================================================
# Turning one answer into fact rows
# ===========================================================================

def parse_the_answer_into_facts(answer_text: str) -> tuple[list[ExtractedFact], str | None]:
    """
    Read the model's JSON answer. Returns the facts, and a complaint if any.

    Two shapes are accepted because providers differ: a bare list, or an object
    with a "facts" key. Being lenient here costs three lines and avoids losing
    a whole request to a wrapper we did not expect.
    """
    try:
        parsed = json.loads(answer_text)
    except json.JSONDecodeError as problem:
        if len(answer_text) > LENGTH_AT_WHICH_AN_ANSWER_IS_PROBABLY_TRUNCATED:
            return [], (
                f"answer was {len(answer_text):,} characters and did not parse — "
                "almost certainly cut off before it finished. Send fewer "
                f"passages per request. ({problem})"
            )
        return [], f"answer was not valid JSON: {problem}"

    if isinstance(parsed, dict):
        parsed = parsed.get("facts", [])

    if not isinstance(parsed, list):
        return [], f"expected a list of facts, got {type(parsed).__name__}"

    facts = []
    complaints = []
    for position, raw_fact in enumerate(parsed):
        try:
            facts.append(ExtractedFact.model_validate(raw_fact))
        except ValidationError as problem:
            first_problem = problem.errors()[0] if problem.errors() else {}
            complaints.append(
                f"fact {position} rejected: "
                f"{first_problem.get('loc')} {first_problem.get('msg')}"
            )

    complaint_text = "; ".join(complaints) if complaints else None
    return facts, complaint_text


def turn_facts_into_database_rows(
    facts: list[ExtractedFact],
    chunk_rows_by_id: dict[str, dict],
) -> tuple[list[dict], list[str]]:
    """
    Convert validated facts into rows, dropping any that name a passage we did
    not send.

    A fact claiming to come from a passage that was not in the request is
    either a mis-attribution or an invention. Either way we cannot ground it,
    so it does not go in the database — it goes in the failures table where it
    can be counted and explained.
    """
    fact_rows = []
    complaints = []
    how_many_from_each_chunk: dict[str, int] = {}

    for fact in facts:
        chunk_row = chunk_rows_by_id.get(fact.chunk_id)
        if chunk_row is None:
            complaints.append(
                f"fact named passage {fact.chunk_id!r}, which was not in this request"
            )
            continue

        position_in_chunk = how_many_from_each_chunk.get(fact.chunk_id, 0)
        how_many_from_each_chunk[fact.chunk_id] = position_in_chunk + 1

        is_a_number = fact.value_kind == "number"

        # Where our own pipeline already knows the answer, use it instead of the
        # model's guess. A passage cut from the tables region IS a table row,
        # whatever the model calls it.
        source_kind = SOURCE_KIND_WE_CAN_DECIDE_OURSELVES.get(
            chunk_row["kind"], fact.source_kind
        )

        fact_rows.append({
            "fact_id": f"{fact.chunk_id}__f{position_in_chunk:02d}",
            "chunk_id": fact.chunk_id,
            "doc_id": chunk_row["doc_id"],
            "page_no": chunk_row["page_no"],

            "subject_raw": fact.subject,
            "attribute_raw": fact.attribute,

            "value_raw": fact.value_raw,
            "value_kind": fact.value_kind,
            # value_num is filled in by Step 5, which parses numbers properly.
            # Storing the raw text here keeps the un-interpreted version so our
            # parsing can always be checked against what the document said.
            "value_text": None if is_a_number else fact.value_raw,
            "unit": fact.unit,
            "currency": fact.currency,

            "period_raw": fact.period_text,
            "as_of_raw": fact.as_of_text,
            "scope_raw": fact.scope,

            "source_kind": source_kind,
            "confidence": CONFIDENCE_BY_SOURCE_KIND.get(
                source_kind, CONFIDENCE_WHEN_SOURCE_IS_UNKNOWN
            ),
            "evidence_text": fact.evidence,
            # grounded stays false until Step 4 proves the evidence is real.
            "grounded": False,
        })

    return fact_rows, complaints


# ===========================================================================
# Running one request
# ===========================================================================

def extract_facts_from_one_request(chunk_rows: list[dict]) -> dict:
    """Send one request and return its rows plus anything that went wrong."""
    prompt_text = build_one_request_prompt(chunk_rows)
    chunk_rows_by_id = {row["chunk_id"]: row for row in chunk_rows}

    try:
        answer_text = llm_client.ask_the_model(
            prompt_text,
            prompt_version=EXTRACTION_PROMPT_VERSION,
            expect_json=True,
            response_schema=list[ExtractedFact],
        )
    except Exception as problem:
        return {
            "fact_rows": [],
            "complaints": [f"request failed: {type(problem).__name__}: {problem}"],
            "chunk_ids": list(chunk_rows_by_id),
        }

    facts, parsing_complaint = parse_the_answer_into_facts(answer_text)
    fact_rows, attribution_complaints = turn_facts_into_database_rows(
        facts, chunk_rows_by_id
    )

    complaints = []
    if parsing_complaint:
        complaints.append(parsing_complaint)
    complaints.extend(attribution_complaints)

    return {
        "fact_rows": fact_rows,
        "complaints": complaints,
        "chunk_ids": list(chunk_rows_by_id),
    }


# ===========================================================================
# Running a whole document
# ===========================================================================

def extract_facts_for_one_document(
    document_row: dict, how_many_requests_at_most: int | None
) -> dict:
    """Extract every fact from one document and save them."""
    document_id = document_row["doc_id"]

    chunk_rows = database.fetch_all_rows(
        """
        SELECT chunk_id, doc_id, page_no, kind, context_header, text
        FROM chunks
        WHERE doc_id = %s AND was_sent_to_llm = TRUE
        ORDER BY chunk_id
        """,
        (document_id,),
    )

    requests = pack_chunks_into_requests(chunk_rows)
    if how_many_requests_at_most is not None:
        requests = requests[:how_many_requests_at_most]

    print(f"\n{document_row['filename']}")
    print(f"  passages to read : {len(chunk_rows)}")
    print(f"  requests to send : {len(requests)}")

    # Re-extracting replaces what was there. Fact ids are derived from passage
    # ids, so leaving old rows behind would mix results from two prompt
    # versions with no way to tell them apart.
    database.execute_sql("DELETE FROM facts WHERE doc_id = %s", (document_id,))
    database.execute_sql(
        "DELETE FROM failures WHERE doc_id = %s AND stage = 'extract'", (document_id,)
    )

    all_fact_rows = []
    all_complaints = []
    how_many_requests_finished = 0

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=config.LLM_CONCURRENT_REQUESTS
    ) as pool:
        for result in pool.map(extract_facts_from_one_request, requests):
            # Save each request's facts AS IT ARRIVES, not once the whole
            # document is finished.
            #
            # This is a project rule that this file originally broke. When a
            # run was interrupted part-way, the facts table had already been
            # cleared for this document but nothing had been written back, so
            # every completed request was thrown away. Writing as we go means
            # an interruption costs the request in flight and nothing else.
            database.insert_rows_in_batches(
                "facts", result["fact_rows"], skip_rows_that_already_exist=True
            )

            all_fact_rows.extend(result["fact_rows"])
            for complaint in result["complaints"]:
                all_complaints.append(complaint)
                database.record_failure(
                    stage="extract",
                    kind="extraction_problem",
                    detail=complaint[:1500],
                    doc_id=document_id,
                    chunk_id=result["chunk_ids"][0] if result["chunk_ids"] else None,
                )

            how_many_requests_finished += 1
            if how_many_requests_finished % 5 == 0:
                print(
                    f"    {how_many_requests_finished}/{len(requests)} requests, "
                    f"{len(all_fact_rows)} facts so far"
                )

    facts_by_source_kind: dict[str, int] = {}
    for row in all_fact_rows:
        kind = row["source_kind"]
        facts_by_source_kind[kind] = facts_by_source_kind.get(kind, 0) + 1

    return {
        "filename": document_row["filename"],
        "doc_id": document_id,
        "passages": len(chunk_rows),
        "requests": len(requests),
        "facts": len(all_fact_rows),
        "facts_by_source_kind": facts_by_source_kind,
        "with_a_period": sum(1 for r in all_fact_rows if r["period_raw"]),
        "with_an_as_of_date": sum(1 for r in all_fact_rows if r["as_of_raw"]),
        "with_a_scope": sum(1 for r in all_fact_rows if r["scope_raw"]),
        "with_a_named_subject": sum(1 for r in all_fact_rows if r["subject_raw"]),
        "complaints": all_complaints,
    }


# ===========================================================================
# Running it
# ===========================================================================

def print_some_facts_to_read(document_id: str, how_many: int = 12) -> None:
    """
    Print a spread of extracted facts for reading by eye.

    There is no test suite, and no answer key beyond the manual fact list. This
    output is how the prompt gets judged, so it deliberately shows the
    qualifiers and the evidence rather than just counting rows.
    """
    rows = database.fetch_all_rows(
        """
        SELECT page_no, subject_raw, attribute_raw, value_raw, unit, currency,
               period_raw, as_of_raw, scope_raw, source_kind, evidence_text
        FROM facts
        WHERE doc_id = %s
        ORDER BY random()
        LIMIT %s
        """,
        (document_id, how_many),
    )

    print()
    print("=" * 78)
    print(f"{len(rows)} RANDOM FACTS — read these against the real pages")
    print("=" * 78)

    for row in rows:
        subject = row["subject_raw"] or "(the document's own subject)"
        value = row["value_raw"]
        if row["unit"]:
            value += f" {row['unit']}"
        if row["currency"]:
            value = f"{row['currency']} {value}"

        print(f"\np{row['page_no']}  [{row['source_kind']}]")
        print(f"  {subject}  —  {row['attribute_raw']}  =  {value}")

        qualifiers = []
        if row["period_raw"]:
            qualifiers.append(f"period: {row['period_raw']}")
        if row["as_of_raw"]:
            qualifiers.append(f"as of: {row['as_of_raw']}")
        if row["scope_raw"]:
            qualifiers.append(f"scope: {row['scope_raw']}")
        print(f"  {' | '.join(qualifiers) if qualifiers else 'no qualifiers stated'}")

        evidence = (row["evidence_text"] or "").replace("\n", " ⏎ ")
        if len(evidence) > 150:
            evidence = evidence[:147] + "..."
        print(f"  evidence: {evidence!r}")


def main() -> int:
    command_line_arguments = sys.argv[1:]

    which_document = None
    if "--document" in command_line_arguments:
        which_document = command_line_arguments[
            command_line_arguments.index("--document") + 1
        ]

    how_many_requests_at_most = None
    if "--max-requests" in command_line_arguments:
        how_many_requests_at_most = int(
            command_line_arguments[command_line_arguments.index("--max-requests") + 1]
        )

    it_is_a_dry_run = "--dry-run" in command_line_arguments

    print("=" * 78)
    print("STEP 3 — extracting facts")
    print("=" * 78)

    config.stop_unless_these_settings_are_filled_in(["DATABASE_URL", "GOOGLE_API_KEY"])
    llm_client.reset_the_counters()

    print(f"\nmodel          : {config.GEMINI_MODEL}")
    print(f"prompt version : {EXTRACTION_PROMPT_VERSION}")
    print(f"cache          : {llm_client.describe_the_cache()}")

    if which_document:
        document_rows = database.fetch_all_rows(
            "SELECT doc_id, filename FROM documents WHERE filename ILIKE %s ORDER BY filename",
            (f"%{which_document}%",),
        )
    else:
        document_rows = database.fetch_all_rows(
            "SELECT doc_id, filename FROM documents ORDER BY filename"
        )

    if not document_rows:
        print(f"\nNo documents matched {which_document!r}.")
        return 1

    if it_is_a_dry_run:
        chunk_rows = database.fetch_all_rows(
            """
            SELECT chunk_id, doc_id, page_no, kind, context_header, text
            FROM chunks WHERE doc_id = %s AND was_sent_to_llm = TRUE
            ORDER BY chunk_id
            """,
            (document_rows[0]["doc_id"],),
        )
        requests = pack_chunks_into_requests(chunk_rows)
        print(f"\nDRY RUN — {len(chunk_rows)} passages would become "
              f"{len(requests)} requests.")
        print("\nThe first request would look like this:\n")
        print("-" * 78)
        print(build_one_request_prompt(requests[0]))
        print("-" * 78)
        return 0

    all_summaries = []
    for document_row in document_rows:
        summary = extract_facts_for_one_document(document_row, how_many_requests_at_most)
        all_summaries.append(summary)

        print(f"  facts extracted  : {summary['facts']}")
        print(f"  by source        : {summary['facts_by_source_kind']}")
        print(f"  with a period    : {summary['with_a_period']}")
        print(f"  with an as-of    : {summary['with_an_as_of_date']}")
        print(f"  with a scope     : {summary['with_a_scope']}")
        print(f"  naming a subject : {summary['with_a_named_subject']}")
        if summary["complaints"]:
            print(f"  PROBLEMS         : {len(summary['complaints'])}")
            for complaint in summary["complaints"][:5]:
                print(f"     {complaint[:150]}")

    print()
    print("=" * 78)
    print("WHAT THIS RUN COST")
    print("=" * 78)
    print(f"  {llm_client.describe_what_this_run_cost()}")
    print(f"  cache now: {llm_client.describe_the_cache()}")

    if all_summaries:
        print_some_facts_to_read(all_summaries[0]["doc_id"])

    total_facts = sum(s["facts"] for s in all_summaries)
    total_problems = sum(len(s["complaints"]) for s in all_summaries)

    print()
    if total_facts == 0:
        print("NOTHING EXTRACTED — read the problems above.")
        return 1
    print(f"Extracted {total_facts} facts with {total_problems} problems.")
    print("Nothing is grounded yet — Step 4 proves the evidence is real.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
