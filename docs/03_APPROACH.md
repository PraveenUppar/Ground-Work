# 03 — Approach Log

Every real decision made in this project, and the reasoning behind it.

**Why this file exists:** in an interview, "we used Supabase" is a weak answer.
"We used Supabase over Neon because we needed a table viewer while building, and
here is the cost that choice imposed and how we handled it" is a strong one. This
file is where those answers get written down while the reasoning is still fresh.

**Format for each entry:**
> **The decision** · what we were choosing between · what we picked · why · what
> it costs us

---

## D-01 · What counts as a fact

**Choosing between:** storing sentences and comparing them with text similarity,
versus breaking each claim into structured slots.

**Picked:** structured slots — subject, attribute, value, unit, plus qualifiers
(period, scope, as-of date), plus evidence and location.

**Why:** two sentences cannot be meaningfully compared. Text similarity tells you
that two sentences *look* alike, not whether they *claim* the same thing. Slots
let you line claims up field by field and say exactly which field disagrees.

**Cost:** extraction becomes harder and slower, because the LLM has to fill nine
fields instead of copying a sentence. Worth it — everything downstream depends on
it.

---

## D-02 · Qualifiers are first-class fields

**Choosing between:** subject/attribute/value only, versus also capturing period,
scope, as-of date and unit.

**Picked:** capture the qualifiers, and allow them to be empty.

**Why:** the qualifiers are the *entire* mechanism behind the "apparent
contradiction explained by context" case. Without them, every value difference
looks like a contradiction, and one of the four required deliverables becomes
impossible to produce.

**Cost:** more fields to extract, normalize and reason about. Also a rule we must
enforce in the prompt: **if a qualifier is not stated in the text, return empty —
do not guess.** A guessed period is worse than a missing one, because it
manufactures fake contradictions.

---

## D-03 · Every fact must be grounded

**Choosing between:** trusting the LLM's output, versus requiring a verbatim quote
and checking it exists.

**Picked:** the LLM must copy the exact source text; we then search for that string
in the source chunk. No match means the fact is rejected and logged.

**Why:** this is the difference between "a model said so" and "the document says
so, at this character offset". It is also cheap to build — a string search — and
it hands us the required "extraction failure" case for free, because rejected
facts are exactly that.

**Cost:** we lose some real facts when the LLM paraphrases slightly instead of
copying. We keep a `grounded` flag rather than deleting, so we can count and
report them.

---

## D-04 · Rules first, LLM second

**Choosing between:** asking the LLM to judge every pair, versus a deterministic
rule tree with the LLM as fallback.

**Picked:** rule tree first. The LLM only handles pairs the rules cannot settle,
and writes the human-readable explanation.

**Why:** three reasons. It is cheap (we are on a free API tier). It is repeatable
(the same pair always gets the same verdict). And it is defensible — we can state
what share of verdicts came from rules rather than from a model's opinion, which
is the first thing a reviewer will ask.

**Cost:** the rule tree needs thresholds we have to choose and justify.

---

## D-05 · Hosted Postgres (Supabase), not local SQLite

**Choosing between:** SQLite (a local file), Neon (hosted Postgres), Supabase
(hosted Postgres plus extras).

**Picked:** Supabase.

**Why:** while building, being able to open a browser and *look* at the `facts`
table is worth real debugging time. Supabase has that built in; Neon does not.
Supabase also gives file storage in the same account if we need it. And for the
submission, a hosted database means a reviewer can inspect the data themselves.

**Cost — and this is a real one:** every write becomes a network round trip.
Hundreds of chunks and thousands of facts written one row at a time would be
thousands of round trips and minutes of pure waiting. Mitigation: **all writes are
batched** (200 rows at a time by default), built into `shared/database.py` from
the start rather than added later. Second cost: if the internet drops mid-run, the
pipeline stops. Mitigation: we write results incrementally, so a crash costs one
batch, not the whole run.

---

## D-06 · The LLM cache lives on local disk, not in the database

