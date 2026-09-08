# 04 — Failures and Hurdles

**This is a graded deliverable, not a scratch file.** The task brief asks for at
least one honest example of an extraction or reasoning failure, what we did about
it, and what we would do with more time.

**How to use this file:** add to the *Running Log* the moment something breaks or
surprises you — one or two lines is enough. At the end, promote the five most
interesting ones into *Top 5*, written up properly. Do not try to write good
entries in the moment; just capture them before you forget.

**What makes a good entry:** a failure that reveals something about the *problem*,
not just a typo you made. "I misspelled a variable" is not interesting. "The
extractor read a number from a table whose units header was in a different chunk,
so every value was wrong by a factor of a million" is very interesting, because it
tells you something true about documents.

---

## Top 5 — the write-up

Chosen from the 37 entries in the running log below. Three were found by checks
we built on purpose; two were found only because we looked at raw output instead
of trusting a number.

---

### F-1 · The model invented its evidence, and the grounding check caught 1,014 cases

**What happened.** Every fact must carry a quote copied character for character
from its source, which we then search for in that source. On the first full run,
**1,014 of 4,399 facts (23%) were rejected** because their quote does not exist
in the document. They looked like this:

```
returned:   "Bad debt written off\n0.02"
on page:    "Bad debt written off | 0.02 | 0.44"
```

**Why it happened.** The model was being helpful, and our prompt was ambiguous.
A table row holds several values; asked to quote the row and report one value,
it *assembled* a quote showing which number it meant. Every one was plausible,
every one pointed at real content, and not one was real text. Given an ambiguous
instruction, the model made a reasonable choice — the instruction was the bug.

**How we handled it.** We fixed the prompt, not the check. The tempting fix was
to relax grounding to accept "all the parts appear within one line" — that would
have recovered a quarter of the corpus in a single edit. We did not, because
that rule cannot tell a correct column pairing from a wrong one; it would have
validated exactly the mistake the check exists to catch, quietly, in the
direction that flatters us. **A check you weaken because it failed is no longer
a check.** The prompt now states the rule outright — quote the whole row, since
which value you mean is already recorded in `attribute` and `as_of_text` — with
three *wrong* answers shown verbatim. Describing the rule in prose had not
worked; the negative examples did. Grounding went from 76.4% to 92.5% on the test
document, and every surviving quote is exact.

**With more time.** Verify the *pairing*, not just the text: for a fact from a
table row, check that the claimed value sits in the column whose header matches
the claimed period. We measure a weaker version of this already —
`value_in_evidence`, at 98.4% — but it cannot tell which of three numbers in a
row belongs to the claim.

---

### F-2 · The derivation check proved anything we asked it to

**What happened.** When two figures disagree, we search for a third documented
quantity equal to the gap. If 63,713 and 98,135 differ by 34,422 and the corpus
records 34,422, the gap is a documented thing rather than a discrepancy. The
first run produced **1,223 such "proofs"**. They were mostly nonsense:

```
Processing centers: 164 vs 160  — gap of 4 "proved" by "Stores and spares" = 4
% of revenue: 34.1% vs 18.8%    — gap of 15 "proved" by "Wood" = 15.3
```

**Why it happened.** With ~2,000 numeric facts about one entity, *some* number
sits near any gap you choose. The comment beside the function had predicted this
in advance — *"an explanation that can always be found explains nothing"* — and
it happened anyway, because the code was written to find the **closest** match
rather than to ask whether the match meant anything.

**How we handled it.** Three changes, of which only one really mattered.
Tightening the tolerance helped a little. Ignoring gaps under 50 helped, since
small integers are everywhere in a financial document. The change that mattered
was **uniqueness**: if more than one fact matches the gap, the match is
coincidence, not evidence. A second pass then caught a subtler version — a
bridging fact essentially equal to one of the two originals, meaning the other
was near zero and there was nothing to explain. **1,223 → 43**, and the
survivors are real, including net IPO proceeds of ₹8,703.00m and ₹8,863.03m
reconciled by ₹160.03m of un-utilised IPO expenses.

