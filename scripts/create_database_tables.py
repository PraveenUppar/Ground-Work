"""
Creates the eight tables Ground Work needs.

Run it with:

    .\\.venv\\Scripts\\python.exe scripts\\create_database_tables.py

Safe to run as many times as you like. Every statement uses
"CREATE ... IF NOT EXISTS", so running it again on an existing database changes
nothing and drops no data.
"""

import sys
from pathlib import Path

# Allow running this file directly (python scripts/create_database_tables.py)
# by putting the project root on the import path first.
PROJECT_ROOT_FOLDER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT_FOLDER))

from groundwork.shared import config, database


# ---------------------------------------------------------------------------
# The schema
#
# A note on foreign keys, because the choice here is deliberate.
#
# We use them where the insertion order is obvious and fixed: a chunk cannot
# exist before its document, so chunks.doc_id points at documents.doc_id and
# the database enforces it.
#
# We deliberately DO NOT use them for facts.entity_id and
# facts.attribute_family. Those two columns start out empty and are filled in
# later, during normalisation, once we have seen enough facts to work out which
# names refer to the same entity. A foreign key there would force an awkward
# ordering on a step that genuinely runs second.
# ---------------------------------------------------------------------------

SQL_TO_CREATE_ALL_TABLES = """

-- ===========================================================================
-- documents : one row per uploaded PDF
-- ===========================================================================
CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,
    filename        TEXT        NOT NULL,

    -- A fingerprint of the file's bytes. Marked UNIQUE, which gives us upload
    -- idempotency for free: uploading the same file twice is rejected by the
    -- database, so we can skip straight to the results we already have.
    sha256          TEXT        NOT NULL UNIQUE,

    page_count      INTEGER,

    -- 'uploaded' -> 'ingested' -> 'chunked' -> 'extracted' -> 'done'
    -- Lets the interface show progress, and lets a crashed run be resumed.
    status          TEXT        NOT NULL DEFAULT 'uploaded',

    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ===========================================================================
-- pages : the raw text of each page, kept so the interface can show a source
--         page without reopening the PDF file
-- ===========================================================================
CREATE TABLE IF NOT EXISTS pages (
    doc_id      TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page_no     INTEGER NOT NULL,
    raw_text    TEXT,

    -- The biggest text near the top of the page. Step 3 pastes this onto every
    -- chunk cut from this page, so a passage read on its own still knows what
    -- part of the document it belongs to.
    section_heading TEXT NOT NULL DEFAULT '',

    -- Footnote marker -> footnote body, for example {"1": "As of 31 March 2024"}.
    -- JSONB because the number of footnotes varies wildly from page to page.
    footnotes   JSONB   NOT NULL DEFAULT '{}',

    -- A page is identified by its document plus its number, so those two
    -- columns together are the primary key.
    PRIMARY KEY (doc_id, page_no)
);


-- ===========================================================================
-- chunks : the passages we actually send to the LLM
-- ===========================================================================
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT    PRIMARY KEY,
    doc_id          TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page_no         INTEGER NOT NULL,

    -- 'prose' or 'table'. Tables are extracted by a different library and
    -- flattened differently, and their facts are less reliable, so we track
    -- where a chunk came from.
    kind            TEXT    NOT NULL,

    -- Where this chunk sits inside the page's raw text. Grounding depends on
    -- these being correct: they are how we turn "found at chunk offset 47"
    -- into "page 2, character 1251".
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL,

    -- Section heading, units note, and footnote bodies, pasted onto the front
    -- of the chunk so it can be understood on its own. Stored separately from
    -- the text so we can tell what the document said from what we added.
    context_header  TEXT    NOT NULL DEFAULT '',

    text            TEXT    NOT NULL,

    -- False when a chunk was skipped by the pre-filter (no numbers, no dates,
    -- no names). Recording this lets us honestly report how many chunks we
    -- chose not to spend an LLM call on.
    was_sent_to_llm BOOLEAN NOT NULL DEFAULT FALSE
);


-- ===========================================================================
-- entities : the things facts are about, after we decide which names match
-- ===========================================================================
CREATE TABLE IF NOT EXISTS entities (
    entity_id       TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL,

    -- Every spelling we have seen: ["Acme Ltd", "Acme Limited", "ACL"]
    aliases         JSONB NOT NULL DEFAULT '[]',

    -- Any registry identifier the document happened to give us, for example
    -- {"DIN": "01173669", "CIN": "L63090DL2011PLC221234"}.
    -- Matching on an identifier is certain. Matching on a name is a guess.
    identifiers     JSONB NOT NULL DEFAULT '{}'
);


-- ===========================================================================
-- attribute_families : groups of phrases that mean the same kind of thing
--
-- "workforce strength", "team size" and "headcount" are three ways of saying
-- one thing. Without this grouping they never get compared and we find no
-- contradictions at all. Built at runtime by measuring phrase similarity —
-- never hard-coded.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS attribute_families (
    family_id       TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    member_phrases  JSONB NOT NULL DEFAULT '[]'
);


-- ===========================================================================
-- facts : the heart of the project
--
-- Four groups of columns:
--   1. identity   - which chunk of which document this came from
--   2. the claim  - what is being asserted
--   3. qualifiers - the context that decides whether two claims can be
--                   compared at all. THIS IS THE IMPORTANT GROUP.
--   4. the proof  - the exact words, and exactly where they are
-- ===========================================================================
CREATE TABLE IF NOT EXISTS facts (
    -- 1. identity ----------------------------------------------------------
    fact_id          TEXT PRIMARY KEY,
    chunk_id         TEXT REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    doc_id           TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page_no          INTEGER,

    -- 2. the claim ---------------------------------------------------------
    subject_raw      TEXT,             -- exactly as written in the document
    entity_id        TEXT,             -- filled in later, during normalisation
    attribute_raw    TEXT,             -- exactly as written, "workforce strength"
    attribute_family TEXT,             -- filled in later, "headcount"

    value_raw        TEXT,             -- exactly as written, "98,135"
    value_num        DOUBLE PRECISION, -- the comparable number, 98135
    value_text       TEXT,             -- for facts whose value is not a number,
                                       -- such as a status: "resigned"
    unit             TEXT,             -- "people", "INR crore", "percent"
    currency         TEXT,             -- "INR", "USD"
    multiplier       DOUBLE PRECISION, -- what we multiplied by to normalise,
                                       -- kept so the maths can be audited

    -- 3. qualifiers --------------------------------------------------------
    -- These are what let us tell a real contradiction from a difference that
    -- context explains. Revenue of 1240 and revenue of 980 only disagree if
    -- they cover the same period at the same scope.
    period_start     DATE,
    period_end       DATE,
    as_of_date       DATE,             -- for facts true at a moment, not over
                                       -- a span: headcount, a director's status
    scope_raw        TEXT,             -- "consolidated", or a footnote's wording
    scope_tags       JSONB NOT NULL DEFAULT '[]',

    -- 4. the proof ---------------------------------------------------------
    -- 'prose', 'table_cell', 'chart_label'. Different sources deserve
    -- different trust: a sentence carries its own meaning, a chart label was
    -- matched to its axis by position, which is a guess.
    source_kind      TEXT,
    confidence       DOUBLE PRECISION,

    evidence_text    TEXT,             -- the exact words, copied verbatim
    char_start       INTEGER,          -- where those words are, in the page
    char_end         INTEGER,

    -- TRUE only when we found evidence_text inside the source chunk. This one
    -- column is the difference between "a model said so" and "the document
    -- says so, here, at this character".
    grounded         BOOLEAN NOT NULL DEFAULT FALSE,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ===========================================================================
-- relations : what we concluded about a pair of facts
-- ===========================================================================
CREATE TABLE IF NOT EXISTS relations (
    relation_id      TEXT PRIMARY KEY,

    fact_a           TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    fact_b           TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,

    -- 'corroborates' | 'contradicts' | 'reconciled' | 'unrelated'
    verdict          TEXT NOT NULL,

    -- 'rule' | 'derivation' | 'llm'
    -- Recorded so we can honestly answer "how much of this is the model just
    -- deciding things?" with a number instead of a shrug.
    method           TEXT NOT NULL,

    -- Plain English, written for a human. The brief says the interesting part
    -- is how facts are explained, so this is a deliverable, not a footnote.
    explanation      TEXT,

    -- When a third fact explains the gap between two others -- for example
    -- 63,713 + 34,422 = 98,135 -- this points at that third fact.
    bridging_fact_id TEXT REFERENCES facts(fact_id) ON DELETE SET NULL,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Stops the same pair being judged twice if a step is re-run.
    UNIQUE (fact_a, fact_b)
);


-- ===========================================================================
-- failures : everything that went wrong, and enough detail to understand it
--
-- Deliberately has NO foreign keys. A failure often happens before the row it
-- refers to exists -- a PDF that will not open has no document row to point at.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS failures (
    failure_id  BIGSERIAL PRIMARY KEY,
    doc_id      TEXT,
    page_no     INTEGER,
    chunk_id    TEXT,

    stage       TEXT NOT NULL,   -- 'ingest', 'extract', 'ground', 'adjudicate'
    kind        TEXT NOT NULL,   -- short label, so we can group similar failures
    detail      TEXT,            -- the full story

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ===========================================================================
-- Indexes
--
-- An index is a lookup shortcut. Without one, finding matching rows means
-- reading every row in the table. Each index below exists for one specific
-- query the pipeline runs a lot.
-- ===========================================================================

-- THE MOST IMPORTANT INDEX IN THE PROJECT.
-- Step 6 groups facts by entity plus attribute family to decide which pairs are
-- worth comparing. That grouping runs against every fact, so it needs to be fast.
CREATE INDEX IF NOT EXISTS index_facts_blocking_key
    ON facts (entity_id, attribute_family);

-- Step 9's derivation check asks "is there a fact whose value is close to
-- 34,422?" to see whether a third fact explains the gap between two others.
CREATE INDEX IF NOT EXISTS index_facts_value_num
    ON facts (value_num);

-- The interface filters facts by document constantly.
CREATE INDEX IF NOT EXISTS index_facts_doc_id       ON facts (doc_id);
CREATE INDEX IF NOT EXISTS index_facts_grounded     ON facts (grounded);
CREATE INDEX IF NOT EXISTS index_chunks_doc_page    ON chunks (doc_id, page_no);
CREATE INDEX IF NOT EXISTS index_relations_verdict  ON relations (verdict);
CREATE INDEX IF NOT EXISTS index_relations_fact_a   ON relations (fact_a);
CREATE INDEX IF NOT EXISTS index_relations_fact_b   ON relations (fact_b);


-- ===========================================================================
-- Later additions
--
-- "CREATE TABLE IF NOT EXISTS" does nothing at all to a table that already
-- exists, so a column added after the first run would never appear. Each
-- addition therefore needs its own statement here. "ADD COLUMN IF NOT EXISTS"
-- keeps this script safe to run any number of times.
-- ===========================================================================

ALTER TABLE pages ADD COLUMN IF NOT EXISTS section_heading TEXT NOT NULL DEFAULT '';

-- The qualifiers exactly as the document worded them: "year ended 31 March
-- 2024", "FY24", "as at 30 June".
--
-- These sit beside the parsed date columns rather than replacing them, and the
-- division of labour is deliberate. The model READS -- it is good at spotting
-- that a phrase describes a period. Our code PARSES -- turning that phrase into
-- dates in Python is deterministic, testable by eye, and identical on every
-- run. Asking a model to do date arithmetic would make the most important
-- comparison in the project depend on something we cannot reproduce.
--
-- Keeping the original wording also means a reviewer can always check our
-- parsing against what the document actually said.
ALTER TABLE facts ADD COLUMN IF NOT EXISTS period_raw TEXT;
ALTER TABLE facts ADD COLUMN IF NOT EXISTS as_of_raw  TEXT;

-- 'number', 'date' or 'text'. Tells Step 5 which column a value belongs in and
-- tells Step 7 how it can be compared.
ALTER TABLE facts ADD COLUMN IF NOT EXISTS value_kind TEXT;

-- HOW a fact's evidence was found, not merely whether it was.
--   'exact'      the quote appears in its own chunk, character for character
--   'flexible'   it appears there but with different spacing
--   'same_page'  it was found elsewhere on the same page, not in the chunk the
--                fact claimed -- a mis-attribution we recovered from
-- A bare true/false would hide the difference between a perfect quote and one
-- we had to go looking for, and those deserve different amounts of trust.
ALTER TABLE facts ADD COLUMN IF NOT EXISTS grounding_method TEXT;

-- Does the claimed VALUE actually appear inside its own evidence?
--
-- This is a separate question from grounding, and the more searching one. A
-- quote can be entirely real while the number attached to it came from a
-- different column of the same table. Grounding proves the text exists; this
-- asks whether the text supports the claim.
ALTER TABLE facts ADD COLUMN IF NOT EXISTS value_in_evidence BOOLEAN;
"""