**Choosing between:** caching responses in Postgres, versus as files on disk.

**Picked:** files on disk, in `data/llm_cache/`, keyed by a hash of the chunk text
plus a prompt version string.

**Why:** we are on a free API tier with a daily request limit, and we will re-run
the pipeline dozens of times while developing. Without a cache we burn the daily
quota by mid-afternoon and lose the ability to work. Disk is instant and free;
Postgres would add a network round trip to something that needs to be fast, and
would bloat the database with text we never query.

**Note:** the prompt version is part of the cache key on purpose. Improving the
prompt should correctly invalidate old answers rather than silently reusing them.

---

## D-07 · Fuzzy text matching, not sentence-transformers

**Choosing between:** `sentence-transformers` (proper embeddings) versus
`rapidfuzz` plus TF-IDF character n-grams.

**Picked:** rapidfuzz plus TF-IDF for now. Embeddings only if time allows.

**Why:** `sentence-transformers` pulls in PyTorch, roughly 2 GB of download. On a
24-hour clock that is potentially an hour lost before writing a single line of
code. For our actual need — grouping short attribute phrases like "workforce
strength" and "team size", and matching company name variants — character n-gram
similarity performs well and installs in seconds.

**Cost:** we lose true semantic matching. "Turnover" and "revenue" share no
characters, so fuzzy matching will not connect them. Mitigation: Gemini also
offers an embeddings endpoint on the same free key, so we can upgrade this one
function later without installing anything.

---

## D-08 · Blocking key excludes dates

**Choosing between:** blocking on `entity + attribute_family + as_of_date`, versus
`entity + attribute_family` only.

**Picked:** entity plus attribute family only.

**Why:** this was nearly a serious bug. If the as-of date is part of the key, two
facts with different dates never land in the same bucket and never get compared.
That silently destroys the two most interesting cases — a director active in 2022
and resigned in 2024, or FY23 revenue against FY24 revenue.

**The general rule:** never block on a field the adjudicator needs to reason
about. Blocking decides what *gets compared*; the rule tree decides what the
comparison *means*.

**Cost:** larger buckets, so more pairs to adjudicate. Still hundreds, not
millions.

---

## D-09 · Numbered files for pipeline order

**Choosing between:** flat descriptive filenames, one folder per step, or numbered
files in one package.

**Picked:** numbered files — `step_01_ingest_pdf.py` through
`step_07_adjudicate_pairs.py`.

**Why:** listing the folder tells you the pipeline order without reading any code
or documentation. Folders-per-step would mean navigating eight directories that
mostly hold one file each.

**Cost:** renumbering is annoying if a step gets inserted in the middle. Accepted —
the pipeline shape is settled.

---

## D-10 · Nothing document-specific in the code

**Choosing between:** speed (hard-code what we see in the sample PDFs) versus
generality.

**Picked:** generality, strictly. No company names, no filenames, no "if the
heading says Revenue then...".

**Why:** the task brief explicitly forbids it and says the solution will be tested
with additional PDFs. Anything document-specific must be **read from the document
at runtime** — for example, extracting a glossary page into an alias table — never
written into the source code.

**Cost:** slower to get the first good result, and the sample documents will
expose weaknesses we cannot patch with a shortcut. That is the point.

---

## D-11 · Foreign keys only where the insertion order is obvious

**Choosing between:** full referential integrity on every link between tables,
versus foreign keys only where they cannot get in the way.

**Picked:** foreign keys on `doc_id` and `chunk_id`, none on `facts.entity_id` or
`facts.attribute_family`.

**Why:** a chunk genuinely cannot exist before its document, so the database
should enforce that — it catches real bugs. But `entity_id` and
`attribute_family` are filled in *later*, during normalisation, once we have seen
enough facts to work out which names refer to the same thing. Putting a foreign
key there would force facts to wait for entities that do not exist yet, turning a
natural two-pass design into an ordering puzzle.

**Cost:** nothing stops a typo writing an `entity_id` that does not exist. We
accept that, and check it in the normalisation tests instead.

