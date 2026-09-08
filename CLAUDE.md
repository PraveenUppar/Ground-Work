# Ground Work — Project Rules

Read this at the start of every session. These rules override default habits.

---

## What this project is

A **fact knowledge layer**. You give it PDFs. It pulls out claims, proves each claim
came from a real place in a real document, and then compares claims against each
other to say whether they agree, disagree, or only look like they disagree.

---

## How we work together

1. **One step at a time.** Finish a step, show the result, wait for the user to say
   go. Do not start the next step on your own.
2. **Explain before building.** The user is learning this and preparing for an
   interview. A step is not done until they understand what it does.
3. **Ask, do not assume.** If a decision could go two ways, ask. The user decides.
4. **No test files.** The user decided against a test suite to save time on the
   24-hour clock. Instead, every step's script ends by **printing a summary the
   user can eyeball** — row counts, a few sample outputs, anything suspicious.
   Put inline checks only where being wrong would be *silent* (character
   offsets, number parsing), because those bugs do not crash, they just quietly
   produce wrong evidence. Do not reintroduce `tests/` without being asked.
5. **Plain English.** No jargon without a one-line explanation next to it.

## How the code must be written

- **Long, descriptive names.** `extract_footnote_markers_from_page` beats `get_fn`.
- **Expanded, not clever.** No one-liners that pack three ideas into one line. No
  nested comprehensions. Prefer an extra variable with a good name.
- **One function does one thing**, and the name says what that thing is.
- **Comments explain WHY, not WHAT.** The code already says what.
- **No silent failures.** If something goes wrong, write a row to the `failures`
  table with enough detail to debug it later.

## Naming rules

- Pipeline files are numbered in run order: `step_01_ingest_pdf.py`,
  `step_02_build_chunks.py`, and so on. Reading the file list should tell you the
  order the pipeline runs in.
- Test files mirror them exactly: `tests/test_step_01_ingest_pdf.py`.
- Docs are numbered too: `docs/01_ARCHITECTURE.md`.
- Everything is `snake_case`. No abbreviations unless they are industry standard.

---

## Hard technical rules

These exist for specific reasons. Do not quietly break them.

**1. Never hard-code anything document-specific.**
No company names, no filenames, no "if the page says Revenue then...". The task
brief forbids it and we will be tested on unseen PDFs. Anything document-specific
must be *read from the document at runtime* (for example, a glossary page), never
written into the code.

**2. Every fact must carry proof.**
A fact is only stored with the exact quoted sentence plus doc id, page number, and
character offsets. If the LLM's quote cannot be found in the source chunk, the fact
is rejected and logged. This is the core credibility feature of the whole project.

**3. Cache every LLM call to local disk.**
Key = hash of (chunk text + prompt version). We are on a free API tier with a daily
limit. We will re-run the pipeline dozens of times. Without the cache we run out of
quota and lose the submission.

**4. Batch every database write.**
The database is hosted (Supabase), so every write is a network round trip. Collect
rows in a list and insert 100–500 at a time. Never insert inside a loop, one row
per call.

**5. Write results to the database as we go, not at the end.**
If the pipeline crashes on chunk 480 of 600, we keep the first 479.

**6. The LLM cache never goes in the database.**
It is local files. Putting it in Postgres makes it slow and huge for no benefit.

---

## The pipeline, in order

| Step | File | Turns this | Into this |
|---|---|---|---|
| 1 | `step_01_ingest_pdf.py` | a PDF | pages: text blocks, tables, footnotes |
| 2 | `step_02_build_chunks.py` | pages | chunks with context headers attached |
| 3 | `step_03_extract_facts.py` | a chunk | candidate facts (structured claims) |
| 4 | `step_04_check_grounding.py` | candidate facts | proven facts, or rejections |
| 5 | `step_05_normalize_facts.py` | raw values | comparable values |
| 6 | `step_06_find_candidate_pairs.py` | all facts | pairs worth comparing |
| 7 | `step_07_adjudicate_pairs.py` | a pair | a verdict plus a written explanation |

Shared helpers live in `groundwork/shared/`. The user interface is
`groundwork/app/streamlit_app.py`.

---

## Documents to keep updated

- `docs/05_TODO.md` — update after finishing every step.
- `docs/03_APPROACH.md` — add an entry whenever a real decision is made.
- `docs/04_FAILURES.md` — add an entry whenever something breaks or surprises us.
  This is a graded deliverable, not a scratch file.

---

## Stack

Python 3.13 · Supabase (hosted Postgres) · Google Gemini via `google-genai` ·
PyMuPDF and pdfplumber for PDFs · Pydantic for validating LLM output ·
Streamlit for the interface · pytest for tests.
