"""
STEP 7 — decide what each pair of facts actually means.

Run it with:
    .\\.venv\\Scripts\\python.exe -m groundwork.step_07_adjudicate_pairs

This is the step the whole project exists for. Everything before it was
preparation; this is where facts become knowledge.

---------------------------------------------------------------------------
THE FOUR VERDICTS
---------------------------------------------------------------------------
  corroborates   same claim, same context, values agree
  contradicts    same claim, same context, values genuinely disagree
  reconciled     values differ, but a qualifier fully explains why
  unrelated      not actually about the same thing after all

And one more that matters more than it looks:

  superseded     a state that changed. A director active in a 2022 prospectus
                 and resigned in a 2024 annual report is NOT a contradiction.
                 The documents agree; the world moved. Telling "these documents
                 disagree" apart from "this changed" is the difference between
                 a useful tool and an alarm that cries wolf.

---------------------------------------------------------------------------
RULES FIRST, MODEL LAST
---------------------------------------------------------------------------
The rule tree runs on every pair. The model is asked only about pairs the
rules cannot settle, and it is asked at most a handful of times.

Three reasons, and the third is the one you will be asked about:

  it is free            we are on a daily allowance
  it is repeatable      the same pair always gets the same verdict
  IT IS ANSWERABLE      every relation records whether a rule or a model
                        decided it. When someone asks "how much of this is
                        just an LLM making things up?", the answer is a
                        number, not a shrug.

---------------------------------------------------------------------------
THE DERIVATION CHECK
---------------------------------------------------------------------------
A label is not proof. Saying "these differ because the scope differs" is a
claim we should have to support.

So when two numbers disagree, we go looking for a third fact worth exactly the
gap between them. If 98,135 and 63,713 disagree by 34,422, and the corpus
elsewhere records a quantity of 34,422 about the same entity, then the gap is
a documented thing and not a discrepancy.

That turns a label into an equation: 63,713 + 34,422 = 98,135. Most systems
stop at "contradiction: 63,713 vs 98,135". This one shows its working.
"""

import sys
from collections import defaultdict
from datetime import date

from pydantic import BaseModel

from groundwork.shared import config, database, llm_client


# ===========================================================================
# Tuning numbers
# ===========================================================================

# Two numbers this close are the same number written twice. Financial documents
# round differently between a headline and a statement, so exact equality is
# too strict to be useful.
HOW_CLOSE_COUNTS_AS_AGREEMENT = 0.005          # half of one percent

# How close a third fact must be to the gap for the derivation check to accept
# it as an explanation.
#
# This began at one percent and produced 1,223 "proofs", most of them nonsense:
# a gap of 4 between 164 and 160 processing centres was solemnly explained by a
# quantity of 4 called "Stores and spares". With two thousand numeric facts
# about one entity, SOME number sits near any gap you like. Tightened, and
# paired with the uniqueness rule below, which matters more.
HOW_CLOSE_A_BRIDGING_FACT_MUST_BE = 0.002      # two tenths of one percent

# If more than this many facts match the gap, the match is a coincidence rather
# than an explanation.
#
# THIS IS THE RULE THAT MAKES THE CHECK MEAN ANYTHING. A gap explained by one
# specific documented quantity is evidence. The same gap "explained" by nine
# different unrelated quantities is arithmetic noise, and the fact that we
# could pick the closest one does not make it a finding.
MOST_FACTS_THAT_MAY_MATCH_A_GAP = 1

# A gap smaller than this, in absolute terms, is too easy to match by accident.
# Small integers are everywhere in a financial document.
SMALLEST_GAP_WORTH_EXPLAINING = 50

# Two scopes sharing less than this proportion of their meaningful words are
# describing different things.
HOW_MUCH_SCOPE_OVERLAP_COUNTS_AS_THE_SAME = 0.5

# A ratio this close to a power of ten is a units problem, not a disagreement.
# Nothing in the real world differs from itself by exactly a thousand times.
HOW_CLOSE_A_RATIO_MUST_BE_TO_A_POWER_OF_TEN = 0.01

# Signs a value was written as a bound rather than a measurement. ">2.8Bn" is
# a floor; comparing it to an exact figure as though both were precise would
# manufacture a disagreement that nobody stated.
SIGNS_A_VALUE_IS_A_BOUND = (">", "<", "~", "≈", "+", "at least", "more than", "over")