---

## D-12 · Claude is denied access to the `.env` file

**Choosing between:** letting the assistant create and edit `.env` like any other
file, versus blocking it.

**Picked:** blocked, via a `deny` rule in `.claude/settings.local.json`.

**Why:** `.env` holds the database password and the API key. Anything Claude reads
enters the conversation, and conversations get scrolled through, screen-shared and
pasted into other places. There is no reason for the assistant to ever see those
values — it only needs to know whether they are *set*, which
`config.hide_middle_of_secret` answers safely.

**Cost:** the user has to copy the template and fill it in by hand. That is about
sixty seconds, once.

---

## D-13 · No test suite; visible output instead

**Choosing between:** a test file per step, tests only on the risky parts, or no
test files at all.

**Picked:** no test files. Every step's script instead prints a summary the user
reads: how many rows it produced, a handful of samples, and anything that looks
wrong. Inline checks are kept only where a bug would be *silent* rather than
loud.

**Why:** a deliberate trade on a 24-hour clock. Writing and maintaining tests
across twelve steps is roughly two hours, and the value of a test is highest when
code will be changed repeatedly over months — which is not this project's
situation.

**Cost, stated honestly:** a bug in an early step can stay hidden until a much
later one, where it is far more expensive to find. Character offsets are the
clearest example: get them wrong in Step 2 and nothing crashes — the evidence
quotes simply point at the wrong text, and you may not notice until you open the
interface in Step 10. That specific risk is why offsets keep an inline check even
though the test suite is gone.

**Note:** the one test file that already exists (`tests/test_database_connection.py`)
stays. It is written and it works; deleting it would cost time and gain nothing.

---

## D-14 · Build on lines and regroup them ourselves, not on the library's blocks

**Choosing between:** trusting PyMuPDF's `get_text("blocks")`, versus taking the
finer `lines` output and grouping it into visual blocks with our own rules.

**Picked:** lines, regrouped by position.

**Why:** we checked, and blocks were wrong on the most fact-dense page in the
corpus. The library merged text across columns and attached a number to a label
from a different tile. Line output on the same page was clean — every line stayed
inside one column. Grouping is then two rules: lines belong together when their
horizontal ranges overlap (or nearly touch) and the vertical gap between them is
small.

**Cost:** about sixty lines of grouping code we would not otherwise have written,
and two thresholds that have to be chosen. Cheap next to silently wrong facts.

---

## D-15 · Font size and page position, never text matching

**Choosing between:** recognising headings, footnotes and page furniture by what
they say, versus by how they look.

**Picked:** by how they look — font size plus position on the page.

**Why:** the task brief forbids document-specific rules, and a running header
saying "Delhivery Limited Annual Report 2023-24" is exactly the kind of thing it
would be tempting to filter by name. Position and size are generic: the smallest
text pressed against the top or bottom edge is page furniture in any document,
the largest text near the top is a heading, and small text in the lower fifth
beginning with a marker like `(1)` is a footnote body.

**Cost:** the thresholds are guesses tuned on three documents, and an unusual
layout will defeat them. Mitigation: when detection finds nothing, we keep the
text rather than dropping it — a wrong guess should cost us noise, never
evidence.

---

## D-16 · Chunks do not overlap

**Choosing between:** overlapping chunks (a common default, so a fact near a
boundary is not cut in half) versus clean cuts.

**Picked:** no overlap.

**Why:** we cut on the blank lines that Step 1 used to separate visual blocks,
and a block is already a unit that belongs together — a tile, a paragraph, a
table. Facts do not straddle those boundaries, so the usual reason for overlap
does not apply here. Overlap would instead produce the *same* fact twice from
two chunks, and those duplicates would later be compared against each other and
solemnly reported as corroborating. That is noise dressed up as a finding — the
same trap as failure 13.

**Cost:** a fact split across a page boundary is lost. Accepted: pages are the
natural unit of these documents, and nothing observed so far spans one.

---

