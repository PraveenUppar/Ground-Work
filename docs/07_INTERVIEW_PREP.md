# 07 — Video script and interview preparation

Everything you need to record the demo and answer questions about it.
Written in plain English, to be read out loud.

---

## Part 1 — The thirty-second pitch

Learn this. It is the answer to "so what did you build?"

> I built a system that reads PDFs and pulls out the facts inside them. But the
> important part is not the extraction. It is that **every fact is proved
> against the document** — the model has to quote the exact words, and I check
> that those words really exist on that page at that character position. If it
> cannot be found, the fact is thrown away.
>
> Then it compares facts against each other and says whether they agree,
> disagree, or only *look* like they disagree. Two revenue figures that differ
> are not a contradiction if one covers 2023 and the other 2024. Working out
> that difference is the whole point.
>
> It found 3,033 proved facts across three documents, made 8,366 comparisons,
> and **99.8% of the verdicts came from ordinary code rules, not from the AI.**

---

## Part 2 — The video script

Aim for **six to eight minutes**. Do not rush. Long pauses are fine.

### 0:00 – 0:45 · What the problem is

**Show:** the three PDFs side by side, or just the app's Overview page.

**Say:**

> The problem is that important facts are scattered across documents. The same
> number appears in three places, worded three different ways. Two figures look
> like they contradict each other, but actually one covers a quarter and the
> other a full year.
>
> I was given three documents about the same company — a prospectus from 2022,
> an annual report from 2024, and an investor presentation. About 227 pages.
>
> A person can read these and spot the connections. It does not scale.

### 0:45 – 2:00 · The one idea everything is built on

**Show:** the fact schema — either the table in the README, or a fact expanded
in the app.

**Say:**

> Here is the idea the whole system rests on. **You cannot compare two
> sentences. You can compare two structured claims.**
>
> Take a sentence like "Revenue for the year ended 31 March 2024 was 1,240
> crore on a consolidated basis." As text, all you can do is measure how
> similar it looks to another sentence. That tells you nothing about whether
> they agree.
>
> So I break it into slots. Subject: the company. Attribute: revenue. Value:
> 1,240 crore. And then the part most systems skip — the **qualifiers**. The
> period: April 2023 to March 2024. The scope: consolidated.
>
> Those qualifiers are the whole game. Revenue of 1,240 and revenue of 980 do
> not disagree if one is this year and one is last year. Without capturing
> period, scope and as-of date, every difference looks like a contradiction and
> the system is useless.
>
> And every fact carries proof: the exact sentence, the document, the page, and
> the character positions.

### 2:00 – 3:30 · Walk through the pipeline

**Show:** the pipeline table in the README, or the file list
`step_01` through `step_07`.

**Say:**

> There are seven stages. Each one saves to the database before the next runs,
> so if something crashes I keep everything up to that point.
>
> **Stage one reads the PDF.** I use PyMuPDF for text and pdfplumber for
> tables. One thing I found early: the obvious way to read a PDF is wrong. If
> you ask the library for text "blocks", it merges across columns. On the
> annual report's infographic page it returned "Count of 46-ft tractors 98,135"
> — that glued a label from one tile onto a number from a different tile. Every
> fact from that page would have been wrong and nothing would have crashed.
>
> So I work at the **line** level instead and group the lines myself, using
> their positions on the page. Same column, small vertical gap, they belong
> together.
>
> **Stage two cuts pages into passages** and — this is important — staples the
> context onto each one. If a passage says "98,135, Workforce strength", that
> is useless on its own. There is no date and no definition. But the footnotes
> at the bottom of the page say "as of March 31, 2024" and "includes permanent
> employees, contractual workers and last-mile partner agents". So I attach
> those. Now the model can fill in the date and the scope.
>
> **Stage three sends each passage to the AI** and asks for structured claims.
> The prompt never mentions revenue or headcount or logistics — it says "find
> claims about named things" — because the system has to work on documents it
> has never seen.
>
> **Stage four is the important one.** The model must copy its evidence word for
> word. I then search for that exact string in the passage. If it is not there,
> the fact is rejected.
>
> **Stage five makes things comparable.** "1,240 crore" becomes 12.4 billion.
> "Year ended 31 March 2024" becomes a date range. "Delhivery Ltd" and
> "Delhivery Limited" become one entity.
>
> **Stage six finds pairs worth comparing.** Comparing every fact to every
> other would be 4.5 million pairs. I group them by entity and attribute, which
> gets it down to 8,366.
>
> **Stage seven decides what each pair means.**

