# Ground Work

A fact knowledge layer for PDF documents.

Live Demo - https://ground-work-superjoin.streamlit.app/

Demo Video -

It reads PDFs, pulls out the claims inside them, **proves each claim against the
source text**, and then compares claims to say whether they agree, disagree, or
only _look_ like they disagree.

```
227 pages  →  353 passages  →  3,960 claims  →  3,033 proved  →  8,366 comparisons
                                                    (76.6%)
```

---

## The Pipeline

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

## The one idea everything rests on

**You cannot compare two sentences. You can compare two structured claims.**

Take this sentence out of an annual report:

> "Revenue for the year ended 31 March 2024 was Rs 1,240 crore on a consolidated basis."

On its own it is just text. Ground Work breaks it into slots:

| Slot      | Value                           | What it is for                         |
| --------- | ------------------------------- | -------------------------------------- |
| subject   | Example Company Ltd             | who or what the claim is about         |
| attribute | revenue                         | what property is being claimed         |
| value     | 1240                            | the claimed amount                     |
| unit      | INR crore                       | the scale, so numbers can be converted |
| period    | 2023-04-01 to 2024-03-31        | **qualifier**                          |
| scope     | consolidated                    | **qualifier**                          |
| evidence  | the exact sentence above        | proof                                  |
| location  | doc_1, page 42, chars 1180-1265 | where the proof lives                  |

Two claims in this shape can be lined up slot by slot.

**The qualifiers are the important part.** Most systems extract subject, attribute
and value, then declare a contradiction whenever two values differ. That is wrong
most of the time. Revenue of 1,240 and revenue of 980 is not a contradiction if one
is FY24 and the other is FY23. Without the qualifiers you cannot tell the
difference — and you also cannot produce the "apparent contradiction explained by
context" case at all.

---

<!-- ---

## How it works

Nine stages. Each writes to the database before the next runs, so any stage can
crash without losing what came before, and any stage can be re-run alone.

|     | Stage                          | Turns      | Into                                 |
| --- | ------------------------------ | ---------- | ------------------------------------ |
| 1   | `step_01_ingest_pdf`           | a PDF      | pages: text, tables, footnotes       |
| 2   | `step_02_build_chunks`         | pages      | passages with their context attached |
| 3   | `step_03_extract_facts`        | a passage  | structured claims                    |
| 4   | `step_04_check_grounding`      | claims     | **proved claims, or rejections**     |
| 5   | `step_05_normalize_facts`      | raw values | comparable values                    |
| 6   | `step_06_find_candidate_pairs` | all facts  | the pairs worth comparing            |
| 7   | `step_07_adjudicate_pairs`     | a pair     | a verdict and a written reason       |

--- -->

## Running it

Requires Python 3.13, a Supabase (or any PostgreSQL) database, and a free
[Google AI Studio](https://aistudio.google.com) API key.

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in `DATABASE_URL` and `GOOGLE_API_KEY`.
Read the comments in that file — the model choice and rate limits matter, and the
free-tier quotas are documented.

```bash
.\.venv\Scripts\python.exe scripts\create_database_tables.py
```

Put PDFs in `data/input_pdfs/`, then:

```bash
.\.venv\Scripts\python.exe -u -m groundwork.step_01_ingest_pdf
.\.venv\Scripts\python.exe -u -m groundwork.step_02_build_chunks
.\.venv\Scripts\python.exe -u -m groundwork.step_03_extract_facts
.\.venv\Scripts\python.exe -u -m groundwork.step_04_check_grounding
.\.venv\Scripts\python.exe -u -m groundwork.step_05_normalize_facts
.\.venv\Scripts\python.exe -u -m groundwork.step_06_find_candidate_pairs
.\.venv\Scripts\python.exe -u -m groundwork.step_07_adjudicate_pairs
```

Every model call is cached to local disk, so re-running costs nothing unless the
prompt version changed.

**The interface**, which also uploads and processes new PDFs:

```bash
.\.venv\Scripts\streamlit.exe run groundwork\app\streamlit_app.py
```

**Useful on their own:**

```bash
.\.venv\Scripts\python.exe scripts\show_pipeline_status.py     what is in the database
.\.venv\Scripts\python.exe scripts\find_the_four_cases.py      the four cases, as text
.\.venv\Scripts\python.exe -m groundwork.shared.normalizers    the 38-case parser check
```

---

## Documentation

|                                                    |                                            |
| -------------------------------------------------- | ------------------------------------------ |
| [docs/01_ARCHITECTURE.md](docs/01_ARCHITECTURE.md) | the parts, the data model, why each choice |
| [docs/02_PIPELINE.md](docs/02_PIPELINE.md)         | one fact traced through all seven stages   |
| [docs/03_APPROACH.md](docs/03_APPROACH.md)         | 23 decisions, each with its cost           |
| [docs/04_FAILURES.md](docs/04_FAILURES.md)         | 37 failures, what broke and what we did    |

Every source file opens with a comment explaining why it exists and what it
refuses to do. Where a decision was hard, the reasoning sits beside the code.

## Stack

Python 3.13 · PostgreSQL (Supabase) · Google Gemini · PyMuPDF · pdfplumber ·
Pydantic · RapidFuzz · Streamlit
