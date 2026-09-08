# 05 — Build Checklist

Updated after every step. `[x]` done · `[ ]` not started · `[~]` in progress ·
`[–]` deliberately skipped

**ALL THIRTEEN STEPS ARE DONE.** The pipeline runs end to end, all four required
cases are found and displayed, the interface works, and the write-up is finished.

```
227 pages  ->  353 chunks  ->  3,960 facts  ->  3,033 grounded (77%)
           ->  8,366 pairs ->  adjudicated, 99.8% by rule
```

> **No test files** from Step 2 onward — decided in D-13. Instead every step
> ends with a printed summary to eyeball, and inline checks only where a bug
> would be silent rather than loud.

---

## Reading the code — a suggested order

Read them in pipeline order; each one only depends on the ones above it.

| File | What to look for |
|---|---|
| `groundwork/shared/config.py` | every setting in one place, secrets masked |
| `groundwork/shared/database.py` | why every write is batched — the note at the top |
| `groundwork/shared/models.py` | **the fact schema. Read this first if you read only one.** |
| `groundwork/shared/normalizers.py` | pure parsing, plus the 38-case check table at the bottom |
| `groundwork/shared/llm_client.py` | the only function that calls a model; caching and rate limiting |
| `step_01_ingest_pdf.py` | why we build on lines instead of the library's blocks |
| `step_02_build_chunks.py` | the context header — why footnotes travel with numbers |
| `step_03_extract_facts.py` | the extraction prompt, and its negative examples |
| `step_04_check_grounding.py` | the credibility core: proving each quote is real |
| `step_05_normalize_facts.py` | entities, attribute families, the LLM merge pass |
| `step_06_find_candidate_pairs.py` | **why the date is deliberately NOT in the blocking key** |
| `step_07_adjudicate_pairs.py` | the rule tree. The order of the branches is the argument. |

Every file opens with a comment explaining why it exists and what it refuses to
do. Where a decision was hard, the reasoning is written beside the code rather
than only in the docs.

---

## Running it

```
.\.venv\Scripts\python.exe scripts\show_pipeline_status.py       what is in the database
.\.venv\Scripts\python.exe scripts\show_stored_page.py <doc> <n> one page, as stored
.\.venv\Scripts\python.exe scripts\find_the_four_cases.py        the four required cases
.\.venv\Scripts\python.exe -m groundwork.shared.normalizers      the parser check table
```

The full pipeline, in order. Everything is cached, so a re-run costs no API
calls unless the prompt version changed.

```
.\.venv\Scripts\python.exe -u -m groundwork.step_01_ingest_pdf
.\.venv\Scripts\python.exe -u -m groundwork.step_02_build_chunks
.\.venv\Scripts\python.exe -u -m groundwork.step_03_extract_facts
.\.venv\Scripts\python.exe -u -m groundwork.step_04_check_grounding
.\.venv\Scripts\python.exe -u -m groundwork.step_05_normalize_facts
.\.venv\Scripts\python.exe -u -m groundwork.step_06_find_candidate_pairs
.\.venv\Scripts\python.exe -u -m groundwork.step_07_adjudicate_pairs
```

Use `-u` — without it Python buffers output and a long run looks frozen.

---

## Step 0 · Project setup — DONE

- [x] Decide hosted database: Supabase
- [x] Decide folder structure: numbered step files in one package
- [x] Create folder structure
- [x] `CLAUDE.md`, `README.md`
- [x] `docs/01_ARCHITECTURE.md` — parts, data model, why each choice
- [x] `docs/02_PIPELINE.md` — one fact traced through all seven steps
- [x] `docs/03_APPROACH.md` — decision log, 13 entries
- [x] `docs/04_FAILURES.md` — template, running log, predicted failures
- [x] `docs/05_TODO.md` — this file
- [x] `.gitignore`, `.env.example`, `requirements.txt`
- [x] `.claude/settings.local.json` — including a deny rule on `.env`
- [x] Copy the three sample PDFs into `data/input_pdfs/`
- [x] Virtual environment created, all 10 packages installed (72 total)
- [–] `git init` — deferred by choice, revisit at Step 12
- [ ] **You:** read 15 pages of the PDFs and fill `data/manual_fact_list.md`
      — our only answer key. Worth real time.

---

## Step 1 · Database — DONE