### 3:30 – 5:00 · Show the four cases (the main event)

**Show:** the app, "The four cases" page. Expand each one.

**Say, for case 1:**

> Here the annual report says freight costs were 59,707.49 million rupees. The
> presentation says 5,971 crore. Different documents, different wording,
> **different units**. The system normalised both and found they agree to
> nought point nought nought percent. And you can see the exact quote and page
> for each one.

**Say, for case 2:**

> Here is a real disagreement. EBITDA margin: the annual report says 1.56
> percent as at March 2024, the presentation says 1.6 percent for the same
> year. Same company, same measure, same period, two documents, and they do not
> match.

**Say, for case 3 — spend the most time here:**

> This is my favourite one. Two figures for net IPO proceeds: 8,703 million and
> 8,863 million. They look like a contradiction.
>
> The system does not just say "these differ". It takes the gap — 160.03
> million — and goes looking through everything it knows for a documented
> quantity of exactly that size. And it finds one: "un-utilised IPO expenses of
> 160.03 million had been transferred to net IPO proceeds."
>
> So it can show the arithmetic: 8,703 plus 160.03 equals 8,863. The gap is a
> real, documented thing, not a discrepancy.

**Then show a `superseded` example:**

> And here is a fifth verdict I added. A director was appointed in 2020 and
> resigned in 2021. That is **not** a contradiction. The documents agree
> perfectly — the world changed between them. Telling "these documents
> disagree" apart from "this thing changed" is the difference between a useful
> tool and an alarm that goes off constantly.

### 5:00 – 6:00 · Be honest about the failures

**Show:** case 4 on the same page.

**Say:**

> Twenty-three percent of extracted facts were rejected. I want to explain why
> that number is there and not hidden.
>
> When I asked the model to quote a table row, it did something reasonable but
> wrong. Instead of quoting the whole row, it glued the row's label to the one
> value it meant — "Bad debt written off, newline, 0.02". The real page says
> "Bad debt written off, pipe, 0.02, pipe, 0.44". So the string it gave me does
> not exist anywhere in the document.
>
> A thousand facts like that. All plausible. All helpful-looking. All invented.
> The grounding check caught every one.
>
> The tempting fix was to relax the check — accept evidence if the pieces
> appear near each other. That would have recovered a quarter of my data in one
> edit. I did not do it, because that rule cannot tell a correct pairing from a
> wrong one. **A check you weaken because it failed is no longer a check.** I
> fixed the prompt instead, and grounding went from 76 to 92 percent.

### 6:00 – 7:00 · Show it works on something new, then close

**Show:** the Upload page, or the RBI document already in the list.

**Say:**

> To test that nothing is hard-coded, I uploaded an unrelated document — a
> Reserve Bank of India annual report. Completely different organisation,
> completely different layout. It produced 77 facts and **every single one was
> proved against the source.**
>
> Nothing in the code names a company or a document. Page headers are
> recognised by font size and position, not by their words. Company identifiers
> are found by their shape — a short uppercase label followed by a long code —
> so the code has no idea what a DIN is, and would equally catch an ISIN.
>
> Even the financial year end is learned from the documents. I scan every
> phrase like "year ended 31 March" and take the most common. An Indian report
> ends in March, an American one in December, and "FY24" means different things
> in each. That convention is data, not code.
>
> The thing I would want you to take away: 99.8 percent of the verdicts came
> from rules I wrote, not from the AI. The AI extracts and explains. The
> decisions are code you can read and argue with.

---

## Part 3 — The approach, in plain English

### What the system does, step by step

1. **Read the PDF.** Get the text out, keeping track of where every piece of
   text sits on the page.
2. **Cut it into passages** small enough for an AI to read carefully, and
   attach the context each passage needs to make sense alone.
3. **Ask the AI for claims.** Give it a passage, get back structured facts.
4. **Prove each claim.** Check the quote really exists. Throw away what does
   not.
5. **Make things comparable.** Parse numbers, dates and periods; work out which
   names refer to the same thing.
6. **Find pairs worth comparing.** Group facts so we compare hundreds, not
   millions.
7. **Judge each pair.** Rules first, AI only for what rules cannot settle.

### Why it is built this way

Each stage saves to the database before the next one starts. That means:

- a crash never loses earlier work
- any stage can be re-run on its own
- you can look at the output of any stage and check it

