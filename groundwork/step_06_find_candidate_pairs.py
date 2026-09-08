"""
STEP 6 — find the small number of fact pairs actually worth comparing.

Run it with:
    .\\.venv\\Scripts\\python.exe -m groundwork.step_06_find_candidate_pairs

---------------------------------------------------------------------------
WHY THIS STEP EXISTS AT ALL
---------------------------------------------------------------------------
We have 3,033 facts. Comparing every one to every other is 4.6 million pairs.
Each would need a judgement, and almost every one would be between two facts
with nothing to do with each other.

So we BLOCK: group facts by something cheap that every genuine comparison must
share, and only compare within a group. Here that is the entity and the
attribute family — two facts can only agree or disagree if they are about the
same thing and measuring the same property.

---------------------------------------------------------------------------
THE ONE RULE THAT MATTERS MOST, AND IT IS EASY TO GET WRONG
---------------------------------------------------------------------------
NEVER BLOCK ON A FIELD THE ADJUDICATOR NEEDS TO REASON ABOUT.

It is tempting to add the date to the blocking key. Facts from different dates
are not comparable, the argument goes, so why generate the pair at all?

Because that reasoning is exactly backwards. A director listed as active in a
2022 prospectus and resigned in a 2024 annual report have DIFFERENT dates —
and that pair is the whole point. Revenue in FY23 against revenue in FY24 has
different dates, and explaining that difference is a required deliverable.

Blocking decides WHAT GETS COMPARED. The rule tree in Step 7 decides WHAT THE
COMPARISON MEANS. Putting a field in the blocking key removes it from the
adjudicator's reach, silently, and the cases you most wanted disappear without
any error being raised.

This was nearly a serious bug in the original plan. It is written up as D-08.
"""

import sys
from collections import defaultdict
from itertools import combinations

from groundwork.shared import config, database


# ===========================================================================
# Tuning numbers
# ===========================================================================

# A bucket bigger than this produces more pairs than it is worth adjudicating,
# and a bucket that large usually means an attribute family has swallowed
# something it should not have. We cap it and say so, rather than quietly
# generating a hundred thousand comparisons.
MOST_FACTS_IN_ONE_BUCKET = 120

# Reported so the numbers below can be judged against the alternative.
def pairs_from(how_many_facts: int) -> int:
    return how_many_facts * (how_many_facts - 1) // 2


# ===========================================================================
# Loading
# ===========================================================================

def load_facts_worth_comparing() -> list[dict]:
    """
    Fetch the facts that could take part in a comparison.

    Three requirements, and each excludes facts for a different reason:

      grounded          an unproved fact must never contribute to a verdict
      has an entity     without one we do not know what it is about
      has a value       a fact with neither a number nor text says nothing
                        that could agree or disagree with anything
    """
    return database.fetch_all_rows(
        """
        SELECT fact_id, doc_id, page_no, entity_id, attribute_family,
               attribute_raw, value_num, value_text, value_raw, unit, currency,
               period_start, period_end, as_of_date, scope_tags, scope_raw,
               source_kind, confidence
        FROM facts
        WHERE grounded = TRUE
          AND entity_id IS NOT NULL
          AND attribute_family IS NOT NULL
          AND (value_num IS NOT NULL OR value_text IS NOT NULL)
        ORDER BY fact_id
        """
    )


# ===========================================================================
# Blocking
# ===========================================================================

def group_facts_into_buckets(fact_rows: list[dict]) -> dict[tuple, list[dict]]:
    """
    Put facts that could possibly be compared into the same bucket.

    The key is entity plus attribute family, and nothing else. See the note at
    the top of this file about why the date is deliberately absent.
    """
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for fact_row in fact_rows:
        key = (fact_row["entity_id"], fact_row["attribute_family"])
        buckets[key].append(fact_row)
    return buckets


def two_facts_are_the_same_statement(first: dict, second: dict) -> bool:
    """
    True when a pair is one fact reported twice, not two facts to compare.

    Adjacent passages sometimes yield the same claim, and a table row read
    twice yields it twice. Comparing such a pair produces a corroboration that
    is really just double-counting — the same trap as failure 13, arriving from
    a different direction.

    Only identical claims from the same page count. The same value on two
    different pages is a genuine corroboration and must survive.
    """
    if first["page_no"] != second["page_no"] or first["doc_id"] != second["doc_id"]:
        return False

    return (
        first["value_num"] == second["value_num"]
        and first["value_text"] == second["value_text"]
        and first["period_start"] == second["period_start"]
        and first["as_of_date"] == second["as_of_date"]
        and first["scope_raw"] == second["scope_raw"]
    )