# The order matters: a table must be created after anything it points at.
TABLE_NAMES_IN_CREATION_ORDER = [
    "documents",
    "pages",
    "chunks",
    "entities",
    "attribute_families",
    "facts",
    "relations",
    "failures",
]


def find_which_tables_exist() -> set[str]:
    """Ask PostgreSQL which of our tables are actually present."""
    rows = database.fetch_all_rows(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        """
    )
    return {row["table_name"] for row in rows}


def create_all_tables() -> None:
    """Create every table and index. Does nothing to tables that already exist."""
    print("Creating tables and indexes...")
    database.run_sql_script(SQL_TO_CREATE_ALL_TABLES)
    print("  done.\n")


def report_what_is_in_the_database() -> bool:
    """
    Print each expected table with its row count.

    Returns True only if all eight tables are present. This is the check that
    turns "the script did not crash" into "the schema is really there".
    """
    tables_that_exist = find_which_tables_exist()

    print("Table                 Present   Rows")
    print("--------------------  -------   ----")

    every_table_is_present = True
    for table_name in TABLE_NAMES_IN_CREATION_ORDER:
        table_is_present = table_name in tables_that_exist
        if table_is_present:
            row_count = str(database.count_rows_in_table(table_name))
        else:
            row_count = "-"
            every_table_is_present = False

        present_marker = "yes" if table_is_present else "NO"
        print(f"{table_name:<20}  {present_marker:<7}   {row_count}")

    return every_table_is_present


def main() -> int:
    """Set up the database. Returns 0 on success, 1 on failure."""
    print("=" * 60)
    print("Ground Work — database setup")
    print("=" * 60)
    print()

    # Fail early and clearly if .env has not been filled in, rather than
    # letting psycopg produce a confusing connection error.
    try:
        config.stop_unless_these_settings_are_filled_in(["DATABASE_URL"])
    except RuntimeError as settings_problem:
        print("SETTINGS PROBLEM\n")
        print(settings_problem)
        return 1

    print("Connecting to the database...")
    connection_problem = database.try_connecting_and_describe_any_problem()
    if connection_problem is not None:
        print("  FAILED to connect.\n")

        print("What the database driver actually said:")
        print(f"  {connection_problem}\n")

        print("Where we were trying to connect (password hidden):")
        print(database.describe_connection_target())
        print()

        print("Most likely causes, in order of how often they happen:")
        print("  1. You used the DIRECT connection string instead of the")
        print("     SESSION POOLER one. Supabase direct connections are")
        print("     IPv6-only on the free tier and most home internet is IPv4.")
        print("     The pooler host looks like: aws-0-<region>.pooler.supabase.com")
        print("  2. The password in DATABASE_URL is wrong, or contains a special")
        print("     character that needs URL-encoding (@ must be written %40).")
        print("  3. The Supabase project is paused. Free projects pause after a")
        print("     week of no activity — open the dashboard to wake it up.")
        return 1
    print("  connected.\n")

    create_all_tables()

    all_tables_present = report_what_is_in_the_database()
    print()

    if all_tables_present:
        print("SUCCESS — all 8 tables are ready.")
        print("Open your Supabase dashboard -> Table Editor to see them.")
        return 0

    print("PROBLEM — some tables are missing. Read the output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