---

## Part 4 — The libraries, and why each one

| Library | What it does | Why this one |
|---|---|---|
| **PyMuPDF** | pulls text out of PDFs with position and font size for every line | fastest, and it gives coordinates — which I need, because I group text by position rather than trusting the library's own grouping |
| **pdfplumber** | finds tables and returns them as rows and columns | PyMuPDF flattens a table into loose numbers with no idea which row or column they came from |
| **google-genai** | talks to Google's Gemini models | free tier, no credit card |
| **Pydantic** | checks the AI's JSON has the right shape | rejects malformed output before it can reach the database |
| **psycopg** | connects to PostgreSQL | plain SQL, no ORM layer to learn or fight |
| **RapidFuzz** | measures how similar two pieces of text are | fast, tiny install. I originally planned sentence-transformers but it pulls in 2 GB of PyTorch, which on a one-day project is an hour lost before writing any code |
| **Streamlit** | the web interface | a working UI in an hour; the brief asks for a simple way to inspect results, not a product |
| **python-dotenv** | loads settings from a `.env` file | keeps secrets out of the code |

**Removed:** scikit-learn. I listed it for text similarity and never used it —
RapidFuzz did the job. It is 40 MB, so leaving it in would slow every
deployment for nothing.

### AI tools used

- **Gemini 3.5 Flash Lite** — extraction and adjudication. Chosen for its daily
  allowance, not its intelligence (see the quota question below).
- **Claude (Claude Code)** — as a coding assistant throughout, for writing and
  reviewing code and for talking through design decisions.

---

## Part 5 — How each hard part works

### How text is extracted from the PDF

Three passes over each page:

1. **Lines with positions.** PyMuPDF gives every line of text with its box on
   the page and its font size.
2. **Grouping.** I put lines together into blocks myself, using two rules: they
   are in the same column, and the vertical gap between them is small. I do
   this rather than using the library's blocks because the library merges
   across columns and attaches numbers to the wrong labels.
3. **Tables separately.** pdfplumber finds tables, and I flatten each row into
   one line: `Total equity | 9,177 | 9,145`. That keeps each number attached to
   its row label and its column heading.

Things thrown away, all by how they look and never by what they say:

- **Page furniture** — smallest text on the page, pressed against the top or
  bottom edge.
- **Footnotes** are pulled out separately — small text, low on the page,
  starting with a marker like `(1)`.
- **Section headings** — largest text near the top.

### How facts are extracted

The passage goes to the AI with a prompt that has four jobs:

1. **Define a fact generally**: "a statement asserting a value, status, date or
   relationship about a named thing". No mention of any domain.
2. **Ask for the slots**, including the qualifiers.
3. **This line matters most**: *if a qualifier is not stated, return null. Never
   guess.* A guessed period invents contradictions that were never there.
4. **Demand a word-for-word quote.**

The prompt has five worked examples, all using invented companies, so the model
is not quietly taught the answers for this corpus.

The reply is forced into a fixed shape by the API's structured-output feature
and then checked again by Pydantic.

### How grounding works

Take the quote. Search for it in the passage it claims to come from.

- **Found exactly** — best case, and we record where.
- **Found with different spacing** — PDFs have odd line breaks; matching words
  in order while allowing any whitespace is still safe, because every
  non-whitespace character must match.
- **Found elsewhere on the same page** — the fact was attached to the wrong
  passage. The claim is real, only the label was wrong.
- **Not found** — rejected, and written to the failures table with the quote
  that could not be found.

The search stops at the page. Searching wider would eventually find *some*
match for anything, and a check that always passes is not a check.

### How relationships are found

Two steps, and keeping them separate matters.

**Step one: which pairs are worth looking at?** Group facts by entity plus
attribute family. Two facts can only agree or disagree if they are about the
same thing and measuring the same property. This takes 4.5 million possible
pairs down to 8,366.

**The rule I got right by thinking about it first:** never group by date. It is
tempting — facts from different dates are not comparable, so why generate the
pair? But that is backwards. A director active in 2022 and resigned in 2024
have different dates, and that pair is exactly what I want to find. Grouping
decides *what gets compared*. The rule tree decides *what the comparison means*.

**Step two: what does each pair mean?**

### How comparison works

For each pair, walk down a list of questions in a fixed order:

