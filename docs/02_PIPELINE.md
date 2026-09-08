# 02 — The Pipeline, With A Worked Example

This document follows **one single fact** from a raw PDF all the way to a verdict
on screen. If you read only one document to understand the project, read this one.

> **Note on the example.** The walkthrough below uses an invented company, so
> that every stage can be shown cleanly without a real page's clutter. The
> mechanism is exactly what runs.
>
> **For real, verified output from these documents, see the four cases in the
> [README](../README.md).** The clearest is a reconciliation the system proved
> arithmetically on its own:
>
> ```
> net IPO proceeds  ₹8,703.00 million
> net IPO proceeds  ₹8,863.03 million
> gap:              ₹  160.03 million
>
> found independently elsewhere in the corpus:
>   "un-utilised IPO expenses of ₹160.03 million had been transferred
>    to net IPO proceeds, thereby increasing…"
>
> → RECONCILED.   8,703.00 + 160.03 = 8,863.03
> ```
>
> And a corroboration across two documents in different units:
> `₹59,707.49 million` (annual report p36, prose) against `₹5,971 Cr`
> (presentation p17, table row) — agreeing to 0.00%.

---

## The scenario

Two documents from the same company say different things about how many people
work there.

- Document A, an annual report, says **98,135**.
- Document B, an investor presentation, says **63,713**.

Both refer to the same date. A naive system reports a contradiction. It would be
wrong. Here is how Ground Work works out why.

---

## Step 1 — Ingest

We open the PDF and pull out **text blocks**, not lines. A block is a visual chunk
of text with a position on the page.

What comes back from page 2 of Document A:

```
block 1   position (410, 120)-(495, 165)   text = "98,135"
block 2   position (400, 170)-(520, 190)   text = "Workforce strength"
block 3   position (408, 160)-(418, 170)   text = "(1,5)"
block 9   position ( 60, 690)-(540, 705)   text = "(1) As of March 31, 2024"
block 13  position ( 60, 780)-(620, 795)   text = "(5) Includes permanent
                                                    employees, contractual
                                                    workers and last mile
                                                    delivery partner agents"
```

Five separate blocks. Nothing yet connects the number to its label, or the label
to its footnotes.

**Why blocks and not lines?** Because this page has multiple columns. If you read
it line by line from left to right, you splice unrelated columns together and get
nonsense. Reading by block keeps each column intact.

Tables are handled separately, by a different library, because plain text
extraction turns a table into a meaningless stream of loose numbers — `1240 980
620 415` with no idea which row or column each belonged to.

---

## Step 2 — Chunk

Now we assemble a chunk that can stand on its own. Three jobs:

1. Group blocks into columns using their horizontal positions.
2. Spot footnote markers like `(1,5)` and look up what they point to.
3. Paste that context onto the front of the chunk.

The chunk we actually store:

```
doc:        02-annual-report-fy24
page:       2
kind:       prose
chars:      1204-1580

text:
  [SECTION: FY24 Highlights]

  98,135 (1,5)
  Workforce strength

  [FOOTNOTE 1]: As of March 31, 2024
  [FOOTNOTE 5]: Includes permanent employees, contractual workers
                and last mile delivery partner agents
```

**This step decides whether the whole project works.** The number sits in the
middle of the page; the rules for reading that number sit at the bottom in tiny
type. If we chunk them apart, the LLM sees a bare number with no date and no
definition, and later we report a contradiction that does not exist.

---

## Step 3 — Extract

The chunk goes to the LLM with a **generic** prompt. The prompt never mentions
headcount, or employees, or logistics. It says: find claims, fill these slots,
quote your evidence exactly.

What comes back:

```
subject      : Example Company Ltd
attribute    : workforce strength
value_raw    : 98,135
unit         : people
as_of_date   : March 31, 2024
scope_note   : Includes permanent employees, contractual workers
               and last mile delivery partner agents
evidence     : 98,135
source_kind  : highlight_tile
```

The LLM did three things rules could not do: it recognised "Workforce strength"
as an attribute name, it pulled the date out of footnote 1 into `as_of_date`, and
it pulled footnote 5 into `scope_note`.

---

## Step 4 — Ground

Cheap, and non-negotiable. Does the string `98,135` actually appear in the chunk
text?

```
searching chunk for the exact evidence string...
found at chunk offset 47   ->   grounded = TRUE
```

If the LLM had returned `98,315` — digits transposed, which happens — the search
fails and the fact is rejected and logged.

**This is the line between "an LLM said so" and "the document says so, right here,
at this character."** It is the single most important credibility feature in the
project.

---

## Step 5 — Normalize

Turn human writing into machine-comparable values.