def build_candidate_pairs(buckets: dict[tuple, list[dict]]) -> tuple[list[dict], dict]:
    """Turn buckets into the pairs worth adjudicating."""
    pair_rows = []
    buckets_that_were_capped = []

    how_many_pairs_within_one_document = 0
    how_many_pairs_across_documents = 0
    how_many_duplicates_skipped = 0

    for (entity_id, attribute_family), facts_in_bucket in sorted(buckets.items()):
        if len(facts_in_bucket) < 2:
            continue

        if len(facts_in_bucket) > MOST_FACTS_IN_ONE_BUCKET:
            buckets_that_were_capped.append(
                (entity_id, attribute_family, len(facts_in_bucket))
            )
            # Keep the most trustworthy ones rather than an arbitrary slice.
            facts_in_bucket = sorted(
                facts_in_bucket,
                key=lambda row: -(row["confidence"] or 0),
            )[:MOST_FACTS_IN_ONE_BUCKET]

        for first, second in combinations(facts_in_bucket, 2):
            if two_facts_are_the_same_statement(first, second):
                how_many_duplicates_skipped += 1
                continue

            # A fixed order, so one pair can never be stored twice under two
            # arrangements of the same two facts.
            fact_a, fact_b = sorted([first["fact_id"], second["fact_id"]])

            if first["doc_id"] == second["doc_id"]:
                how_many_pairs_within_one_document += 1
            else:
                how_many_pairs_across_documents += 1

            pair_rows.append({
                "relation_id": f"rel_{fact_a}__{fact_b}"[:200],
                "fact_a": fact_a,
                "fact_b": fact_b,
                "verdict": "candidate",
                "method": "blocking",
                "explanation": None,
                "bridging_fact_id": None,
            })

    statistics = {
        "within_one_document": how_many_pairs_within_one_document,
        "across_documents": how_many_pairs_across_documents,
        "duplicates_skipped": how_many_duplicates_skipped,
        "buckets_that_were_capped": buckets_that_were_capped,
    }
    return pair_rows, statistics


# ===========================================================================
# Running it
# ===========================================================================

def print_the_biggest_buckets(buckets: dict[tuple, list[dict]], how_many: int = 12) -> None:
    """Show the buckets producing the most comparisons, so they can be sanity-checked."""
    biggest = sorted(
        ((key, facts) for key, facts in buckets.items() if len(facts) >= 2),
        key=lambda pair: -len(pair[1]),
    )[:how_many]

    entity_names = {
        row["entity_id"]: row["canonical_name"]
        for row in database.fetch_all_rows("SELECT entity_id, canonical_name FROM entities")
    }
    family_labels = {
        row["family_id"]: row["label"]
        for row in database.fetch_all_rows("SELECT family_id, label FROM attribute_families")
    }

    print()
    print("=" * 78)
    print("THE BIGGEST BUCKETS — these produce the most comparisons")
    print("=" * 78)
    print(f"  {'entity':<26}{'attribute family':<34}{'facts':>6}{'pairs':>8}")
    for (entity_id, family_id), facts in biggest:
        entity = (entity_names.get(entity_id) or entity_id)[:24]
        family = (family_labels.get(family_id) or family_id)[:32]
        print(f"  {entity:<26}{family:<34}{len(facts):>6}{pairs_from(len(facts)):>8,}")


def main() -> int:
    print("=" * 78)
    print("STEP 6 — finding the pairs worth comparing")
    print("=" * 78)

    config.stop_unless_these_settings_are_filled_in(["DATABASE_URL"])

    fact_rows = load_facts_worth_comparing()
    if len(fact_rows) < 2:
        print("Not enough comparable facts. Run step_05_normalize_facts first.")
        return 1

    print(f"\n{len(fact_rows):,} facts are grounded, identified and carry a value")
    print(f"  comparing every one to every other would be "
          f"{pairs_from(len(fact_rows)):,} pairs")

    buckets = group_facts_into_buckets(fact_rows)
    buckets_worth_anything = {k: v for k, v in buckets.items() if len(v) >= 2}

    print(f"\nBlocked into {len(buckets):,} buckets by entity + attribute family")
    print(f"  {len(buckets_worth_anything):,} of them hold more than one fact")
    print(f"  {len(buckets) - len(buckets_worth_anything):,} hold a single fact and "
          f"produce nothing")

    pair_rows, statistics = build_candidate_pairs(buckets)

    # Re-running replaces the candidates. Verdicts from a previous run were
    # reached against a different set of pairs, so keeping them beside new ones
    # would mix two different views of the corpus.
    database.execute_sql("DELETE FROM relations")
    database.insert_rows_in_batches("relations", pair_rows)

    print()
    print("=" * 78)
    print("RESULT")
    print("=" * 78)
    print(f"  candidate pairs            : {len(pair_rows):,}")
    print(f"  reduction from comparing all: "
          f"{pairs_from(len(fact_rows)) / max(1, len(pair_rows)):,.0f}x fewer")
    print()
    print(f"  within one document        : {statistics['within_one_document']:,}")
    print(f"  ACROSS DOCUMENTS           : {statistics['across_documents']:,}")
    print("      ^ these are the interesting ones. A contradiction inside one")
    print("        document is usually our own extraction error; a difference")
    print("        between two documents is a real finding.")
    print()
    print(f"  duplicates skipped         : {statistics['duplicates_skipped']:,}")
    print("      ^ the same claim reported twice on one page. Comparing those")
    print("        would report a corroboration that is only double-counting.")

    if statistics["buckets_that_were_capped"]:
        print()
        print(f"  BUCKETS CAPPED AT {MOST_FACTS_IN_ONE_BUCKET}: "
              f"{len(statistics['buckets_that_were_capped'])}")
        for entity_id, family_id, size in statistics["buckets_that_were_capped"][:5]:
            print(f"     {size:,} facts in {family_id[:50]}")
        print("      ^ a bucket this big usually means an attribute family has")
        print("        swallowed something it should not have. Worth a look.")

    print_the_biggest_buckets(buckets_worth_anything)

    print()
    if not pair_rows:
        print("NO PAIRS FOUND — nothing can be compared. Check normalisation.")
        return 1
    print("Pairs stored as 'candidate'. Next: Step 7 decides what each one means.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
