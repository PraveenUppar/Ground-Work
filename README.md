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
then search for that string in the passage it came from. No match, no fact. This
is the difference between _"a model said so"_ and \_"the document says so, on this
page, at this character".

**Rules first, model last.** A deterministic rule tree judges every pair; the
model is asked only about the handful the rules cannot settle. **99.8% of
verdicts came from rules.** When someone asks _"how much of this is a model's
opinion?"_, the answer is a number — every relation records which
decided it.

**The order of the rule tree is the argument.** Each branch asks _"is there a
stated reason these could differ without either being wrong?"_ Only when every
such reason is exhausted may the word _contradicts_ be used. Check values first
and context second, and you report a contradiction for every pair of figures
covering different years.

**Never block on a field the adjudicator needs.** Pairs are grouped by entity and
attribute family

### Nothing is document-specific

The system is tested on unseen PDFs.

- The extraction prompt never names revenue, headcount, directors or logistics.
  Its worked examples use invented companies.

---

## What this does not do

Stated plainly, because a system that hides its limits cannot be trusted with the
ones it reports.

**Grounding proves the text, not the pairing.** A fact can quote a real table row
containing three numbers and no column headings. The quote is honest; the pairing
may still be wrong. 98.4% of facts have their value present inside their own
evidence, which narrows this but does not close it.

**Undetected tables force a guess.** On financial-statement pages with no ruling
lines, `pdfplumber` finds no table, so labels and numbers survive as separate
columns and the model aligns them by position. Grounding rejects most of these —
the 23% rejection rate is largely this. The fix is to reconstruct tables from the
line coordinates we already have; it did not fit the time budget.

**Attribute families are grouped by spelling, then by one model pass.**
"Workforce strength" and "team size" share no characters and would not group
without that pass, which is deliberately conservative.

**1,235 contradictions is more than are real.** Many are facts sharing an
attribute family that should not, or figures with no stated period. The
interface's _four cases_ view applies stricter criteria to surface credible ones.

---

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

---

## The four cases

### 1 · Corroborated across documents, expressed differently

```
Annual report, p36 (prose)
  "Our freight, handling and servicing costs increased by 5.31% to
   ₹59,707.49 million for FY24 from ₹56,694.80 million for FY23"

Presentation, p17 (table row)
  "Total freight, handling and servicing cost | 1,372 | 1,572 | 1,519 |
   (3.4%) | 10.7% | | 5,669 | 5,971 | 5.3%"

→ CORROBORATES.  Agreement to 0.00%.
```

Two documents, different wording, **different units** — millions against crore —
and a different kind of source in each. The system had to normalise both to a
common scale before it could see they were the same number.

### 2 · A genuine contradiction

```
Annual report, p37   EBITDA margin 1.56%   as at 31 March 2024
Presentation,  p17   EBITDA margin 1.6%    FY24

→ CONTRADICTS.  Same company, same measure, same year, 2.5% apart.
```

A statutory filing and an investor deck reporting the same metric differently.

### 3 · An apparent contradiction explained by context

```
net IPO proceeds  ₹8,703.00 million
net IPO proceeds  ₹8,863.03 million
gap:              ₹  160.03 million

Found independently elsewhere in the corpus:
  "un-utilised IPO expenses of ₹160.03 million had been transferred to
   net IPO proceeds, thereby increasing…"

→ RECONCILED.   8,703.00 + 160.03 = 8,863.03
```

The system does not merely _label_ this a reconciliation — it goes looking for a
third documented quantity that accounts for exactly the gap, and shows the
arithmetic.

**And a fifth verdict that matters more than it looks:**

```
Jiang Bo   "Appointment as non-executive additional director"  25 Jun 2020
        →  "Resignation as nominee director"                   13 Oct 2021

→ SUPERSEDED, not CONTRADICTS.
```

The documents agree perfectly; the world moved between them. Telling _"these
documents disagree"_ apart from _"this thing changed"_ is the difference between
a useful tool and an alarm that cries wolf. It also caught the company renaming
itself from _Delhivery Private Limited_ to _Delhivery Limited_.

### 4 · An extraction failure, and how it was handled

**927 of 3,960 facts (23%) were rejected** because their quoted evidence does not
exist in the document.

Asked to quote a table row verbatim, the model instead _assembled_ evidence,
gluing a row's label to the one value it meant:

```
returned:  "Bad debt written off\n0.02"
on page:   "Bad debt written off | 0.02 | 0.44"
```

Helpful in intent — it was showing _which_ number it claimed — but that string
appears nowhere in the document. Every one was caught.

**How it was handled:** the prompt was fixed, not the check. Relaxing grounding
to accept "the parts all appear nearby" would have validated precisely the
mistake the check exists to catch, quietly, in the direction that flatters us.
Grounding went from 76% to 92% on the test document, with every surviving quote
exact.

**37 failures are written up in [docs/04_FAILURES.md](docs/04_FAILURES.md)**,
including five caused by this project's own code and caught by it — among them a
derivation check that "proved" a gap of 4 using an unrelated quantity of 4, and a
word-boundary bug that made `>2.8Bn` parse as `2.8`.

---

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
