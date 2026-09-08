# Ground Work

A fact knowledge layer for PDF documents.

It reads PDFs, pulls out the claims inside them, **proves each claim against the
source text**, and then compares claims to say whether they agree, disagree, or
only _look_ like they disagree.

```
227 pages  →  353 passages  →  3,960 claims  →  3,033 proved  →  8,366 comparisons
                                                    (76.6%)
```

---

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

### Four decisions worth explaining

**Every fact must carry proof.** The model must copy its evidence verbatim; we
then search for that string in the passage it came from. No match, no fact.

**Rules first, model last.** A deterministic rule tree judges every pair; the
model is asked only about the handful the rules cannot settle.

**The order of the rule tree is the argument.** Each branch asks _"is there a
stated reason these could differ without either being wrong?"_ Only when every
such reason is exhausted may the word _contradicts_ be used. Check values first
and context second, and you report a contradiction for every pair of figures
covering different years.

**Never block on a field the adjudicator needs.** Pairs are grouped by entity and
attribute family

## The one idea everything rests on

**You cannot compare two sentences. You can compare two structured claims.**

> "Revenue for the year ended 31 March 2024 was ₹1,240 crore on a consolidated basis."

As text, that can only be matched against another sentence by how similar the
words look — which says nothing about whether the two _agree_. So it is broken
into slots:

| Slot       | Value                           |               |
| ---------- | ------------------------------- | ------------- |
| subject    | Example Company Ltd             |               |
| attribute  | revenue                         |               |
| value      | 1240 · INR crore                |               |
| **period** | **2023-04-01 → 2024-03-31**     | **qualifier** |
| **scope**  | **consolidated**                | **qualifier** |
| evidence   | the exact sentence above        | proof         |
| location   | doc 1, page 42, chars 1180–1265 | proof         |

**The qualifiers are the point.** Most extraction schemas stop at subject,
attribute and value — and then report a contradiction every time two figures
differ. Revenue of 1,240 and revenue of 980 do not disagree if one is FY24 and
the other FY23. Without period, as-of date and scope you cannot tell a real
contradiction from a difference that context fully explains.

## Running it

Requires Python 3.13, a Supabase (or any PostgreSQL) database, and a free
[Google AI Studio](https://aistudio.google.com) API key.

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in `DATABASE_URL` and `GOOGLE_API_KEY`.
Read the comments in that file — the model choice and rate limits matter, and the
free-tier quotas are documented there because guessing them cost us a day.

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

|                                                    |                                             |
| -------------------------------------------------- | ------------------------------------------- |
| [docs/01_ARCHITECTURE.md](docs/01_ARCHITECTURE.md) | the parts, the data model, why each choice  |
| [docs/02_PIPELINE.md](docs/02_PIPELINE.md)         | one fact traced through all seven stages    |
| [docs/03_APPROACH.md](docs/03_APPROACH.md)         | 23 decisions, each with its cost            |
| [docs/04_FAILURES.md](docs/04_FAILURES.md)         | 37 failures, what broke and what we did     |
| [docs/05_TODO.md](docs/05_TODO.md)                 | build log, and a reading order for the code |

Every source file opens with a comment explaining why it exists and what it
refuses to do. Where a decision was hard, the reasoning sits beside the code.

## Stack

Python 3.13 · PostgreSQL (Supabase) · Google Gemini · PyMuPDF · pdfplumber ·
Pydantic · RapidFuzz · Streamlit