```
different currencies?            → NOT COMPARABLE
one is a percentage, one is not? → NOT COMPARABLE
different periods?               → EXPLAINED  (FY23 vs FY24)
different points in time?
    and it is a status?          → CHANGED    (appointed, then resigned)
    and it is a number?          → EXPLAINED
different scope?                 → EXPLAINED  (counting different things)
numbers agree within 0.5%?       → AGREE
ratio is exactly 1000x?          → EXPLAINED  (our own units bug, not their error)
one value says "more than X"?    → EXPLAINED  (a bound is not a measurement)
does a third fact explain the gap? → EXPLAINED, with arithmetic
──────────────────────────────────────────
nothing left                     → DISAGREE
```

**The order is the argument.** Every branch asks: is there a stated reason
these could differ without either being wrong? Only when all of them are
exhausted may the system say "disagree".

If you checked the numbers first and the context second, you would report a
contradiction for every pair of figures covering different years.

### How the derivation check works

When two numbers disagree, look for a third fact worth exactly the gap.

63,713 and 98,135 differ by 34,422. If the documents record 34,422 somewhere
about the same entity, then the gap is a documented quantity, not a
discrepancy. The system can then show `63,713 + 34,422 = 98,135`.

**Three rules stop it proving nonsense**, and I needed all three:

- the gap must be at least 50 (small integers are everywhere)
- **exactly one** fact may match the gap — if several do, it is coincidence
- the matching fact must not be roughly equal to one of the two originals — a
  component that accounts for the whole is not a component

---

## Part 6 — The database schema, and why

Eight tables in PostgreSQL.

```
documents  →  pages  →  chunks  →  facts  →  relations
                                     ↑
                        entities, attribute_families
                                  failures
```

| Table | Holds | Why it exists |
|---|---|---|
| `documents` | one row per PDF, with a fingerprint of its bytes | the fingerprint is unique, so uploading the same file twice is caught by the database itself |
| `pages` | the cleaned text of each page | **this is the anchor.** Every character position anywhere in the system points into this text |
| `chunks` | the passages sent to the AI | each records its character range, so anything extracted from it inherits a real position |
| `facts` | the claims | four groups of columns: what it came from, the claim, the qualifiers, the proof |
| `entities` | things facts are about | with every spelling seen and any registry identifier found |
| `attribute_families` | groups of phrases meaning the same measurement | "revenue" and "revenue from contracts with customers" |
| `relations` | one row per compared pair | the verdict, **which method decided it**, and the written explanation |
| `failures` | everything that went wrong | a deliverable, not a log |

### The columns worth pointing at

**`facts.grounded`** — was the quote actually found? This one boolean is the
difference between "a model said so" and "the document says so".

**`facts.value_in_evidence`** — does the claimed number appear inside its own
quote? A separate and harder question. A quote can be real while the number
attached to it came from a different column.

**`relations.method`** — `rule`, `derivation` or `llm`. This is how I can say
99.8% of verdicts came from rules.

**`facts.confidence`** — how much to trust the source. Prose 0.90, footnote
0.85, table row 0.75, chart label 0.50. A sentence carries its own meaning; a
chart label was matched to its number by position on the page, which is a
guess.

---

## Part 7 — Decisions and trade-offs

### Why hosted PostgreSQL and not SQLite

**Chose:** Supabase.
**Why:** while building, being able to open a browser and look at the facts
table saves real debugging time. And a reviewer can inspect the data themselves.
**Cost:** every write is a network round trip. Thousands of single-row inserts
would be minutes of pure waiting. So every write is batched, 200 rows at a time,
built into the database module from the start rather than added later.

### Why lines and not the library's blocks

**Chose:** group text lines myself by position.
**Why:** the library's grouping merged across columns and attached numbers to
the wrong labels, silently.
**Cost:** about sixty lines of grouping code and two thresholds to tune. Cheap
next to wrong facts.

### Why rules first and the model last

**Chose:** a rule tree judges every pair; the model only sees what rules cannot
settle.
**Why:** rules are free, repeatable, and defensible. The same pair always gets
the same verdict, and I can point at the code.
**Cost:** the rule tree needs thresholds I had to choose and justify.

### Why capture qualifiers as text and parse them in code

**Chose:** the AI returns "year ended 31 March 2024"; my code turns that into
dates.
**Why:** the model is good at *spotting* that a phrase describes a period. My
code is better at *converting* it, because that is deterministic and identical
every run. The most important comparison in the project should not depend on
output I cannot reproduce.