ADJUDICATION_PROMPT_VERSION = "adjudicate-v1"


# ===========================================================================
# Small comparisons
# ===========================================================================

def relative_difference_between(first: float, second: float) -> float:
    """
    How far apart two numbers are, as a proportion of the larger one.

    Relative rather than absolute, because a difference of 100 is nothing
    between two billions and everything between two hundreds.
    """
    largest = max(abs(first), abs(second))
    if largest == 0:
        return 0.0 if first == second else 1.0
    return abs(first - second) / largest


WAYS_OF_WRITING_A_PROPORTION = {"%", "percent", "percentage", "pct", "bps", "basis points"}


def value_is_a_proportion(fact: dict) -> bool:
    """
    True when a fact states a share of something rather than an amount.

    Checks the unit as well as the value. "58.13" with unit "percent" has no
    percent sign anywhere in the value, and looking only at the value let a
    proportion be compared against an amount and reported as a contradiction.
    """
    if "%" in (fact.get("value_raw") or ""):
        return True
    unit = (fact.get("unit") or "").strip().lower()
    return unit in WAYS_OF_WRITING_A_PROPORTION


def a_value_was_written_as_a_bound(value_raw: str | None) -> bool:
    """True for figures written as 'more than X' rather than as a measurement."""
    if not value_raw:
        return False
    lowered = value_raw.lower()
    return any(sign in lowered for sign in SIGNS_A_VALUE_IS_A_BOUND)


def the_ratio_is_a_power_of_ten(first: float, second: float) -> int | None:
    """
    Detect two numbers that differ by a factor of ten, a thousand, a million.

    Nothing genuinely differs from itself by exactly 1,000 times. When it
    appears, one of the two figures was stated in different units and we failed
    to normalise it — which is our bug, not a disagreement between documents,
    and must not be reported as one.

    Returns the power of ten, or None.
    """
    if first == 0 or second == 0:
        return None
    ratio = max(abs(first), abs(second)) / min(abs(first), abs(second))
    if ratio < 9.9:
        return None

    power = 0
    while ratio >= 9.9:
        ratio /= 10
        power += 1
    if abs(ratio - 1.0) <= HOW_CLOSE_A_RATIO_MUST_BE_TO_A_POWER_OF_TEN:
        return power
    return None


def how_much_two_scopes_overlap(first_tags, second_tags) -> float:
    """Proportion of meaningful words two scope notes share."""
    first_set, second_set = set(first_tags or []), set(second_tags or [])
    if not first_set and not second_set:
        return 1.0
    if not first_set or not second_set:
        return 0.0
    return len(first_set & second_set) / len(first_set | second_set)


def describe_a_period(start: date | None, end: date | None, raw: str | None) -> str:
    """Say what period a fact covers, in the way a person would."""
    if raw:
        return raw
    if start and end:
        return f"{start} to {end}"
    if end:
        return str(end)
    return "no period stated"


def tidy_number(value: float) -> str:
    """
    Format a number for a human, keeping decimals only where they matter.

    Rounding everything to whole numbers turned "10.82 + 7,817,470,000" into
    "11 + 7,817,470,000", which reads like a typo in our own arithmetic and
    undermines the very explanation it appears in.
    """
    if value == int(value):
        return f"{int(value):,}"
    if abs(value) < 1000:
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def shorten(text: str | None, limit: int = 90) -> str:
    if not text:
        return ""
    tidied = " ".join(str(text).split())
    return tidied if len(tidied) <= limit else tidied[: limit - 3] + "..."


# ===========================================================================
# The derivation check
# ===========================================================================

def build_an_index_of_values_by_entity(fact_rows: list[dict]) -> dict[str, list[dict]]:
    """Group every numeric fact by entity, so the gap search is a lookup."""
    index = defaultdict(list)
    for fact_row in fact_rows:
        if fact_row["value_num"] is not None:
            index[fact_row["entity_id"]].append(fact_row)
    return index