- [x] Supabase project created, region ap-south-1 (Mumbai)
- [x] `groundwork/shared/config.py` — reads `.env`, masks secrets when printing
- [x] `groundwork/shared/database.py` — connection reuse, batched inserts, queries
- [x] `scripts/create_database_tables.py` — eight tables, eight indexes
- [x] `conftest.py`
- [x] Connection diagnostics that report the driver's real error, not a bare False
- [x] Password redaction that survives a password containing `@`
- [x] Supabase database password reset to letters and digits only
- [x] Fresh Session pooler string in `.env`
- [x] `scripts/create_database_tables.py` runs clean — all 8 tables present
- [x] `GOOGLE_API_KEY` in `.env` (format looks unusual — verify at Step 4)
- [ ] Confirm the tables appear in the Supabase Table Editor (your eyes, 30 sec)

---

## Step 2 · Read PDFs into pages — DONE

- [x] Reconnaissance: all three PDFs are text, not scanned. **No OCR needed.**
- [x] Discovered PyMuPDF's `blocks` mode merges across columns and attaches
      numbers to the wrong labels — see failure 5. Built on `lines` instead.
- [x] `groundwork/step_01_ingest_pdf.py`
- [x] Extract lines with position and font size
- [x] Group lines into visual blocks by position (our own rules, not the library's)
- [x] Remove page furniture by size and position — never by matching words
- [x] Find footnote bodies, including continuation lines
- [x] Detect the section heading by font size and position
- [x] Extract tables separately, flatten each row, skip what prose already has
- [x] Assemble one canonical text per page — the anchor for all later offsets
- [x] Inline check: every block's text must survive into the assembled page
- [x] Save documents and pages to the database, batched
- [x] Re-ingesting replaces a document, because changed text invalidates offsets
- [x] **Checked by eye:** annual report page 2 — all 12 infographic tiles now
      pair the right number with the right label and the right footnotes
- [x] Drop prose lines that a kept table already holds — see failure 13
- [x] Sort lines into reading order before reading footnotes — see failure 12
- [x] `scripts/show_stored_page.py` — inspect any stored page against the PDF
- [x] **Full run: 227 pages, 3 documents, 0 failures**
- [x] Spot-checked the presentation balance sheet and a prose prospectus page
- [–] Complex multi-row table headers still flatten poorly — logged as
      failure 14, deliberately not fixed, goes in "with more time"

---

## Step 3 · Build chunks — DONE

- [x] Added `pages.section_heading` and re-ingested to fill it
- [x] `groundwork/step_02_build_chunks.py`
- [x] Split each page into its three regions: prose, tables, footnotes
- [x] Cut on the blank lines that separate Step 1's visual blocks — never
      mid-block, so a number is never parted from its label
- [x] Break up any single paragraph too long to keep whole, at sentences
- [x] Group pieces up to ~1000 words; no overlap (D-16)
- [x] Context header: section heading + only the footnotes a passage refers to
- [x] Symbol-marked footnotes (`*`) attach to every passage on their page (F-15)
- [x] Header stored in its own column, so we can always tell the document's
      words from our scaffolding
- [x] Pre-filter, with every skip recorded in `failures` rather than dropped
- [x] **Offset check passes** — verified after storage, not in memory, because
      an in-memory check would be circular
- [x] **353 chunks:** 264 prose, 60 table, 29 footnotes. 26 carry footnote scope.
- [ ] Decide how to bring 347 API calls inside the daily free-tier allowance

---

## Step 4 · LLM client — DONE

- [x] `groundwork/shared/llm_client.py` — one function, one entry point
- [x] Disk cache keyed on prompt + model name + our own prompt version
- [x] Atomic cache writes, so an interrupted run leaves no truncated answer
- [x] Retry with growing pauses, but only on temporary-looking failures
- [x] Global rate limiter shared across threads
- [x] Provider-native JSON mode, rather than asking nicely and repairing
- [x] Model pinned to an exact version, never a moving alias (see D-18)
- [x] **Self-check passes all three:** the key works, the same prompt twice
      sends one request, and a new prompt version ignores the stale answer

---

## Step 5 · Extract facts — IN PROGRESS

- [x] `groundwork/shared/models.py` — the Fact shape, validated by Pydantic
- [x] Qualifiers captured as the document worded them; our code parses them
      later, so the most important comparison does not depend on model output
      we cannot reproduce
- [x] New columns: `period_raw`, `as_of_raw`, `value_kind`
- [x] `groundwork/step_03_extract_facts.py`
- [x] Generic prompt — no mention of revenue, headcount, directors or logistics
- [x] Five worked examples, all from invented companies
- [x] Provider-enforced response schema, not hopeful JSON parsing
- [x] Deterministic batching, so cache keys stay stable between runs
- [x] Confidence by source: prose .90, footnote .85, table .75, figure .50
- [x] Facts naming a passage that was not in the request are rejected and logged
- [x] **Presentation done: 253 facts, 5 requests, 0 problems**
- [x] **Cache proven in production:** a re-run sent 1 request instead of 5
- [ ] Run on the prospectus and the annual report (~64 more requests)
- [ ] **Read the facts by hand** against `data/manual_fact_list.md`

---

## Step 6 · Grounding check — IN PROGRESS

- [x] `groundwork/step_04_check_grounding.py`
- [x] Exact search, then a whitespace-forgiving search, then the whole page
- [x] `grounding_method` records HOW each was found, not merely that it was —
      an exact quote and one we had to hunt for deserve different trust
- [x] `value_in_evidence`: does the claimed number appear in its own quote?
      A separate and harder question than whether the quote is real
- [x] Character offsets stored against the page, which is what the interface needs
- [x] Every rejection written to `failures` with its unfindable quote
- [x] Searching stops at the page — a check that always succeeds is not a check
- [x] **First run found the big one:** 1,014 facts (23%) quoting assembled
      evidence that appears nowhere in the document. See failure 27.
- [x] Prompt fixed and validated: 76.4% → 92.5% grounded, all exact quotes
- [ ] Re-extract both remaining documents with prompt v2, then ground everything

---

## Step 7 · Normalize — IN PROGRESS

**Parsers done** — `groundwork/shared/normalizers.py`

- [x] Numbers: commas, crore, lakh, million, billion, percentages
- [x] Bracket negatives, including the unmatched `(452` one model produces
- [x] Approximate markers (`>`, `~`, `+`) recorded, not discarded — a floor is
      not a measurement and must not be compared as though it were
- [x] Currency symbols and codes, so two currencies are never compared as equal
- [x] Dates in six written forms, leap years included
- [x] Periods: "year ended X", "nine months ended X", "Q4 FY24", "FY24"
- [x] **Financial year end learned from the documents**, not hard-coded — an
      Indian report ends in March and an American one in December, and "FY24"
      means different things in each
- [x] **38-case check table, all passing** — caught a word-boundary bug that
      made `>2.8Bn` parse as 2.8 (see failure 29)

**Resolution done** — `groundwork/step_05_normalize_facts.py`

- [x] Parsers applied to all 3,033 grounded facts
- [x] 89% now carry a comparable number
- [x] Entities: self-reference handling, legal-form stripping, fuzzy matching
- [x] Registry identifiers pulled from evidence by SHAPE, not by knowing what a
      DIN is — captured the CIN and three directors' DINs
- [x] Attribute families: spelling first, then one LLM pass for meaning (D-23)
- [x] Scope turned into comparable tags
- [x] 296 entities, 1,029 families, `Delhivery Limited` holding 2,002 facts
- [–] Glossary mining — not needed so far; the LLM merge pass covers the same
      ground. Revisit only if pairing turns out to be starved.

---

## Step 8 · Find candidate pairs — DONE

- [x] `groundwork/step_06_find_candidate_pairs.py`
- [x] Blocked on entity + attribute family — **never** on date (D-08)
- [x] Same-page duplicate claims skipped, so a corroboration is never just
      the same fact counted twice
- [x] Bucket size capped, keeping the most trustworthy facts if it bites
- [x] **4,561,710 possible pairs → 8,331 candidates (548x fewer)**
- [x] **2,377 pairs cross documents** — the ones capable of being a real finding
- [x] No bucket hit the cap; biggest is `revenue` at 40 facts
- [–] Fuzzy attribute matching at pair time — unnecessary, the family grouping
      in Step 7 already does this work once instead of per pair

---

## Step 9 · Adjudicate — DONE

- [x] `groundwork/step_07_adjudicate_pairs.py`
- [x] The rule tree, in order — **the order is the argument**: every branch
      asks "is there a stated reason these could differ without either being
      wrong?", and only when all are exhausted may we say "contradicts"
- [x] Currencies and proportions rejected as incomparable before values are read
- [x] Each fact reduced to a single moment before comparing time (failure 37)
- [x] A ratio that is an exact power of ten is our own units bug, not their
      disagreement, and is never reported as one
- [x] A value written as a bound (">2.8Bn") is not compared as a measurement
- [x] `superseded` — a state that changed is not a contradiction
- [x] The derivation check, with the uniqueness rule that makes it mean
      anything (failures 33 and 34)
- [x] LLM fallback, capped, for the handful of pairs rules cannot settle
- [x] A plain-English explanation on every single relation
- [x] `method` recorded on every relation: rule, derivation or llm

**Result: 8,366 pairs judged, 99.8% by rule.**

```
reconciled   rule         5,921  (71%)
contradicts  rule         1,235  (15%)
unrelated    rule           946  (11%)
corroborates rule           195  ( 2%)
reconciled   derivation      43  ( 1%)   <- with arithmetic proof
superseded   rule             9
by model                      15  (0.2%)
```

---

## Step 10 · Interface — DONE

`groundwork/app/streamlit_app.py`, six pages. One idea runs through all of them:
**nothing is shown without its evidence.**

- [x] **Overview** — corpus numbers, the grounding rate stated as a headline
      result, and what share of verdicts came from rules rather than a model
- [x] **The four cases** — the required deliverable, selected by query rather
      than chosen by hand
- [x] **Facts** — filter by document, entity or free text; every fact shows its
      quote, page and character range; rejected facts can be browsed too
- [x] **Comparisons** — both facts side by side, the verdict, the written
      reasoning, and the bridging fact where one exists
- [x] Click through to the **source page with the quote highlighted in place** —
      this is what the character offsets have been carried through seven steps for
- [x] **Upload a PDF** — runs all seven stages with a page limit and live
      progress, then compares the new document against everything stored
- [x] **What went wrong** — the failures table, browsable by stage
- [x] Verified running: all interface queries execute, pages render with real data

---

## Step 11 · The four required cases — FOUND

`scripts/find_the_four_cases.py` selects them by stated query, not by hand, so
the examples are whatever the system actually produced.

- [x] **Case 1** — `Revenues from customers ₹81,415.38 million` (annual report
      p36) against `Revenue from customers (A+B) ₹8,142 Cr` (presentation p17).
      Different documents, different wording, **different units**, agreeing to
      0.01%. Also the same DIN corroborated across prospectus and annual report.
- [x] **Case 2** — `EBITDA margin 1.56%` as at 31 Mar 2024 (annual report p37)
      against `EBITDA margin 1.6%` for FY24 (presentation p17). Same company,
      same measure, same year, two documents, 2.5% apart.
- [x] **Case 3** — `net IPO proceeds ₹8,703.00m` against `₹8,863.03m`,
      reconciled by `un-utilised IPO expenses ₹160.03m` found independently:
      8,703.00 + 160.03 = 8,863.03. Plus 9 `superseded` cases, including a
      director's appointment then resignation and the company's own renaming
      from Private Limited to Limited.
- [x] **Case 4** — 927 facts (23%) rejected for quoting evidence that does not
      exist, plus five bugs we caused and caught ourselves (failures 33-37).
- [ ] Screenshot each one from the interface once Step 10 exists
- [ ] Verify cases 1 to 3 by opening the real pages yourself

---

## Step 12 · Finish — DONE

- [x] **Top 5 written up** in `docs/04_FAILURES.md`, chosen from 37 logged
      entries. Three were caught by checks we built on purpose; two only
      because we looked at raw output instead of trusting a number.
- [x] **README rewritten**: the four cases with real evidence, how it works,
      the four decisions worth explaining, and an honest "what this does not do"
- [x] `docs/02_PIPELINE.md` now points at the real verified reconciliation
- [ ] Optional: recall check against `data/manual_fact_list.md` (never filled in)
- [ ] Optional: `git init` and a first commit

### The original Step 12 list, for reference

- [ ] Fill in the Top 5 in `docs/04_FAILURES.md` — **37 candidates now logged**.
      Strongest: #27 (the model assembled evidence, grounding caught 1,014),
      #33 (the derivation check proved anything we asked it to), #29 (a word
      boundary made numbers a billion times too small, silently), #5 (the PDF
      library glued numbers to the wrong labels), #22 (we planned the whole API
      budget on a guessed quota that was wrong by 12x).
- [ ] Expand `README.md`: setup, how to run, the four cases
- [ ] Replace the illustrative example in `02_PIPELINE.md` with the real
      net-IPO-proceeds reconciliation, now that we have one
- [ ] Recall check: how many of the manual facts did the system find?
- [ ] Precision check: 30 random facts, verified by hand
- [ ] `git init` and a final commit