**With more time.** Require the bridging fact to be semantically plausible as a
component — "partner agents" can explain a gap in a headcount; "Wood" cannot.
That needs the attribute families to be much better than ours, which is the same
underlying gap as F-5.

---

### F-3 · A word boundary made numbers a billion times too small, silently

**What happened.** `>2.8Bn` parsed as **2.8**, not 2,800,000,000.
`18.8Mn Sq ft` parsed as 18.8. Nothing raised, nothing logged.

**Why it happened.** The scale-word search used `\b`, a boundary between word
and non-word characters. Digits *are* word characters, so in `2.8Bn` there is no
boundary between `8` and `Bn` and `\bbn\b` matched nothing. A two-character
mistake in a regex.

**How we handled it.** Caught by the printed check table — a list of tricky
inputs with expected outputs, the only test-like thing in the project. It exists
for exactly this file, because every bug here is silent: a missed "crore" does
not crash, it makes a number ten million times too small, and that fact then
quietly disagrees with every other statement of the same quantity. Fixed by
requiring no *letter* on either side rather than a word boundary, so `8bn`
matches while `cr` inside "increase" and `k` inside "lakh" stay ignored. The
table now runs 38 cases.

**With more time.** Extend the same treatment to the other silent-failure
surface: reconcile every extracted number against the units note of the section
it came from, so a value missing its unit is flagged rather than compared as
though it were already in base units. That gap produced F-2's second failure
mode.

---

### F-4 · The PDF library glued numbers to the wrong labels

**What happened.** On the annual report's infographic page, PyMuPDF's `blocks`
mode merged text across columns and returned:

```
"Count of 46-ft tractors 98,135 (1,5)"
```

That joins the label belonging to **753** onto the value **98,135**, whose real
label is *Workforce strength*. Every fact from the most fact-dense page in the
corpus would have been wrong, and nothing would have crashed.

**Why it happened.** We used the library's default grouping without checking it.
It is a reasonable default for single-column prose and wrong for a
multi-column layout.

**How we handled it.** Found by dumping the raw block boxes and reading them by
eye *before* writing any extraction code — not by any check, because no check
existed that could have caught it. Rebuilt on the finer `lines` output, grouping
lines into visual blocks ourselves by position. That took four further attempts,
each failing on a boundary case: comparing against a block's last line instead
of its full extent; a superscript overlapping a neighbouring column by six
points and welding two tiles together; the fix for that then orphaning a marker
overlapping its own number by two points. All twelve tiles now read correctly.

**With more time.** Reconstruct financial-statement tables from the same line
coordinates, aligning label lines with number lines by vertical position. That is
the single change that would most improve the system: it is the cause of most of
the remaining 23% rejection rate, because where no table is detected the model
has to align two columns by eye and guess.

---

### F-5 · We planned the whole API budget on a guessed quota, wrong by 12×

**What happened.** The batching architecture existed to bring a full run under an
assumed 250 requests per day. The real free-tier limit for the model we chose is
**20 requests per day and 5 per minute**. One document exhausted the allowance,
and our configured rate of 10 requests a minute was itself double what was
permitted.

**Why it happened.** The number was recalled rather than read. It was stated
confidently, and confidence is exactly what stops anyone checking.

**How we handled it.** Found only because the user opened the provider's usage
dashboard and saw 21/20. Switched the bulk work to a model allowing 500 a day —
and turned the constraint into a design rather than a workaround: the
high-allowance model does the mechanical volume, and the scarce high-quality
requests are reserved for the handful of genuinely hard judgements, where the
rule tree leaves only a few. The real limits are now written into
`.env.example` with the date they were read.

**With more time.** Read the quota from the provider's API at startup and refuse
to begin a run that cannot finish, instead of discovering it at request 21.

---

## Running log

Add a line the moment something goes wrong. Date, step, one sentence.