### Why RapidFuzz and not embeddings

**Chose:** fuzzy string matching, plus one AI pass for meaning.
**Why:** sentence-transformers pulls in 2 GB of PyTorch. On a one-day project
that is an hour gone before writing any code.
**Cost:** spelling similarity cannot connect "workforce strength" to "team
size". That is why there is one AI pass over the attribute labels afterwards,
which is deliberately conservative.

### Why no test suite

**Chose:** no tests; instead every stage prints a summary to read, plus inline
checks only where a bug would be **silent**.
**Why:** a deliberate trade on a one-day clock.
**Cost, stated honestly:** a bug in an early stage can hide until a much later
one. That is exactly what happened twice.
**Where I did keep checks:** character offsets, and a 38-case table for the
number and date parsers — because those bugs do not crash, they just quietly
produce wrong numbers.

---

## Part 8 — Problems I hit

Thirty-seven are written up in `docs/04_FAILURES.md`. These five are the ones
worth telling.

### 1. The model invented its evidence — 1,014 times

Asked to quote a table row, it glued the row's label to the one value it meant.
`"Bad debt written off\n0.02"` — a string that appears nowhere in the document.
Every one was caught by the grounding check.

**Fixed** by changing the prompt, not the check — and what worked was showing
three *wrong* answers verbatim. Describing the rule in prose had not been
enough. 76% → 92%.

### 2. My derivation check proved anything I asked it to

First run: 1,223 "arithmetic proofs". A gap of 4 between 164 and 160 processing
centres was proven by an unrelated quantity of 4 called "Stores and spares".

My own code comment had predicted this — *"an explanation that can always be
found explains nothing"* — and it happened anyway.

**Fixed** by requiring uniqueness: if more than one fact matches the gap, it is
coincidence. 1,223 → 43, and the survivors are real.

### 3. A word boundary made numbers a billion times too small

`>2.8Bn` parsed as **2.8**. Digits count as word characters, so the regex
`\bbn\b` found no boundary between `8` and `Bn` and matched nothing. Nothing
crashed. The number was simply wrong.

**Caught** by the parser check table, which exists for exactly this reason.

### 4. The PDF library glued numbers to the wrong labels

Described above. Found by dumping the raw output and reading it by eye before
writing any extraction code — not by any check, because no check existed that
could have caught it.

### 5. I planned the whole API budget on a guessed quota

I assumed 250 requests a day. The real limit was **20**. One document exhausted
it. Found only when I looked at the provider's usage dashboard.

**Fixed** by switching to a model with 500 a day — and turning the constraint
into a design: the cheap high-allowance model does the bulk work, and the
scarce good model is saved for the few genuinely hard judgements.

---

## Part 9 — What works, what does not, what is next

### What works

- Reading PDFs, including multi-column pages and infographics
- Capturing footnotes and attaching them to the numbers they qualify
- Proving 77% of facts against the source, and rejecting the rest
- Normalising units, dates and periods so figures can be compared
- Finding all four required cases, including one with arithmetic proof
- Working on an unseen document from a different organisation (100% proved)
- 99.8% of verdicts from deterministic rules

### What does not work yet

**Grounding proves the text, not the pairing.** A fact can quote a real table
row containing three numbers and no headings. The quote is honest; the pairing
may still be wrong. I measure a weaker version of this — 98.4% of facts have
their value inside their own quote — but it does not close the gap.

**Undetected tables force a guess.** On financial-statement pages with no
ruling lines, pdfplumber finds no table at all. Labels and numbers survive as
separate columns and the model lines them up by position. Most of the 23%
rejection rate is this.

**1,235 contradictions is more than are real.** Many are facts sharing an
attribute family that should not, or figures with no stated period. The four
cases view applies stricter filters to surface credible ones, but the raw
number overstates.

**Attribute grouping is shallow.** Spelling similarity plus one conservative AI
pass. Real semantic grouping would connect far more.

**No entity disambiguation by identifier at scale.** I extract DINs and CINs
and use them, but matching mostly still falls back to names — and "Delhivery
Limited" and "Delhivery Private Limited" are, strictly, different registered
entities.

### What I would build next, in order

1. **Reconstruct tables from coordinates.** I already have every line's x and y
   position from PyMuPDF. Aligning label lines with number lines by vertical
   position would fix the single biggest source of both rejections and wrong
   pairings. This is the highest-value change by a distance.
