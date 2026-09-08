# 01 — Architecture

How Ground Work is put together, and why each piece exists.

---

## The one idea everything rests on

**You cannot compare two sentences. You can compare two structured claims.**

Take this sentence out of an annual report:

> "Revenue for the year ended 31 March 2024 was Rs 1,240 crore on a consolidated basis."

On its own it is just text. Ground Work breaks it into slots:

| Slot | Value | What it is for |
|---|---|---|
| subject | Example Company Ltd | who or what the claim is about |
| attribute | revenue | what property is being claimed |
| value | 1240 | the claimed amount |
| unit | INR crore | the scale, so numbers can be converted |
| period | 2023-04-01 to 2024-03-31 | **qualifier** |
| scope | consolidated | **qualifier** |
| evidence | the exact sentence above | proof |
| location | doc_1, page 42, chars 1180-1265 | where the proof lives |

Two claims in this shape can be lined up slot by slot.

**The qualifiers are the important part.** Most systems extract subject, attribute
and value, then declare a contradiction whenever two values differ. That is wrong
most of the time. Revenue of 1,240 and revenue of 980 is not a contradiction if one
is FY24 and the other is FY23. Without the qualifiers you cannot tell the
difference — and you also cannot produce the "apparent contradiction explained by
context" case at all.

---

## The parts

Nine modules. Each one does a single transformation and writes its output to the
database before the next one runs. That means any stage can crash without losing
the work done before it, and a broken stage can be re-run on its own.

| # | Module | Takes in | Puts out |
|---|---|---|---|
| 1 | `step_01_ingest_pdf` | a PDF file | pages: text blocks with positions, tables, a footnote map |
| 2 | `step_02_build_chunks` | pages | chunks of roughly 600 words, each with a context header |
| 3 | `step_03_extract_facts` | one chunk | candidate facts, as validated structured data |
| 4 | `step_04_check_grounding` | candidate facts | proven facts, or a logged rejection |
| 5 | `step_05_normalize_facts` | raw slot values | comparable slot values |
| 6 | `step_06_find_candidate_pairs` | all facts | the small set of pairs worth comparing |
| 7 | `step_07_adjudicate_pairs` | one pair | a verdict and a written explanation |
| — | `shared/llm_client` | a prompt | a response, with caching and retries |
| — | `shared/database` | rows | batched reads and writes to Postgres |

### Why the LLM lives behind one function

Every call to Gemini goes through a single function in `shared/llm_client.py`.
Two reasons. First, caching and retry logic exist in exactly one place instead of
seven. Second, if Google rate-limits us at hour 19, we swap to a different
provider by editing one file rather than hunting through the whole codebase.

---

## The pipeline, drawn

```
   PDF file
      |
      v
 [1] INGEST ............. text blocks + tables + footnotes, per page
      |
      v
 [2] CHUNK .............. attach section heading, units note and footnote
      |                   bodies so each chunk can stand on its own
      v
 [3] EXTRACT ............ LLM reads the chunk, returns structured claims
      |
      v
 [4] GROUND ............. does the quoted evidence really exist in the
      |                   chunk? no -> reject it and log why
      v
 [5] NORMALIZE .......... "1,240" -> 1240 ; "FY24" -> a date range ;
      |                   "Acme Ltd" and "Acme Limited" -> one entity id
      v
 [6] PAIR ............... group facts so we compare hundreds of pairs,
      |                   not millions
      v
 [7] ADJUDICATE ......... rules first, LLM only for the leftovers
      |
      v
   VERDICT + EXPLANATION  ->  stored, then shown in the interface
```

---

## What the adjudicator decides

For each candidate pair, one of four verdicts:

| Verdict | Meaning |
|---|---|
| `corroborates` | same claim, same context, values agree |
| `contradicts` | same claim, same context, values genuinely disagree |
| `reconciled` | values differ, but a qualifier explains it (period, scope, unit, as-of date) |
| `unrelated` | not actually about the same thing |

Rules run first and settle most pairs deterministically. The LLM is only asked
about pairs the rules cannot settle, and about writing the human-readable
explanation. Every relation records **which method decided it**, so we can honestly
say what share of verdicts came from rules rather than from a model's opinion.

---

## Data model

Eight tables in Postgres.

**documents** — doc_id, filename, sha256, page_count, status, uploaded_at
The sha256 gives us upload idempotency for free: the same file uploaded twice
skips straight to the existing results.

**pages** — doc_id, page_no, raw_text, footnotes
Kept so the interface can show a source page without re-opening the PDF.

**chunks** — chunk_id, doc_id, page_no, kind (prose or table), char_start,
char_end, text, context_header

**facts** — the main table, in four groups:
- *identity*: fact_id, chunk_id, doc_id, page_no
- *the claim*: subject_raw, entity_id, attribute_raw, attribute_family,
  value_raw, value_num, value_text, unit, currency, multiplier
- *the qualifiers*: period_start, period_end, as_of_date, scope_raw, scope_tags
- *the proof*: source_kind, confidence, evidence_text, char_start, char_end,
  grounded

**entities** — entity_id, canonical_name, aliases, identifiers
"Identifiers" means any registry id the document happens to give us. Matching on
an identifier is certain; matching on a name is a guess.

**attribute_families** — family_id, label, member_phrases
Built at runtime by grouping similar attribute phrases. This is what lets
"workforce strength" and "team size" land in the same bucket.

**relations** — fact_a, fact_b, verdict, method, explanation, bridging_fact_id

**failures** — doc_id, page_no, chunk_id, stage, kind, detail
Not a scratch table. It is a graded deliverable, and it is where case 4 comes from.

---

## Where things run

| Thing | Where | Why there |
|---|---|---|
| Postgres | Supabase, hosted | a reviewer can inspect the data; we get a web table viewer while building |
| LLM | Google Gemini API | free tier, no credit card, generous token limit |
| LLM response cache | local disk, `data/llm_cache/` | it is just files; putting it in Postgres would be slow and huge for no gain |
| Uploaded PDFs | local disk, `data/input_pdfs/` | we only need to read them, not share them |
| Interface | Streamlit, run locally | the brief asks for a simple upload-and-inspect UI, not a product |

### The cost of a hosted database

Every write is a network round trip. The pipeline produces hundreds of chunks and
thousands of facts. Written one row at a time, that is thousands of round trips and
minutes of pure waiting.

So every write is **batched**: rows are collected in a list and inserted a few
hundred at a time. This is not an optimisation we bolt on later — it is how
`shared/database.py` is built from the first line.

---

## What makes this generalise to unseen PDFs

The task brief says the system must not rely on hard-coded facts, filenames,
schemas, or document-specific rules. Three things keep us honest:

1. **The fact schema is generic.** There is no field named "revenue" or
   "director". There is subject, attribute, value, qualifiers. The document
   decides what fills them.
2. **The extraction prompt never names a domain.** It defines a fact as "a
   statement asserting a value, status, date, or relationship about a named
   entity", and its worked examples are built from invented text, not from our
   sample PDFs.
3. **Vocabulary is read from the document, not written into the code.** If a
   document has a glossary or abbreviations page, we extract the term-definition
   pairs at runtime and hand them to the normalizer. The glossary is input data,
   exactly like the rest of the PDF.