def look_for_a_fact_that_explains_the_gap(
    first: dict, second: dict, values_by_entity: dict[str, list[dict]]
) -> dict | None:
    """
    Find a third fact worth exactly the difference between two others.

    This is what turns "they differ because of scope" from a label into an
    equation. If 63,713 and 98,135 disagree by 34,422, and the corpus records a
    quantity of 34,422 about the same entity, then the gap is a documented
    thing rather than a discrepancy.

    Deliberately restricted to the same entity, and to a bridging fact that is
    not one of the two being compared. Searching more widely would eventually
    find SOME number close to any gap, and an explanation that can always be
    found explains nothing.
    """
    gap = abs(first["value_num"] - second["value_num"])

    # A small gap is too easy to match by accident. Financial documents are
    # full of small integers, and 4 will always be findable somewhere.
    if gap < SMALLEST_GAP_WORTH_EXPLAINING:
        return None

    # If the gap is essentially one of the two values, then the other is
    # essentially zero and there is nothing here to explain.
    #
    # This happened whenever a unit went missing in extraction: 38,382.91
    # million became 38.4 billion while the same figure elsewhere, with no unit
    # captured, stayed at 49,114. The "gap" was then the first value itself,
    # and the check obligingly found the first value in the corpus and called
    # it a proof. A component that accounts for the whole is not a component.
    for one_side in (first, second):
        if relative_difference_between(gap, abs(one_side["value_num"])) <= 0.05:
            return None

    # A percentage cannot explain the gap between two absolute quantities, and
    # a quantity cannot explain the gap between two percentages.
    the_pair_are_percentages = value_is_a_proportion(first)

    facts_that_match_the_gap = []

    for candidate in values_by_entity.get(first["entity_id"], []):
        if candidate["fact_id"] in (first["fact_id"], second["fact_id"]):
            continue
        if candidate["value_num"] is None or abs(candidate["value_num"]) < 1:
            continue
        if value_is_a_proportion(candidate) != the_pair_are_percentages:
            continue

        # A component must belong to the same moment as the things it is
        # bridging. A quantity from a different year cannot explain why two
        # figures from this year differ.
        candidate_shares_a_date = (
            candidate["as_of_date"] is not None
            and candidate["as_of_date"] in (first["as_of_date"], second["as_of_date"])
        ) or (
            candidate["period_end"] is not None
            and candidate["period_end"] in (first["period_end"], second["period_end"])
        )
        neither_side_states_a_date = (
            first["as_of_date"] is None and second["as_of_date"] is None
            and first["period_end"] is None and second["period_end"] is None
        )
        if not candidate_shares_a_date and not neither_side_states_a_date:
            continue

        if relative_difference_between(candidate["value_num"], gap) <= HOW_CLOSE_A_BRIDGING_FACT_MUST_BE:
            facts_that_match_the_gap.append(candidate)
            # More matches than allowed means coincidence; stop early.
            if len(facts_that_match_the_gap) > MOST_FACTS_THAT_MAY_MATCH_A_GAP:
                return None

    if len(facts_that_match_the_gap) != 1:
        return None
    return facts_that_match_the_gap[0]


# ===========================================================================
# The rule tree
# ===========================================================================

class Verdict:
    """One decision about one pair."""

    def __init__(self, verdict: str, method: str, explanation: str,
                 bridging_fact_id: str | None = None):
        self.verdict = verdict
        self.method = method
        self.explanation = explanation
        self.bridging_fact_id = bridging_fact_id