| # | Step | What happened |
|---|---|---|
| 1 | Step 0 | Writing a file through a Bash heredoc failed on a quoting error. Switched to the direct file-write tool. Not interesting enough for the Top 5, but logged for completeness. |
| 2 | Step 1 | **Swallowed exception hid a five-second diagnosis.** `check_database_is_reachable()` caught every exception and returned a bare `False`. "Cannot connect" has at least five very different causes, so this turned a one-line fix into guesswork. Added `try_connecting_and_describe_any_problem()`, which returns the driver's own message, plus `describe_connection_target()`, which shows the host and user with the password removed. The real cause was visible immediately on the next run. **Lesson: a boolean is the wrong return type for anything that can fail in more than one way.** |
| 3 | Step 1 | **A password containing `@` broke the connection string.** In a URL, `@` separates the password from the host. Python's `urlparse` splits at the *last* `@` and read it correctly; the database driver splits at the *first* `@` and read the tail of the password as part of the hostname. Fix is to URL-encode `@` as `%40`, or to use a password with no punctuation. Good Top 5 candidate — it is a real, general trap that has nothing to do with our documents. |
| 5 | Step 2 | **PyMuPDF's "blocks" mode glued numbers to the wrong labels.** On the annual report's infographic page, `get_text("blocks")` merged text across columns: it returned `"Count of 46-ft tractors 98,135 (1,5)"` — joining the label belonging to **753** onto the value **98,135**, which actually belongs to *Workforce strength*. It also produced `"Workforce strength >4.8Mn tonnes"`, joining a label to a different metric's value entirely. Every fact extracted from that page would have been wrong, and nothing would have crashed. Found by dumping block boxes and reading them by eye before writing any extraction code. Fixed by working at **line** level (`get_text("dict")`) and regrouping lines into visual blocks ourselves using position. **Strong Top 5 candidate: it is silent, it is general to any multi-column layout, and it was only caught by looking at the raw output instead of trusting the library.** |
| 6 | Step 2 | **Grouping compared each new line against the block's LAST line instead of the block's full extent.** A tile reads `98,135` / `(1,5)` / `Workforce strength`. Once the narrow superscript `(1,5)` (x1112–1142) was the block's last line, the label `Workforce strength` (x1002–1094) no longer touched it, so the label was orphaned into a block of its own and the number lost its meaning. Fixed by comparing against the block's full horizontal range. **A block is not its last line.** |
| 7 | Step 2 | **A superscript welded two unrelated columns together.** The marker `(3,4)` sat at x187–227: touching `>33,200` (ending x186) on its left, and overlapping `18,793` (starting x221) by *six points* on its right. Six points was enough for a plain overlap test, so two tiles merged and four values scrambled. Fixed by requiring substantial text to share at least 25% of the narrower range, while still letting genuinely narrow fragments attach by touching. |
| 8 | Step 2 | **The fix for 7 then orphaned `(1)` from `111`.** Markers with a *two-point* overlap now failed the 25% rule and fell through the "touching" exception, which only applied when there was no overlap at all. Fixed by checking the fragment exception first: whether a superscript touches, misses by a point, or clips by a point is an accident of typesetting, not a statement about which column it is in. **Two fixes in a row where the boundary case was the interesting case.** |
| 9 | Step 2 | **Ordering bug: footnotes were read before page furniture was removed.** The running footer sits *below* the last footnote, so the footnote continuation rule absorbed `2 3 Delhivery Limited Annual Report 2023-24` onto the end of footnote 5 — the very footnote that defines the scope of the headline headcount. Fixed by removing furniture first. **The bug was not in either function; it was in the order they ran.** |
| 10 | Step 2 | **An invisible control character (`\x07`) survived into a footnote body.** Python's whitespace-based text tidying does not treat BEL as whitespace, so it passed straight through. It would have silently broken the exact-match grounding check in Step 6 for no visible reason. Now stripped at extraction. |
| 11 | Step 2 | **A page's running header was extracted as a table.** pdfplumber reported `Corporate Overview \| Statutory Reports \|` as tabular data. Fixed generically: a table row needs at least two filled cells to say anything, and a table needs at least two such rows — no matching on the header's actual words. |
| 12 | Step 2 | **A footnote absorbed the table above it, because lines were not in reading order.** On the presentation's balance sheet, footnote 4 came out as `"Includes security deposits and other non-current assets Total liabilities 2,036 2,308 ... 18"`. PyMuPDF returns lines in the PDF's internal order, not top-to-bottom, so table rows sitting *above* the footnote appeared *after* it in the list and were read as continuing it. Fixed three ways: sort lines by position first, and require a continuation line to match the footnote's font size and left edge. **Same root cause as failure 9 — a rule that was correct in isolation, applied to input that was not in the order it assumed.** |
| 13 | Step 2 | **The same content appeared twice, once safely and once dangerously.** A balance-sheet page yielded a correct table (`Total equity \| 9,177 \| 9,145`) *and* a prose copy with labels in one column and numbers in another, unlinked. `Borrowings` appears twice on that page — once under non-current liabilities, once under current — so a model reading the prose copy would have had to guess, and a guess there produces a confident, fully-grounded, wrong fact. Fixed by dropping prose lines that a kept table already holds, matched cell by cell. **The dangerous output was not the missing one, it was the redundant one.** |
| 14 | Step 2 | **Open, not yet fixed: complex prospectus tables flatten badly.** Tables with stacked multi-row headers come out with the header split across rows and many empty cells (`Preference Shares \| Number of ... \|  \| Number of Equity \|  \|  \| ...`). Numbers keep some positional context but column meanings are degraded. Left as-is deliberately: the fix is a real table-structure reconstruction, which does not fit the time budget. Documented rather than hidden, and a candidate for the "what we would do with more time" section. |
| 15 | Step 3 | **Asterisk footnotes were silently losing their qualifier.** Our marker search only matched bracketed digits like `(1,5)`, so nine annual-report pages that mark their footnote with `*` contributed no scope at all. Those facts would have gone to adjudication unqualified — and an unqualified fact is exactly what turns a scope difference into a false contradiction. A bare `*` cannot be searched for in running text (it matches ordinary punctuation), so the fix uses a different property: a symbol footnote is the only one on its page and qualifies whatever sits beneath it, so it attaches to every passage on that page. Footnote coverage went from 6 chunks to 26. |
| 16 | Step 3 | **psycopg read a `LIKE '%[FOOTNOTE%'` pattern as a malformed placeholder.** The driver scans query text for `%s`-style placeholders before sending it, and `%[` is not one. Fixed by passing the pattern as a parameter instead of writing it inline — which is the correct habit anyway. Minor, but a good reminder that `%` is not an inert character in a parameterised query. |
| 17 | Step 4 | **The model list endpoint reported a model the key could not actually use.** `gemini-2.5-flash` appeared in `client.models.list()`, but `generateContent` refused it with "no longer available to new users". Listing a capability is not the same as being able to exercise it. Resolved by *testing* each candidate with a real request — including the JSON response mode we actually depend on — rather than trusting the catalogue. Three of five candidates worked; two returned 503. **Lesson: verify a dependency by using it, not by asking whether it exists.** |
| 18 | Step 4 | **Our own error message sent us hunting for the wrong bug.** A permanent 404 stopped after one attempt, but the message read "Gave up after 5 attempts" because it printed the retry *limit* rather than the attempts actually made. It implied a flaky connection when the real problem was a dead model name. Fixed to count real attempts. **An error message that misreports what happened is worse than no message.** |
| 19 | Step 5 | **Table-row evidence cannot prove which column a value came from.** A fact reading `Performance-based ESOPs = 15,060,000` quoted the whole row as evidence: `Total / 40,424,975 / 25,364,975 / 15,060,000`. The quote is real, but it contains three numbers and no column headings, so it proves the value exists on the page without proving it belongs to that attribute. **Grounding verifies the text, not the pairing** — which is the sharpest limitation of the whole grounding idea and worth stating plainly rather than hiding. Partly mitigated because the model puts the disambiguation in the attribute name. |
| 20 | Step 5 | **A column heading was mis-slotted as a scope.** `% of shares outstanding on a fully diluted basis = 5.03%` came back with `scope: "Total"`. "Total" is the column it sat under, not a qualification of what the value covers. Harmless here, but a wrong scope is worse than a missing one — Step 7 compares scopes to decide whether two figures are comparable, so a spurious one could make two identical facts look like different measurements. |
| 21 | Step 5 | **`value_raw` sometimes swallows its own unit.** PAT loss came back as `value_raw: "(1,008 Cr)"` with `unit: "Cr"` — the unit appears twice. Step 7's number parser therefore cannot assume `value_raw` is a bare number; it has to strip trailing scale words. Logged now so the parser is written for reality rather than for the tidy case. |
| 22 | Step 5 | **We planned the entire API budget on a guessed quota that was wrong by 12x.** The whole batching design existed to bring a run under an assumed 250 requests per day. The provider's own dashboard showed the real free-tier limit for the Flash models is **20 requests per day and 5 per minute** — so a single document exhausted the allowance, and our configured rate of 10 requests a minute was itself double the permitted rate. Found only because the user opened the usage dashboard. **Lesson: a quota is a fact to be read from the provider, never estimated from memory — and the cost of guessing is discovered at the worst possible moment.** Resolved by switching the bulk work to Gemini 3.5 Flash Lite, which allows 500 per day and 15 per minute on the same key. |
| 23 | Step 5 | **We broke our own "write as you go" rule, and an interrupted run proved it.** `CLAUDE.md` rule 5 says results go to the database as they are produced, so a crash at chunk 480 keeps the first 479. The extraction step instead cleared a document's facts up front and inserted only after every request had returned. When the prospectus run was stopped part-way, the completed requests were discarded — the table had been emptied and never refilled. Fixed to insert after each request. **A rule written down is not a rule followed; the code has to be checked against it.** |
| 24 | Step 5 | **We asked the model something we already knew, and it got it wrong.** `source_kind` was taken from the model's answer. Gemini 3.7 Flash labelled 76 facts `figure_label`; Flash Lite labelled zero, calling every chart number `prose`. Since `source_kind` drives our confidence score, chart-derived numbers — matched to their axis by pixel position, our least trustworthy source — would have carried the confidence of a written sentence. But Step 2 already separated each page into prose, tables and footnotes, so for two of three categories we had the answer on hand. Fixed: where our own pipeline knows, it decides; the model's answer only stands inside the prose region, where a sentence and a figure label genuinely look alike to us. **Do not ask a model to re-derive something your pipeline already established.** |
| 25 | Step 5 | **A response was 143,000 characters and came back malformed.** The failure message read "invalid JSON at character 143127", which sounds like a parser problem and is really a length problem: the answer was cut off before it finished. The limit that bites is not how much you send, it is how much comes back — and a dense table page yields far more facts per word than prose does, so request size in *words* is a poor proxy for response size. Halved the request size, and added an explicit check so an over-long answer says so plainly instead of masquerading as malformed JSON. |
| 26 | Step 5 | **Comparing two models by exact value match understated their agreement.** The comparison reported 61% overlap, but the "disagreements" were largely the same claims formatted differently — `(452 cr)` against `(452`, `30%+` against `30+`. Flash Lite moves the unit out of `value_raw`, which is arguably more correct, but **drops the closing bracket**, leaving an unmatched paren. Two lessons: a naive comparison metric can make two models look far more different than they are, and Step 7's number parser must treat a leading `(` as negative without relying on a matching `)`. |
| 27 | Step 6 | **THE BIG ONE. The model assembled its evidence instead of quoting it, and grounding caught 1,014 cases.** Asked for a verbatim quote, it returned strings like `"Bad debt written off\n0.02"`. The page holds `Bad debt written off \| 0.02 \| 0.44` — one row, two columns. The model glued the row label to the one value it meant, to show *which* number it was claiming. Helpful in intent, but that string does not exist in the document. 1,014 of 4,399 facts (23%) were rejected for this, and every one would otherwise have entered the comparison stage carrying a plausible, helpful-looking, entirely synthetic quote. **This is the clearest possible demonstration that grounding is not theatre**, and it is the required "extraction failure" case with hard evidence attached. |
| 28 | Steps 2+6 | **Undetected tables force the model to guess, and grounding catches the guess.** After fixing the prompt, grounding rose to 92.5% on the presentation but stayed at 58-68% for table facts in the prospectus and annual report. The cause is upstream: on financial-statement pages, pdfplumber finds no table at all — there are no ruling lines and no clean column gaps — so Step 2 stores loose prose columns instead, a block of labels with the numbers in a different block. The model then reconstructs `Travelling and conveyance\n822.07\n777.67` by aligning two columns **by position**, which is exactly the guess this project exists to avoid. Tested `text` and `lines+text` detection strategies; both produce worse output, splitting words into `Corporate Overvi \| ew`. **The 23% rejection rate is not grounding failing — it is grounding refusing to certify facts that were built on a positional guess.** The proper fix is to reconstruct tables ourselves from the line coordinates PyMuPDF already gives us, aligning labels and numbers by vertical position. That is several hours plus a full re-extraction, and did not fit the time budget. Diagnosed, evidenced, and left open deliberately. |
| 29 | Step 7 | **A word-boundary made numbers a billion times too small, silently.** The scale-word search used `\b` — a change between word and non-word characters. Digits *are* word characters, so in `">2.8Bn"` there is no boundary between `8` and `Bn`, and `\bbn\b` matched nothing. `>2.8Bn` parsed as **2.8** instead of 2,800,000,000, and `18.8Mn Sq ft` as 18.8. Nothing raised, nothing logged; the facts simply disagreed with every other statement of the same quantity. Caught by the printed check table, which is the only test-like thing kept in the project and exists precisely because every bug in this file is silent. Fixed by requiring no *letter* on either side rather than a word boundary, so `8bn` matches while `cr` inside `increase` and `k` inside `lakh` stay ignored. **Where a bug cannot announce itself, a table of worked examples is not optional.** |
| 30 | Step 7 | **Truncating names to build ids made two different families collide.** Ids were built by slugging a label and cutting it to 60 characters. Two distinct attributes both beginning "fair value loss on financial liabilities at fair value throu…" produced the same id and the database rejected the duplicate. Loud, at least — a primary key caught what would otherwise have silently merged two unrelated measures. Fixed by appending an 8-character fingerprint of the *full* text, keeping ids readable while making collision impossible. |
| 31 | Step 7 | **"Our Company" became a company.** A prospectus writes "Our Company holds 2,209,446 shares"; an annual report writes "the Group employs". Those are pronouns for whoever the document is about, not entity names — but entity resolution treated them as names and created phantom entities called `Company` and `Our Company`, each holding a slice of the real company's facts. Fixed with a generic list of English self-reference phrases that resolve to the document's own subject. Merged 62 stray facts back and removed two phantom entities. **The fix names no company and no document, so it survives an unseen PDF.** |
| 32 | Step 7 | **The largest entity in the corpus was labelled "Company".** After fixing 31, the display name was still chosen as the alphabetically first alias — and "Company" sorts before "Delhivery Limited". 2,002 facts were correctly grouped and then presented under the least informative name available. Fixed by choosing the most *frequent* spelling and excluding self-references from aliases entirely. **Correct grouping presented under a wrong label still reads as a broken system.** |
| 33 | Step 9 | **THE DERIVATION CHECK PROVED ANYTHING WE ASKED IT TO.** Its first run produced 1,223 "arithmetic proofs", and they were mostly nonsense: a gap of 4 between 164 and 160 processing centres was explained by a quantity of 4 called "Stores and spares"; a gap of 15 between two percentages was explained by "Wood = 15.3". With two thousand numeric facts about one entity, *some* number sits near any gap you choose. The code comment beside the function had predicted this in advance — "an explanation that can always be found explains nothing" — and it happened anyway. Three fixes, of which only the second really matters: a tighter tolerance; **a uniqueness rule, so a gap matched by more than one fact is coincidence rather than evidence**; and a floor on gap size, since small integers are everywhere. 1,223 → 70. **A test that never fails is not a test, and a proof that always succeeds is not a proof.** |
| 34 | Step 9 | **The same check then "proved" a figure using itself.** After the uniqueness fix, survivors included `total income 38,382.91 million` against `total income 49,114.06` — the second having lost its unit in extraction, so one was normalised to 38.4 billion and the other left at 49,114. The gap was therefore the *first value itself*, which the check duly found in the corpus and called a proof. Fixed by rejecting any bridging fact whose value is essentially one of the two originals: a component that accounts for the whole is not a component. 70 → 43, and the survivors are genuine — including net IPO proceeds of ₹8,703.00m and ₹8,863.03m reconciled by ₹160.03m of un-utilised IPO expenses. |
| 35 | Step 7+9 | **A growth rate was compared against an amount and called a contradiction.** Fuzzy matching scored `"revenue from contracts with customers"` and `"revenue from contracts with customers CAGR"` as 95% alike and merged them into one family. A CAGR of 48.49% was then compared against "$1 billion in revenues" and reported as a disagreement. **A rate is not the quantity it measures; a margin is not the amount it is a margin of.** Fixed by giving each phrase a "kind of measurement" signature — the words in it that change what is being measured (cagr, growth, margin, ratio, per, total, net…) — and refusing to group two phrases whose signatures differ, *before* similarity is even considered. |
| 36 | Step 9 | **A proportion with no percent sign was compared against an amount.** The percentage check looked for `%` in the value, but `58.13` with `unit = "percent"` carries no sign at all, so a share of revenue was compared against a dollar amount. Fixed by checking the unit as well as the value. **Two fields encode one property; checking only one of them is checking nothing.** |
| 37 | Step 9 | **Facts five years apart were compared because they stated time differently.** The rule tree asked "do both have a *period*?" and separately "do both have an *as-of date*?" — so a fact with `period: Fiscal 2019` and one with `as at: March 31, 2024` fell through **both** branches and went on to have their numbers compared. An EBITDA margin from 2019 was reported as contradicting one from 2024. Fixed by reducing each fact to a single moment (`period_end` or `as_of_date`) before comparing. **When two fields express one idea, branch on the idea, not on the fields.** Contradictions fell by 131. |
| 4 | Step 1 | **Our own password redaction failed, caused by failure 3.** `remove_the_password_from()` searched for the password as one whole string. Because the driver had torn the password in half at the `@`, only a *fragment* appeared in the error text, so the search matched nothing and that fragment was printed. Fixed by also redacting each separator-delimited piece of the password of three characters or more. **Lesson: redaction that assumes secrets stay intact is not redaction.** A leaked fragment means the password must be rotated, not just the code patched. |

---

## Failures we expect to hit

Listed in advance so we recognise them quickly rather than debugging blind. These
are **predictions, not observations** — none of them is a valid Top 5 entry until
it actually happens and we can show the evidence.

| Likely failure | Why it happens |
|---|---|
| A table's units header ("in millions") sits outside the chunk | Every value in that table comes out wrong by a factor of a million |
| A number gets attached to the wrong row or column label | Plain text extraction flattens a table and loses the grid |
| Negative numbers written as `(1,240)` are read as positive | Accounting notation; a bare number parser does not know the brackets mean negative |
| A company name in a footnote refers to a subsidiary, not the parent | Entity resolution by name merges two different legal entities |
| The LLM paraphrases instead of quoting | The grounding check rejects a fact that was actually correct |
| Chart labels are matched to the wrong axis value | The numbers and the labels are separate floating text; we pair them by position, which is a guess |
| An LLM returns malformed JSON | Model output is not guaranteed to parse; one bad chunk must not kill a 600-chunk run |
| Free-tier rate limit or daily quota is hit mid-run | We are on a free API tier with hard caps |