| Slot | Before | After |
|---|---|---|
| value | `"98,135"` | `98135` |
| as_of | `"March 31, 2024"` | `2024-03-31` |
| subject | `"Example Company Ltd"` | `entity_id = ent_001` |
| attribute | `"workforce strength"` | `attribute_family = headcount` |
| scope | free text | `scope_tags = [permanent, contractual, partner_agents]` |

**Attribute family matters more than it looks.** "Workforce strength", "team
size", and "permanent employees on the rolls" are three phrasings of the same kind
of thing. If we only ever compare exact attribute strings, they never meet, and we
find zero contradictions in the entire corpus.

We build the families at runtime: measure how similar the attribute phrases are to
each other, group the close ones, and let the LLM name each group. Nothing is
hard-coded.

---

## Step 6 — Pair

We cannot compare every fact to every other fact. Five thousand facts would be
twelve and a half million pairs.

Instead we **block**: group facts by `entity_id + attribute_family`.

```
bucket:  ent_001 | headcount
  |-- F-1029    98,135    annual report, page 2
  |-- F-2447    63,713    presentation, page 7
  |-- F-1882    23,381    annual report, sustainability section
```

Three facts in the bucket, so three pairs to compare. Not twelve million.

**A warning we learned the hard way:** do *not* put the as-of date in the blocking
key. If you do, a director "active" in a 2022 document and "resigned" in a 2024
document never land in the same bucket, and you lose your best cases. Dates are
something the adjudicator **reasons about**, not something the pairing step
**filters on**.

---

## Step 7 — Adjudicate

Take the pair (F-1029, F-2447) and walk the rule tree in order:

```
Same entity?                ent_001 = ent_001        -> yes, keep going
Same attribute family?      headcount = headcount    -> yes, keep going
Do units differ?            people vs people         -> no conversion needed
Do periods differ?          both empty               -> no
Do as-of dates differ?      2024-03-31 both          -> no
Do scope tags differ?       [permanent, contractual, partner_agents]
                            vs [permanent, contractual]
                                                     -> YES. Stop here.
```

Verdict: **reconciled**. Reason: different scope.

### Then the derivation check runs

A label is not proof. A reviewer can fairly ask: how do you know it is a scope
difference and not just two wrong numbers? So we go looking for a third fact that
closes the gap.

```
gap = 98,135 - 63,713 = 34,422

search all facts for:  same entity, same as-of date, value close to 34,422
found -> F-2451: "Partner agents = 34,422"  (presentation, page 7)

check:  63,713 + 34,422 = 98,135    exact match
```

The gap is a real, documented quantity. Now we are not labelling, we are proving.

### The stored result

```
fact_a       : F-2447  (63,713, "team size")
fact_b       : F-1029  (98,135, "workforce strength")
verdict      : reconciled
method       : rule + derivation
bridging_fact: F-2451

explanation  : Not a contradiction. Both are headcounts as of 31 March 2024,
               measured at different scopes. "Workforce strength" includes
               last-mile partner agents; "team size" excludes them. Confirmed
               arithmetically: 63,713 + 34,422 (partner agents, F-2451) = 98,135.
```

**That explanation is the actual deliverable.** The numbers were the easy part.

---

## Step 8 — Show it

The interface renders a relation card:

```
+---------------------------------------------------------------+
|  RECONCILED BY SCOPE                 method: rule + derivation |
+----------------------------+----------------------------------+
|  63,713                    |  98,135                          |
|  "Team size"               |  "Workforce strength"            |
|  presentation, page 7      |  annual report, page 2           |
|  as of 31-Mar-2024         |  as of 31-Mar-2024               |
|  scope: excludes agents    |  scope: includes agents          |
+----------------------------+----------------------------------+
|  Not a contradiction. 63,713 + 34,422 = 98,135                |
|  [ show source ]  [ show source ]  [ show bridging fact ]      |
+---------------------------------------------------------------+
```

Clicking "show source" jumps to page 7, character 4,412, with the sentence
highlighted. That is the grounding from Step 4 closing the loop — every claim on
screen can be traced back to ink on a page.

---

## Summary of what each step contributes

| Step | If you skip it, what breaks |
|---|---|
| 1 Ingest | Multi-column pages become word salad; table numbers lose their labels |
| 2 Chunk | Footnotes are lost, so qualifiers vanish, so you cannot produce case 3 |
| 3 Extract | Nothing is structured, so nothing is comparable |
| 4 Ground | You cannot tell a real fact from a hallucination |
| 5 Normalize | "1,240" and "1240.0" look different; "FY24" cannot be compared to a date |
| 6 Pair | Millions of comparisons, or the wrong facts never meet |
| 7 Adjudicate | You have facts but no knowledge — this is where the value is |