def adjudicate_one_pair(
    first: dict, second: dict, values_by_entity: dict[str, list[dict]]
) -> Verdict:
    """
    Walk the rule tree in order and decide what this pair means.

    THE ORDER IS THE ARGUMENT. Each branch asks "is there a stated reason these
    two could differ without either being wrong?" and only when every such
    reason is exhausted do we allow the word "contradicts". Checking values
    first and context afterwards would report a contradiction for every pair of
    figures covering different years.
    """
    first_is_a_number = first["value_num"] is not None
    second_is_a_number = second["value_num"] is not None

    # --- can these be compared at all? ------------------------------------

    if first["currency"] and second["currency"] and first["currency"] != second["currency"]:
        return Verdict(
            "unrelated", "rule",
            f"Stated in different currencies ({first['currency']} and "
            f"{second['currency']}), so the figures are not comparable without "
            "an exchange rate the documents do not give.",
        )

    if first_is_a_number != second_is_a_number:
        return Verdict(
            "unrelated", "rule",
            "One states a number and the other states text, so they are not "
            "measuring the same thing in a comparable way.",
        )

    # A proportion and an amount are different kinds of thing, however similar
    # their labels. "Finance costs 32.14" against "Finance costs 43.4%" was
    # being reported as a contradiction; one is money and the other is a share
    # of something, and they cannot disagree because they never agreed on what
    # was being measured.
    # The unit matters as much as the value. A figure of 58.13 with unit
    # "percent" carries no % sign at all, and checking only the value let a
    # proportion be compared against an amount in dollars.
    first_is_a_percentage = value_is_a_proportion(first)
    second_is_a_percentage = value_is_a_proportion(second)
    if first_is_a_percentage != second_is_a_percentage:
        return Verdict(
            "unrelated", "rule",
            f"One is a proportion ({first['value_raw'] if first_is_a_percentage else second['value_raw']}) "
            "and the other is an absolute amount. They measure different kinds "
            "of thing and cannot agree or disagree with one another.",
        )

    # --- periods ----------------------------------------------------------

    both_have_a_period = first["period_start"] and second["period_start"]
    if both_have_a_period and (
        first["period_start"] != second["period_start"]
        or first["period_end"] != second["period_end"]
    ):
        return Verdict(
            "reconciled", "rule",
            "Not a disagreement: these cover different periods. One is "
            f"{describe_a_period(first['period_start'], first['period_end'], first['period_raw'])}"
            f" and the other is "
            f"{describe_a_period(second['period_start'], second['period_end'], second['period_raw'])}"
            ". Two figures covering different spans of time can both be correct.",
        )

    # --- a state that changed ---------------------------------------------
    #
    # Both sides are reduced to a single MOMENT before comparing, and that
    # detail matters more than it looks. Documents state time in whichever way
    # suits them: one says "Fiscal 2019", another says "as at March 31, 2024".
    #
    # Checking "do both have a period?" and separately "do both have an as-of
    # date?" leaves the mixed case falling through both branches — and an
    # EBITDA margin from 2019 was duly reported as contradicting one from 2024,
    # five years apart, because neither test applied to the pair.

    first_moment = first["period_end"] or first["as_of_date"]
    second_moment = second["period_end"] or second["as_of_date"]

    both_have_an_as_of_date = first_moment and second_moment
    dates_differ = both_have_an_as_of_date and first_moment != second_moment

    if dates_differ and not first_is_a_number:
        earlier, later = sorted(
            [first, second], key=lambda f: f["period_end"] or f["as_of_date"]
        )
        if (earlier["value_text"] or "").strip().lower() != (later["value_text"] or "").strip().lower():
            return Verdict(
                "superseded", "rule",
                "Not a contradiction — a state that changed. As at "
                f"{earlier['period_end'] or earlier['as_of_date']} the position was "
                f"\"{shorten(earlier['value_text'], 60)}\"; as at "
                f"{later['period_end'] or later['as_of_date']} it was "
                f"\"{shorten(later['value_text'], 60)}\". The later document "
                "updates the earlier one rather than disagreeing with it.",
            )

    if dates_differ and first_is_a_number:
        return Verdict(
            "reconciled", "rule",
            "Not a disagreement: these describe different moments in time. One "
            f"applies to {describe_a_period(None, first_moment, first['period_raw'] or first['as_of_raw'])}"
            f" and the other to "
            f"{describe_a_period(None, second_moment, second['period_raw'] or second['as_of_raw'])}"
            ". A quantity measured at two different times may legitimately "
            "differ at each.",
        )

    # --- scope ------------------------------------------------------------

    first_scope = first["scope_tags"] or []
    second_scope = second["scope_tags"] or []
    both_state_a_scope = bool(first_scope) and bool(second_scope)

    if both_state_a_scope:
        overlap = how_much_two_scopes_overlap(first_scope, second_scope)
        if overlap < HOW_MUCH_SCOPE_OVERLAP_COUNTS_AS_THE_SAME:
            return Verdict(
                "reconciled", "rule",
                "Not a disagreement: these are measured at different scopes. "
                f"One is qualified as \"{shorten(first['scope_raw'], 70)}\" and the "
                f"other as \"{shorten(second['scope_raw'], 70)}\". They are "
                "counting different things.",
            )

    # --- text values ------------------------------------------------------

    if not first_is_a_number:
        first_text = (first["value_text"] or "").strip().lower()
        second_text = (second["value_text"] or "").strip().lower()
        if first_text == second_text:
            return Verdict(
                "corroborates", "rule",
                f"Both state the same thing: \"{shorten(first['value_text'], 70)}\".",
            )
        # Genuinely ambiguous — two different statements with no date to order
        # them. This is what the model is for.
        return Verdict(
            "needs_a_judgement", "rule",
            f"Two different statements with nothing to order them: "
            f"\"{shorten(first['value_text'], 50)}\" and \"{shorten(second['value_text'], 50)}\".",
        )

    # --- numbers ----------------------------------------------------------

    difference = relative_difference_between(first["value_num"], second["value_num"])

    if difference <= HOW_CLOSE_COUNTS_AS_AGREEMENT:
        where_from = (
            "in the same document"
            if first["doc_id"] == second["doc_id"]
            else "in two different documents"
        )
        return Verdict(
            "corroborates", "rule",
            f"Both give {first['value_raw']} for the same measure at the same "
            f"scope and period, stated {where_from}. Agreement to within "
            f"{difference:.2%}.",
        )

    # A factor of exactly ten, a thousand, a million is our own units bug, and
    # calling it a contradiction would blame the documents for our mistake.
    power_of_ten = the_ratio_is_a_power_of_ten(first["value_num"], second["value_num"])
    if power_of_ten is not None:
        return Verdict(
            "reconciled", "rule",
            f"These differ by almost exactly a factor of 10^{power_of_ten}, which "
            "means one is stated in different units and our normalisation missed "
            "it. Recorded as a scale difference rather than a disagreement — "
            "nothing in the world differs from itself by exactly a thousand times.",
        )

    if a_value_was_written_as_a_bound(first["value_raw"]) or a_value_was_written_as_a_bound(
        second["value_raw"]
    ):
        return Verdict(
            "reconciled", "rule",
            f"One figure is written as a bound rather than a measurement "
            f"({first['value_raw']} against {second['value_raw']}), so a "
            "difference between them is not a disagreement.",
        )

    # Everything that could explain a difference has been ruled out. Before
    # calling it a contradiction, look for a third fact that closes the gap.
    bridging_fact = look_for_a_fact_that_explains_the_gap(first, second, values_by_entity)
    if bridging_fact is not None:
        smaller, larger = sorted([first, second], key=lambda f: f["value_num"])
        return Verdict(
            "reconciled", "derivation",
            "Not a disagreement, and here is the arithmetic. The two figures "
            f"differ by {tidy_number(abs(first['value_num'] - second['value_num']))}, "
            "and the documents separately record that quantity as "
            f"\"{shorten(bridging_fact['attribute_raw'], 60)}\" "
            f"({bridging_fact['value_raw']}, page {bridging_fact['page_no']}). "
            f"So {tidy_number(smaller['value_num'])} + "
            f"{tidy_number(bridging_fact['value_num'])} = "
            f"{tidy_number(larger['value_num'])}. Nothing else in the corpus "
            "matches that gap, so it is a documented quantity rather than a "
            "coincidence.",
            bridging_fact_id=bridging_fact["fact_id"],
        )

    # --- a real disagreement ----------------------------------------------

    only_one_states_a_scope = bool(first_scope) != bool(second_scope)
    caveat = ""
    if only_one_states_a_scope:
        qualified = first if first_scope else second
        caveat = (
            " Note that only one of the two carries a stated qualification "
            f"(\"{shorten(qualified['scope_raw'], 60)}\"), so this may be a "
            "difference of definition rather than of fact."
        )

    both_are_low_confidence = (first["confidence"] or 1) < 0.6 and (second["confidence"] or 1) < 0.6
    if both_are_low_confidence:
        return Verdict(
            "contradicts", "rule",
            f"These disagree: {first['value_raw']} against {second['value_raw']}, a "
            f"difference of {difference:.1%}. BUT both values come from a source we "
            "do not trust much (a chart label matched to its axis by position), so "
            "this may be our own extraction error rather than a real disagreement "
            "between the documents." + caveat,
        )

    where_from = (
        "within the same document"
        if first["doc_id"] == second["doc_id"]
        else "between two documents"
    )
    return Verdict(
        "contradicts", "rule",
        f"These disagree {where_from}: {first['value_raw']} "
        f"(page {first['page_no']}) against {second['value_raw']} "
        f"(page {second['page_no']}), a difference of {difference:.1%}. They "
        "cover the same period, the same scope and the same measure, so no "
        "stated context explains the gap." + caveat,
    )