## D-17 · A chunk never spans more than one page

**Choosing between:** letting a chunk run across consecutive pages to reach the
target size, versus keeping every chunk inside one page.

**Picked:** one page, always.

**Why:** a chunk's whole value is its address — a document, a page, and a start
and end character position inside that page's stored text. A chunk spanning two
pages has no single such address, and every downstream feature (evidence
display, source highlighting, offset verification) is built on there being one.

**Cost, and it is a real one:** most pages hold far less than 1,000 words, so
chunks come out well below target — a median of 346 words in the prospectus and
74 in the presentation. Chunk count is therefore driven by page count, not by
the size setting, and 227 pages produced 353 chunks rather than the ~200 the
1,000-word target implied.

Since one chunk means one API call, that puts a full run above the free tier's
daily allowance. The fix belongs in Step 5, not here: **keep the chunk as the
unit of grounding, but let one API request carry several chunks.** Decoupling
"unit of evidence" from "unit of request" preserves every offset while cutting
the request count by roughly two thirds.

---

## D-18 · Pin an exact model version, never a "latest" alias

**Choosing between:** `gemini-flash-latest`, which always points at the newest
model, versus an exact pinned version.

**Picked:** pinned — `gemini-3.7-flash`.

**Why:** the cache key is built from the prompt, our prompt version, and the
model NAME. An alias keeps the same name while the model behind it changes, so
the key would not change either, and we would go on serving answers produced by
a model we are no longer using. Every result would silently be a mixture of two
models with no way to tell which produced what.

There was also a practical reason: when tested, `gemini-flash-latest` returned
503 while `gemini-3.7-flash` and `gemini-3.6-flash` both worked.

**Cost:** we have to update the version by hand when it is eventually retired.
That is a one-line change in `.env`, and it is the change we *want* to be
deliberate about.

---

## D-19 · Model chosen by daily allowance, not by capability

**Choosing between:** the strongest model the free tier offers, versus the one
whose quota can actually carry the work.

**Picked:** `gemini-3.5-flash-lite` for all bulk extraction.

**Why:** read from the provider's own dashboard, the free tier allows **20
requests a day** on the Flash models and **500** on Flash Lite. Twenty cannot
process a single document. The strongest model available is worthless if its
allowance cannot reach the end of the corpus.

This turns the constraint into a design instead of a workaround: the cheap,
high-allowance model does the high-volume mechanical work, and the scarce
high-quality requests are saved for Step 7, where a handful of genuinely hard
judgements need real reasoning and the volume is tiny.

**Cost:** a smaller model. Measured rather than assumed — see D-21, where it
turned out request size mattered considerably more than model size.

---

## D-20 · Where our own pipeline knows the answer, it decides — not the model

**Choosing between:** letting the model report where a fact came from, versus
deriving it from what Step 2 already established.

**Picked:** derive it, and fall back to the model only where we genuinely
cannot tell.

**Why:** `source_kind` drives our confidence score, and the two models
disagreed wildly about it — one labelled 76 facts as figure labels, the other
labelled none, calling every chart number prose. That would have given
chart-derived values (matched to an axis by pixel position, our least reliable
source) the confidence of a written sentence.

But Step 2 had already split every page into prose, tables and footnotes. We
were asking a model to re-derive something we had already established
deterministically. Now a passage cut from the tables region *is* a table row,
whatever the model calls it. The model's answer only stands inside the prose
region, where a sentence and a figure label genuinely do look alike to us.

**The general rule:** never ask a model for something your pipeline already
knows. It is slower, it costs a request, and it can be wrong.

---

## D-21 · Request size is a recall lever, not just a cost lever

**Choosing between:** packing as much as possible into each request to save
quota, versus smaller requests.

**Picked:** 1,200 words and at most 4 passages per request.

**Why:** we halved the request size expecting to trade quota for reliability,
and instead recall nearly tripled — 253 facts became 696 on identical
passages, with as-of dates rising from 12 to 226 and JSON failures dropping to
zero. Given less to read at once, the model reads it properly rather than
skimming.