2. **Verify the pairing, not just the text.** For a fact from a table row,
   check the value sits in the column whose heading matches the claimed period.
3. **Proper semantic grouping of attributes**, using embeddings, so "workforce
   strength" and "team size" connect without an AI pass.
4. **Rank contradictions by credibility** rather than listing them flat, so the
   real ones surface without needing a hand-written filter.
5. **Read the API quota from the provider at startup** and refuse to begin a
   run that cannot finish.

---

## Part 10 — Fifteen questions, with answers

### 1. What is a "fact" in your system?

A claim broken into slots: subject, attribute, value, unit, plus the qualifiers
— period, as-of date and scope — plus the exact quote it came from and where
that quote sits.

The reason for the slots is that sentences cannot be compared. You can only
measure whether two sentences *look* alike, which says nothing about whether
they *agree*. Once both are in slots you can line them up field by field and
say exactly which field differs.

### 2. How do you know the AI did not just make it up?

It has to copy the evidence word for word, and I search for that string in the
passage it came from. No match, no fact.

That is not theoretical — it rejected 927 facts, 23% of everything extracted.
They were all plausible and all invented. Without that check they would have
flowed straight into the comparison stage.

### 3. How is this not just a wrapper around an LLM?

Three answers.

**The AI does not make the decisions.** 99.8% of verdicts came from a rule tree
I wrote. Every relation records which method decided it, so that is a number,
not a claim.

**The AI's output is not trusted.** Every fact is checked against the source
and 23% were thrown away.

**The hard parts are not AI at all.** Grouping text by position on the page,
parsing "1,240 crore" into a number, working out that "FY24" means April to
March, deciding which pairs are even worth comparing — that is all ordinary
code.

The AI does two things: it reads a passage and fills in slots, and it writes an
explanation for the few pairs rules cannot settle.

### 4. Why not compare every fact to every other?

4.5 million pairs, and almost all of them between facts with nothing to do with
each other.

So I group first, by entity and attribute family. That is a cheap filter on
something every genuine comparison must share. 4.5 million becomes 8,366.

The rule I am most pleased about here is what I *did not* put in the grouping
key: the date. It is tempting, because facts from different dates are not
comparable. But a director active in 2022 and resigned in 2024 have different
dates, and that pair is the whole point. Grouping decides what gets compared;
the rule tree decides what the comparison means.

### 5. Why Supabase and not SQLite?

Being able to open a browser and look at the facts table while building saved
real debugging time, and a reviewer can inspect the data themselves.

The cost is real: every write is a network round trip, so thousands of
single-row inserts would take minutes. Every write is batched 200 rows at a
time, built into the database module from the first line rather than added as
an optimisation later.

For a single-user tool with no reviewer, SQLite would have been the right call.

### 6. How do you handle different units?

Two figures might be "1,240 crore" and "12,400 million" and mean the same
thing. So every number is parsed into a canonical value: the digits, times a
multiplier from the scale word.

The parser handles crore, lakh, million, billion, thousand, percentages,
accounting brackets for negatives, and figures written as bounds like ">2.8Bn".

I keep the multiplier we used on every fact, so the arithmetic can be audited
afterwards. And currency is never converted — two amounts in different
currencies are marked not comparable, because the documents do not give an
exchange rate.

### 7. How do you know "FY24" means April to March?

I do not hard-code it — that would break on an American document where it means
January to December.

Instead I read it from the documents. Every phrase like "year ended 31 March
2024" states its own year end explicitly. I collect all of them and take the
most common month and day. That becomes the convention used to interpret bare
"FY24" references.

The convention is input, not code.

### 8. What happens with a PDF you have never seen?

I tested exactly this. I uploaded a Reserve Bank of India annual report —
different organisation, different layout, different subject. It produced 77
facts and every single one was proved against the source.

Nothing in the code names a company or a document type. Page headers are found
by font size and position. Footnotes by size, position and a leading marker.
Identifiers by shape — a short uppercase label followed by a long alphanumeric
code — so the code does not know what a DIN is and would equally catch an ISIN.

### 9. Why is a director resigning not a contradiction?

Because the documents agree perfectly. One says he was appointed in 2020, the
other says he resigned in 2021. Both are true. The world changed between them.

I gave that its own verdict — `superseded` — rather than folding it into
"reconciled". Telling "these documents disagree" apart from "this thing
changed" is the difference between a useful tool and an alarm that goes off
constantly. It also caught the company renaming itself from Private Limited to
Limited.