# ===========================================================================
# The model, for the few pairs rules cannot settle
# ===========================================================================

class ModelVerdict(BaseModel):
    verdict: str
    explanation: str


ADJUDICATION_INSTRUCTIONS = """\
You are deciding what a pair of extracted claims means. Both were taken from
real documents and both have been verified against their source text.

Choose exactly one verdict:

  corroborates  they say the same thing
  contradicts   they genuinely disagree, and no stated context explains it
  reconciled    they differ, but something stated explains why
  superseded    a state that changed over time, so the later replaces the
                earlier rather than disagreeing with it
  unrelated     they are not really about the same thing

RULES

1. Use only what is written below. Do not use anything you know about these
   organisations from elsewhere.
2. "contradicts" is the strongest claim available and needs the most evidence.
   If any stated difference in period, scope, definition or date could explain
   the gap, the verdict is "reconciled", not "contradicts".
3. A status changing over time is "superseded", never "contradicts". Documents
   describing different moments are not in disagreement.
4. Write the explanation for a person who has not seen the documents. Say what
   each claim is, and why you reached your verdict. Two or three sentences.
"""


def ask_the_model_about_one_pair(first: dict, second: dict) -> Verdict | None:
    """Ask the model about a pair the rules could not settle."""
    prompt = f"""{ADJUDICATION_INSTRUCTIONS}

CLAIM A
  subject   : {first['subject_raw'] or "the document's own subject"}
  attribute : {first['attribute_raw']}
  value     : {first['value_raw']} {first['unit'] or ''} {first['currency'] or ''}
  period    : {first['period_raw'] or 'not stated'}
  as at     : {first['as_of_raw'] or 'not stated'}
  scope     : {first['scope_raw'] or 'not stated'}
  source    : page {first['page_no']}
  evidence  : "{shorten(first['evidence_text'], 300)}"

CLAIM B
  subject   : {second['subject_raw'] or "the document's own subject"}
  attribute : {second['attribute_raw']}
  value     : {second['value_raw']} {second['unit'] or ''} {second['currency'] or ''}
  period    : {second['period_raw'] or 'not stated'}
  as at     : {second['as_of_raw'] or 'not stated'}
  scope     : {second['scope_raw'] or 'not stated'}
  source    : page {second['page_no']}
  evidence  : "{shorten(second['evidence_text'], 300)}"
"""

    allowed = {"corroborates", "contradicts", "reconciled", "superseded", "unrelated"}
    try:
        answer = llm_client.ask_the_model(
            prompt,
            prompt_version=ADJUDICATION_PROMPT_VERSION,
            expect_json=True,
            response_schema=ModelVerdict,
            model_name=config.ADJUDICATION_MODEL,
        )
        decision = ModelVerdict.model_validate_json(answer)
    except Exception as problem:
        database.record_failure(
            stage="adjudicate", kind="model_call_failed",
            detail=f"{type(problem).__name__}: {problem}"[:1500],
        )
        return None

    verdict = decision.verdict.strip().lower()
    if verdict not in allowed:
        return None
    return Verdict(verdict, "llm", decision.explanation.strip())