There was also a hard limit involved: one oversized request produced 143,000
characters of JSON and was cut off mid-structure. What constrains a request is
not how much you send but how much comes back, and a dense table page yields
far more facts per word than prose does — so size in words is a poor proxy for
response size.

**Cost:** roughly 140 requests instead of 60. Affordable at 500 a day, and
plainly worth it.

---

## D-22 · Fix the prompt rather than relax the check

**The situation:** grounding rejected 1,014 of 4,399 facts — 23% of the corpus,
and mostly table data, where most of the numbers live. Every rejection had the
same cause: asked for a verbatim quote, the model returned an assembled one,
gluing a row's label to the single value it meant.

**Choosing between:** relaxing grounding to accept evidence whose parts all
appear within one line; accepting the 23% loss and reporting it; or fixing the
prompt and re-extracting.

**Picked:** fix the prompt.

**Why:** the relaxed check was the tempting option and the wrong one. Matching
"all these pieces appear somewhere in this line" cannot tell a correct column
pairing from a wrong one — it would have validated precisely the mistake the
check exists to catch, and it would have done so quietly, in the direction that
flatters us. **A check you weaken because it failed is no longer a check.**

The prompt was genuinely at fault. It demanded a verbatim quote without saying
what to do when a value sits in a table row among other values, and the model
resolved that ambiguity sensibly — by showing which number it meant. The fix
states the rule outright: quote the whole row, because which value you mean is
already recorded in `attribute` and `as_of_text`. The quote's only job is to
point at real text.

**What worked, and is worth repeating:** the fix that mattered was a NEGATIVE
example — showing the three wrong answers verbatim and saying they would be
thrown away. Describing the rule in prose had not been enough.

**Measured result:** grounding went from 76.4% to 92.5% on the test document,
and every surviving fact is an exact character-for-character quote — no
whitespace forgiveness, no mis-attribution recovery.

**Cost:** about 170 API requests to re-extract, and a lower fact count, because
facts that previously survived on invented evidence no longer do. That is the
correct trade: fewer facts, all of them checkable.

---

## D-23 · Spelling groups the variants; the model groups the meanings

**The situation:** fuzzy matching produced 1,047 attribute families from 1,155
phrases — correct, but barely any grouping. It reliably merges wordings that
share characters ("revenue from contract" with "revenue from contracts") and
structurally cannot merge wordings that do not ("revenue" with "revenue from
contracts with customers" scores too low; "workforce strength" and "team size"
share almost nothing).

That matters because cross-document corroboration is a required deliverable,
and a prospectus, an annual report and an investor deck do not share
vocabulary.

**Choosing between:** a containment rule ("revenue" inside "revenue growth" —
free but merges different measures), lowering the fuzzy threshold (merges
unrelated things), or asking the model.

**Picked:** one LLM pass over the family labels, after the mechanical grouping
has already done what it can.

**Why:** recognising that two differently-worded phrases name one quantity is a
judgement about meaning, and that is the one thing worth spending a model on.
The same division as everywhere else here — code does the mechanical work, the
model answers only the question code cannot.

**How it is kept safe:**
- every label goes in ONE request, so any label can be compared with any other;
  batching would let "revenue" and "turnover" land in different batches and
  never meet, which is the whole problem
- the prompt states plainly that the two errors are not equally bad: a missed
  merge loses a comparison, a wrong merge invents a contradiction that will be
  reported confidently with real evidence attached
- only labels we actually sent may be grouped, so an invented or reworded label
  cannot create a family nothing belongs to
- any failure returns nothing and leaves the spelling grouping untouched — a
  merge that half-worked would be worse than one that did not run

**Result:** deliberately conservative. 18 labels merged into 15 families — but
the `revenue` family went from one phrasing to twelve, and `din` joined
`director identification number`. Small in count, decisive in effect. `ebitda`
and `ebitda margin` were correctly left apart.

---

<!-- Add new entries below. Keep the same format. -->