### 10. What was your worst bug, and how did you find it?

The derivation check. When two numbers disagree, it looks for a third fact
worth exactly the gap, so it can show the arithmetic.

The first run produced 1,223 "proofs" and most were nonsense. A gap of 4
between 164 and 160 processing centres was proven by an unrelated quantity of 4
called "Stores and spares". A gap of 15 between two percentages was proven by
something called "Wood".

With two thousand numbers about one company, *some* number sits near any gap
you choose.

I found it by not trusting my own good result. 1,223 looked too high, so I
printed a random sample and read them. The fix that mattered was not a tighter
tolerance — it was **uniqueness**: if more than one fact matches the gap, it is
coincidence, not evidence. 1,223 became 43, and those are real.

The lesson: a proof that always succeeds is not a proof.

### 11. Your grounding rate is only 77%. Isn't that bad?

It is the number I am most confident in, because I know exactly what the other
23% is.

Almost all of it comes from financial-statement pages where the table detector
finds no table — no ruling lines, no clean column gaps. The labels and numbers
end up as separate columns of text, and the model has to line them up by
position. That is a guess, and grounding refuses to certify a guess.

So the 23% is not the check failing. It is the check working on facts that were
built on guesswork.

The fix is to reconstruct those tables myself from the line coordinates I
already have. I know how to do it; it did not fit the time budget.

### 12. You report 1,235 contradictions. Are they all real?

No, and I would not claim otherwise. Most are artefacts.

Two main causes. Some are facts grouped into the same attribute family that
should not be. Some are figures with no stated period at all, where the system
has nothing to tell it they cover different times.

That is why the four cases view uses stricter criteria than the raw verdict:
both facts must state the same moment, both must come from sources with decent
confidence, both values must appear inside their own evidence, and the
difference must be modest — between 1% and 50%. A 99% gap almost always means
two unrelated things were compared.

Ranking contradictions by credibility instead of listing them flat is the
fourth thing on my list to build.

### 13. Why not use embeddings or a vector database?

Two reasons, one practical and one about the problem.

Practically, sentence-transformers pulls in about 2 GB of PyTorch. On a one-day
project that is an hour gone before writing any code.

More importantly, the matching I actually need is not fuzzy. Two facts must be
about the same entity and the same measurement, and that is a question about
identity, not similarity. Semantic similarity would tell me that "revenue" and
"profit" are related, which is exactly the wrong answer — they are related and
must never be compared.

Where I do need meaning rather than spelling — connecting "workforce strength"
to "team size" — I make one AI call over the attribute labels, and the prompt is
explicit that a wrong merge is worse than a missed one.

### 14. Why did you not write tests?

A deliberate trade, and I would defend it for a one-day project while admitting
the cost.

Instead, every stage prints a summary I read: row counts, samples, anything
suspicious. And I kept checks in exactly two places, where a bug would be
**silent** rather than loud: character offsets, verified after storage rather
than in memory, because in-memory would be circular; and a 38-case table for
the number and date parsers.

That table earned its place immediately. It caught a regex bug where `>2.8Bn`
parsed as 2.8 — a number a billion times too small, with nothing raised.

The honest cost: a bug in an early stage can hide until a much later one. That
happened twice.

### 15. What would you build next?

**Reconstructing tables from coordinates**, by a distance. I already have every
line's position from PyMuPDF. Aligning label lines with number lines by
vertical position would fix the biggest source of both rejections and wrong
pairings in one change.

Then verifying the pairing rather than just the text — checking a table-row
value sits in the column whose heading matches its claimed period.

Then proper semantic grouping of attributes, ranking contradictions by
credibility, and reading the API quota from the provider at startup so a run
that cannot finish never starts.

---

## Part 11 — Things to remember while talking

**Do not oversell.** The strongest thing about this project is that it knows
what it does not know. Saying "23% were rejected and here is exactly why" is
more convincing than any success number.

**When you do not know, say so.** "I did not test that" is a fine answer.
Guessing is not.

**Lead with the qualifiers.** If you have one minute, the thing that separates
this from a normal extraction project is capturing period, scope and as-of date
— because that is what makes the difference between a contradiction and a
difference that context explains.

**Have one number ready:** 99.8% of verdicts came from rules, not the AI.

**Have one story ready:** the derivation check that proved anything you asked
it to, and how you found it by not trusting your own good result.