# ===========================================================================
# Running it
# ===========================================================================

def main() -> int:
    print("=" * 78)
    print("STEP 7 — deciding what each pair means")
    print("=" * 78)

    config.stop_unless_these_settings_are_filled_in(["DATABASE_URL"])
    llm_client.reset_the_counters()

    pair_rows = database.fetch_all_rows(
        "SELECT relation_id, fact_a, fact_b FROM relations ORDER BY relation_id"
    )
    if not pair_rows:
        print("No candidate pairs. Run step_06_find_candidate_pairs first.")
        return 1

    fact_rows = database.fetch_all_rows(
        """
        SELECT fact_id, doc_id, page_no, entity_id, attribute_family, attribute_raw,
               subject_raw, value_num, value_text, value_raw, value_kind, unit,
               currency, period_start, period_end, period_raw, as_of_date, as_of_raw,
               scope_tags, scope_raw, source_kind, confidence, evidence_text
        FROM facts WHERE grounded = TRUE
        """
    )
    facts_by_id = {row["fact_id"]: row for row in fact_rows}
    values_by_entity = build_an_index_of_values_by_entity(fact_rows)

    print(f"\n{len(pair_rows):,} pairs to judge")
    print(f"the rule tree runs on all of them; the model sees at most "
          f"{config.MOST_PAIRS_TO_ASK_THE_MODEL_ABOUT}")

    verdicts_by_relation: dict[str, Verdict] = {}
    pairs_the_rules_could_not_settle = []

    for pair in pair_rows:
        first = facts_by_id.get(pair["fact_a"])
        second = facts_by_id.get(pair["fact_b"])
        if first is None or second is None:
            continue

        verdict = adjudicate_one_pair(first, second, values_by_entity)
        if verdict.verdict == "needs_a_judgement":
            pairs_the_rules_could_not_settle.append((pair, first, second, verdict))
            continue
        verdicts_by_relation[pair["relation_id"]] = verdict

    print(f"\nrules settled {len(verdicts_by_relation):,} pairs")
    print(f"{len(pairs_the_rules_could_not_settle):,} need a judgement")

    # Ask about the most interesting unsettled pairs: those spanning two
    # documents, since a disagreement inside one document is more often our own
    # extraction error than a real finding.
    pairs_the_rules_could_not_settle.sort(
        key=lambda item: (item[1]["doc_id"] == item[2]["doc_id"], item[0]["relation_id"])
    )
    how_many_to_ask = min(
        len(pairs_the_rules_could_not_settle), config.MOST_PAIRS_TO_ASK_THE_MODEL_ABOUT
    )

    if how_many_to_ask:
        print(f"\nasking {config.ADJUDICATION_MODEL} about {how_many_to_ask} of them...")

    for pair, first, second, fallback in pairs_the_rules_could_not_settle[:how_many_to_ask]:
        decision = ask_the_model_about_one_pair(first, second)
        verdicts_by_relation[pair["relation_id"]] = decision or Verdict(
            "unrelated", "rule",
            "The rules could not settle this and the model could not be reached. "
            + fallback.explanation,
        )

    # Anything left unasked is recorded honestly as undecided rather than
    # quietly given a verdict nobody reached.
    for pair, first, second, fallback in pairs_the_rules_could_not_settle[how_many_to_ask:]:
        verdicts_by_relation[pair["relation_id"]] = Verdict(
            "undecided", "rule",
            "Neither rule nor model settled this one; it was beyond the daily "
            "allowance for model judgements. " + fallback.explanation,
        )

    database.update_rows_in_batches(
        """
        UPDATE relations
        SET verdict = %s, method = %s, explanation = %s, bridging_fact_id = %s
        WHERE relation_id = %s
        """,
        [
            (v.verdict, v.method, v.explanation, v.bridging_fact_id, relation_id)
            for relation_id, v in verdicts_by_relation.items()
        ],
    )

    # --- what we found -----------------------------------------------------
    counts = database.fetch_all_rows(
        """
        SELECT verdict, method, count(*) AS how_many
        FROM relations GROUP BY verdict, method ORDER BY how_many DESC
        """
    )
    print()
    print("=" * 78)
    print("VERDICTS")
    print("=" * 78)
    total = sum(row["how_many"] for row in counts)
    for row in counts:
        print(f"  {row['verdict']:<16}{row['method']:<12}{row['how_many']:>7,}  "
              f"({row['how_many'] / total:.0%})")

    by_method = database.fetch_one_row(
        """
        SELECT count(*) FILTER (WHERE method IN ('rule','derivation')) AS by_rule,
               count(*) FILTER (WHERE method = 'llm') AS by_model
        FROM relations
        """
    )
    print()
    print(f"  decided by rules      : {by_method['by_rule']:,} "
          f"({by_method['by_rule'] / total:.1%})")
    print(f"  decided by the model  : {by_method['by_model']:,} "
          f"({by_method['by_model'] / total:.1%})")
    print("      ^ this is the answer to 'how much of this is a model's opinion?'")

    derivations = database.count_rows_in_table("relations")
    with_proof = database.fetch_one_row(
        "SELECT count(*) AS n FROM relations WHERE bridging_fact_id IS NOT NULL"
    )["n"]
    print(f"  reconciled WITH ARITHMETIC PROOF : {with_proof:,}")

    print()
    print(f"  {llm_client.describe_what_this_run_cost()}")
    print()
    print("Next: Step 8 hunts the four required cases in these verdicts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
