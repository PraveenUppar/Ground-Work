"""
STEP 5 — make facts comparable to one another.

Run it with:
    .\\.venv\\Scripts\\python.exe -m groundwork.step_05_normalize_facts

Extraction gave us what each document SAID. This step turns that into
something that can be lined up against something else:

    "1,240" with unit "crore"        ->  12,400,000,000
    "year ended 31 March 2024"       ->  2023-04-01 .. 2024-03-31
    "Delhivery Ltd" / "Delhivery Limited" -> one entity
    "revenue" / "Revenue from operations" -> one attribute family

Without this, two facts about the same thing look like two unrelated strings
and nothing can ever be compared.

---------------------------------------------------------------------------
THE FOUR JOBS, AND WHY THEY NEED THE WHOLE CORPUS AT ONCE
---------------------------------------------------------------------------
Parsing a number needs only that number, and lives in shared/normalizers.py.
The four jobs here are different: each one can only be done by looking at every
fact together.

  ENTITIES        You cannot tell whether "Delhivery Ltd" and "Delhivery
                  Limited" are the same company by looking at either alone.

  ATTRIBUTE       "Revenue from operations" and "Revenue from contract with
  FAMILIES        customers" only reveal themselves as the same kind of thing
                  when you see the whole vocabulary of the corpus.

  THE FINANCIAL   "FY24" means April-to-March in one country and
  YEAR END        January-to-December in another. We read every explicit
                  "year ended <date>" in the documents and take the most
                  common. The convention is INPUT, never code.

  SCOPE TAGS      Turning a sentence of qualification into comparable tokens.

---------------------------------------------------------------------------
WHAT WE DELIBERATELY DO NOT DO
---------------------------------------------------------------------------
We never merge two entities, or two attributes, because it would be convenient.
A wrong merge is worse than no merge: it makes two unrelated numbers look like
a contradiction, and that contradiction will be confidently reported with real
evidence attached. When in doubt, leave them apart.
"""

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict

from pydantic import BaseModel
from rapidfuzz import fuzz

from groundwork.shared import config, database, llm_client
from groundwork.shared.normalizers import (
    parse_date,
    parse_number,
    parse_period,
    work_out_the_financial_year_end,
)


# ===========================================================================
# Tuning numbers
# ===========================================================================

# How alike two names must be before we call them the same entity. Deliberately
# high. "Delhivery Limited" and "Delhivery Private Limited" score in the low
# nineties and are probably the same company; "Spoton Logistics" and "Spoton
# Supply Chain" also score high and may well not be. When the cost of a wrong
# merge is an invented contradiction, err towards leaving things apart.
HOW_ALIKE_TWO_NAMES_MUST_BE = 93

# The same idea for attribute phrases, which vary more in wording, so a little
# more forgiving.
HOW_ALIKE_TWO_ATTRIBUTES_MUST_BE = 88

# A phrase seen only once is usually a one-off wording rather than a real
# category. We still give it a family, but we do not let it name one.
HOW_OFTEN_A_PHRASE_MUST_APPEAR_TO_NAME_ITS_FAMILY = 2

# Words carrying no distinguishing meaning in a scope note. Ordinary English
# stopwords plus a few that are near-universal in documents of any kind —
# nothing here names a company, an industry, or a subject.
WORDS_THAT_CARRY_NO_MEANING = {
    "a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "at", "by",
    "with", "from", "as", "is", "are", "was", "were", "be", "been", "this",
    "that", "these", "those", "it", "its", "our", "we", "us", "their", "which",
    "includes", "including", "included", "excludes", "excluding", "excluded",
    "period", "periods", "such", "any", "all", "other", "others", "per",
}

# Legal-form words stripped when comparing company names. These are worldwide
# corporate suffixes, not names of anything in our documents.
LEGAL_FORM_WORDS = {
    "limited", "ltd", "private", "pvt", "public", "plc", "incorporated", "inc",
    "corporation", "corp", "company", "co", "llp", "llc", "gmbh", "sa", "nv",
    "holdings", "holding", "group",
}

# Ways a document refers to ITSELF rather than naming anything.
#
# A prospectus says "Our Company holds 2,209,446 shares"; an annual report says
# "the Group employs". These are not entity names — they are pronouns for
# whoever the document is about, and treating them as names produces phantom
# entities called "Company" and "Our Company" sitting beside the real one, each
# holding a slice of the same facts.
#
# This is generic English self-reference, in the same category as stopwords. It
# names no company, industry or document, so it stays true of an unseen PDF.
WAYS_A_DOCUMENT_REFERS_TO_ITSELF = {
    "company", "our company", "the company", "the companies",
    "issuer", "the issuer", "registrant", "the registrant",
    "group", "the group", "our group", "the corporation", "corporation",
    "we", "us", "our", "the firm", "firm", "the business", "business",
    "the organisation", "the organization",
}


def a_name_is_the_document_referring_to_itself(name: str) -> bool:
    """True when a 'subject' is really a pronoun for whoever the document is about."""
    tidied = re.sub(r"[^\w\s]", " ", (name or "").lower())
    tidied = " ".join(tidied.split())
    return tidied in WAYS_A_DOCUMENT_REFERS_TO_ITSELF


# An identifier written as a label followed by a code: "DIN: 01173669",
# "CIN - L63090DL2011PLC221234", "ISIN INE148O01028".
#
# Deliberately generic. It does not know what a DIN is; it recognises the SHAPE
# of "short uppercase label, then a long alphanumeric code", which is how
# registries write identifiers anywhere in the world. Matching on an identifier
# is certain in a way that matching on a name never is.
IDENTIFIER_PATTERN = re.compile(r"\b([A-Z]{2,6})\s*[:\-]?\s*([A-Z0-9]{6,25})\b")


# ===========================================================================
# Building ids
# ===========================================================================

# How much of the original text to keep in an id before the fingerprint.
HOW_MUCH_OF_A_NAME_TO_KEEP_IN_AN_ID = 44


def make_a_readable_id(prefix: str, text: str) -> str:
    """
    Build an id that is readable AND guaranteed unique.

    A readable id is worth having: you will be reading these in query results
    and error messages for the rest of the project, and `fam_revenue_a1b2c3d4`
    tells you something a bare hash does not.

    But readability alone is not enough, and this failed the first time it ran.
    Truncating a long attribute name to a fixed length made two genuinely
    different families — both beginning "fair value loss on financial
    liabilities at fair value throu..." — collapse into the same id, and the
    database rejected the duplicate.

    A short fingerprint of the FULL text fixes it: the prefix stays readable,
    and two different names can never collide however similar their openings.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower())
    slug = slug[:HOW_MUCH_OF_A_NAME_TO_KEEP_IN_AN_ID].strip("_")
    fingerprint = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{slug}_{fingerprint}"


# ===========================================================================
# 1. Applying the parsers
# ===========================================================================

def normalise_one_fact_value(fact_row: dict, financial_year_ends_on: tuple[int, int]) -> dict:
    """Parse one fact's number, period and as-of date."""
    result = {
        "fact_id": fact_row["fact_id"],
        "value_num": None,
        "multiplier": None,
        "currency": fact_row["currency"],
        "period_start": None,
        "period_end": None,
        "as_of_date": None,
    }

    if fact_row["value_kind"] == "number":
        parsed = parse_number(
            fact_row["value_raw"], fact_row["unit"], fact_row["currency"]
        )
        result["value_num"] = parsed.value
        result["multiplier"] = parsed.multiplier
        if parsed.currency:
            result["currency"] = parsed.currency

    if fact_row["period_raw"]:
        parsed_period = parse_period(fact_row["period_raw"], financial_year_ends_on)
        result["period_start"] = parsed_period.start
        result["period_end"] = parsed_period.end

    if fact_row["as_of_raw"]:
        result["as_of_date"] = parse_date(fact_row["as_of_raw"])

    # A date-valued fact carries its date in the value, not in a qualifier.
    if fact_row["value_kind"] == "date" and result["as_of_date"] is None:
        result["as_of_date"] = parse_date(fact_row["value_raw"] or "")

    return result


# ===========================================================================
# 2. Entities
# ===========================================================================

def simplify_a_name(name: str) -> str:
    """
    Reduce a name to its distinguishing part.

    Lowercases, drops punctuation, and removes worldwide legal-form words so
    "Delhivery Limited" and "Delhivery Ltd." both become "delhivery".

    Note what this cannot do: "Delhivery Limited" and "Delhivery Private
    Limited" also both become "delhivery", and those are, in Indian company law,
    potentially different registered entities. We accept that risk because the
    alternative — treating every legal-form variant as a separate company —
    would split almost every entity in the corpus. It is recorded here as a
    known limitation rather than hidden.
    """
    without_punctuation = re.sub(r"[^\w\s]", " ", name.lower())
    meaningful_words = [
        word for word in without_punctuation.split()
        if word not in LEGAL_FORM_WORDS
    ]
    if not meaningful_words:
        # A name made only of legal-form words is all we have; keep it.
        return " ".join(without_punctuation.split())
    return " ".join(meaningful_words)


def find_identifiers_in(some_text: str) -> dict[str, str]:
    """
    Pull registry identifiers out of a piece of text.

    Matching two entities on an identifier is certain. Matching on a name is
    always a guess, however good the guess is.
    """
    if not some_text:
        return {}
    identifiers = {}
    for label, code in IDENTIFIER_PATTERN.findall(some_text):
        # A code of only letters is a word, not an identifier.
        if not any(character.isdigit() for character in code):
            continue
        identifiers[label] = code
    return identifiers


def work_out_each_documents_main_subject(fact_rows: list[dict]) -> dict[str, str]:
    """
    Decide what each document is mostly about.

    Facts extracted from a page of highlights have no named subject — the page
    says "98,135 / Workforce strength" and never repeats the company name,
    because a reader knows whose report they are holding. Those facts arrive
    with subject = null, and null means "this document's own subject".

    We work out what that is by taking the most frequently named subject in the
    document, which is the closest thing to reading the cover page without
    spending an API call on it.
    """
    subjects_named_in_each_document = defaultdict(Counter)
    for fact_row in fact_rows:
        if not fact_row["subject_raw"]:
            continue
        # "Our Company" must not be allowed to become the answer to "what is
        # this document about?" — it is the question restated.
        if a_name_is_the_document_referring_to_itself(fact_row["subject_raw"]):
            continue
        subjects_named_in_each_document[fact_row["doc_id"]][
            simplify_a_name(fact_row["subject_raw"])
        ] += 1

    main_subject_of_each_document = {}
    for document_id, counter in subjects_named_in_each_document.items():
        main_subject_of_each_document[document_id] = counter.most_common(1)[0][0]
    return main_subject_of_each_document


def group_names_into_entities(all_simplified_names: list[str]) -> dict[str, str]:
    """
    Decide which simplified names refer to the same thing.

    Exact matches group for free. Anything left over is compared with fuzzy
    matching, but only against names we have already decided to keep — so a
    chain of near-misses cannot quietly drag two clearly different companies
    into one group.

    Returns a mapping from every name to the name chosen to represent its group.
    """
    names_by_how_common = [
        name for name, _ in Counter(all_simplified_names).most_common()
    ]

    representative_of_each_name: dict[str, str] = {}
    names_we_have_kept: list[str] = []

    for name in names_by_how_common:
        best_match = None
        best_score = 0

        for kept_name in names_we_have_kept:
            score = fuzz.token_sort_ratio(name, kept_name)
            if score > best_score:
                best_score = score
                best_match = kept_name

        if best_match is not None and best_score >= HOW_ALIKE_TWO_NAMES_MUST_BE:
            representative_of_each_name[name] = best_match
        else:
            representative_of_each_name[name] = name
            names_we_have_kept.append(name)

    return representative_of_each_name


def build_entities(fact_rows: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """
    Give every fact an entity id, and build the rows for the entities table.

    Returns the fact-to-entity mapping and the entity rows.
    """
    main_subject_of_each_document = work_out_each_documents_main_subject(fact_rows)

    simplified_name_of_each_fact: dict[str, str] = {}
    for fact_row in fact_rows:
        subject = fact_row["subject_raw"]

        # No subject, or a subject that is only the document referring to
        # itself, both mean the same thing: this document's own subject.
        if not subject or a_name_is_the_document_referring_to_itself(subject):
            simplified = main_subject_of_each_document.get(
                fact_row["doc_id"], "unnamed subject"
            )
        else:
            simplified = simplify_a_name(subject)

        simplified_name_of_each_fact[fact_row["fact_id"]] = simplified

    representative_of_each_name = group_names_into_entities(
        list(simplified_name_of_each_fact.values())
    )

    # Collect what we know about each entity: every spelling seen, and any
    # identifier found in the evidence of a fact about it.
    spellings_seen: dict[str, Counter] = defaultdict(Counter)
    identifiers_found: dict[str, dict] = defaultdict(dict)
    how_many_facts: Counter = Counter()

    entity_id_of_each_fact: dict[str, str] = {}

    for fact_row in fact_rows:
        simplified = simplified_name_of_each_fact[fact_row["fact_id"]]
        representative = representative_of_each_name[simplified]
        entity_id = make_a_readable_id("ent", representative)

        entity_id_of_each_fact[fact_row["fact_id"]] = entity_id
        how_many_facts[entity_id] += 1

        # A self-reference is not a name for this entity, so it must not become
        # an alias — and above all must not end up as the display name. The
        # first version of this picked aliases alphabetically and proudly
        # labelled 2,002 facts as belonging to "Company".
        if fact_row["subject_raw"] and not a_name_is_the_document_referring_to_itself(
            fact_row["subject_raw"]
        ):
            spellings_seen[entity_id][fact_row["subject_raw"]] += 1

        identifiers_found[entity_id].update(
            find_identifiers_in(fact_row["evidence_text"] or "")
        )

    entity_rows = []
    for entity_id, count in how_many_facts.items():
        spellings_by_how_common = spellings_seen.get(entity_id, Counter())
        if spellings_by_how_common:
            # The wording the documents actually favour, not whichever variant
            # happens to sort first.
            canonical_name = spellings_by_how_common.most_common(1)[0][0]
        else:
            canonical_name = entity_id[4:].replace("_", " ")

        entity_rows.append({
            "entity_id": entity_id,
            "canonical_name": canonical_name,
            "aliases": database.as_json(sorted(spellings_by_how_common)),
            "identifiers": database.as_json(identifiers_found.get(entity_id, {})),
        })

    return entity_id_of_each_fact, entity_rows


# ===========================================================================
# 3. Attribute families
# ===========================================================================

# Words that change WHAT KIND of measurement a phrase describes, rather than
# what it is about.
#
# "Revenue", "revenue CAGR" and "revenue increase" share almost every word, so
# fuzzy matching happily merges them — and then a growth rate of 48.49% gets
# compared against an amount of a billion dollars and reported as a
# contradiction. That really happened, and it is why this list exists.
#
# A growth rate is not the quantity it measures. A margin is not the amount it
# is a margin of. Two phrases may only be grouped if they carry the SAME set of
# these words.
#
# Nothing here names a company, a document or an industry — these are the words
# any domain uses to say "a rate of" or "a share of".
WORDS_THAT_CHANGE_WHAT_IS_BEING_MEASURED = {
    "cagr", "growth", "increase", "decrease", "decline", "change", "delta",
    "margin", "ratio", "rate", "percentage", "percent", "share", "proportion",
    "yoy", "qoq", "per", "average", "median", "total", "net", "gross",
    "adjusted", "reported", "estimated", "projected", "forecast",
}


def simplify_an_attribute(attribute: str) -> str:
    """Reduce an attribute phrase to its comparable form."""
    without_punctuation = re.sub(r"[^\w\s]", " ", (attribute or "").lower())
    return " ".join(without_punctuation.split())


def what_kind_of_measurement_is_this(phrase: str) -> frozenset:
    """
    The set of words in a phrase that decide what KIND of measure it is.

    Two phrases can only belong to the same family if these match exactly.
    "revenue" and "revenue growth" differ here, so they stay apart however
    similar their spelling — which is the whole point.
    """
    return frozenset(
        word for word in phrase.split()
        if word in WORDS_THAT_CHANGE_WHAT_IS_BEING_MEASURED
    )


def group_attributes_into_families(
    all_attributes: list[str],
) -> tuple[dict[str, str], dict[str, int]]:
    """
    Group attribute phrases that mean the same kind of thing.

    THIS IS WHAT LETS ANYTHING BE COMPARED AT ALL. "Revenue from operations"
    and "Revenue from contract with customers" are the same measurement written
    two ways. Compared as exact strings they never meet, and the corpus appears
    to contain no contradictions whatever.

    The grouping is by spelling similarity, which has a real and honest limit:
    it will connect "revenue" with "revenue from operations", and it will NOT
    connect "workforce strength" with "team size", because those share almost
    no characters. Fixing that needs semantic similarity — see the note in
    docs/03_APPROACH.md, D-07.

    Phrases are considered most-common-first, so a family is named by the
    wording the documents actually favour rather than by whichever variant
    happened to be seen first.
    """
    how_often_each_appears = Counter(
        simplify_an_attribute(attribute) for attribute in all_attributes if attribute
    )

    family_of_each_phrase: dict[str, str] = {}
    families_we_have_kept: list[str] = []

    for phrase, times_seen in how_often_each_appears.most_common():
        if not phrase:
            continue

        kind_of_this_phrase = what_kind_of_measurement_is_this(phrase)

        best_family = None
        best_score = 0
        for family in families_we_have_kept:
            # A rate and an amount are never the same family, however alike
            # their wording. This check comes before the similarity score
            # because no amount of similarity should overrule it.
            if what_kind_of_measurement_is_this(family) != kind_of_this_phrase:
                continue
            score = fuzz.token_sort_ratio(phrase, family)
            if score > best_score:
                best_score = score
                best_family = family

        if best_family is not None and best_score >= HOW_ALIKE_TWO_ATTRIBUTES_MUST_BE:
            family_of_each_phrase[phrase] = best_family
            continue

        family_of_each_phrase[phrase] = phrase
        # Only a phrase the documents use more than once is allowed to become
        # the name of a family. A one-off wording naming a family would pull
        # commoner phrasings under an unusual label.
        if times_seen >= HOW_OFTEN_A_PHRASE_MUST_APPEAR_TO_NAME_ITS_FAMILY:
            families_we_have_kept.append(phrase)

    return family_of_each_phrase, how_often_each_appears


# ===========================================================================
# 3b. Merging families that mean the same thing but share no spelling
# ===========================================================================

# Only families with at least this many facts are worth asking about. A phrase
# used once will not be the hinge of a cross-document comparison, and including
# every one-off wording would fill the request with noise.
FEWEST_FACTS_FOR_A_FAMILY_TO_BE_WORTH_MERGING = 3

# Everything goes in ONE request so every label can be compared with every
# other. Splitting into batches would mean "revenue" and "turnover" could land
# in different batches and never meet — which is the entire problem we are
# solving.
MOST_LABELS_TO_SEND_AT_ONCE = 260

FAMILY_MERGE_PROMPT_VERSION = "merge-families-v1"

FAMILY_MERGE_INSTRUCTIONS = """\
Below is a list of measurement labels taken from a set of documents.

Group together the labels that name THE SAME MEASUREMENT written differently.
Different documents describe identical quantities in different words, and this
grouping is what lets their figures be compared at all.

GROUP labels that a reader would accept as interchangeable names for one
quantity, for example:
  "revenue" / "revenue from operations" / "revenue from contracts with customers"
  "workforce strength" / "team size" / "number of employees"

DO NOT GROUP:
  - a quantity with a RATIO or MARGIN of it
        "revenue" and "revenue margin" are different measurements
  - a quantity with its CHANGE or GROWTH
        "revenue" and "revenue growth" are different measurements
  - a quantity with a DIFFERENT LINE ITEM that merely sounds similar
        "current borrowings" and "non-current borrowings" are different
  - things you are unsure about

BEING WRONG IN THE TWO DIRECTIONS IS NOT EQUALLY BAD. Leaving two labels apart
loses a possible comparison. Merging two labels wrongly makes two unrelated
numbers look like a contradiction, and that false contradiction will be
reported confidently with real evidence attached. WHEN IN DOUBT, DO NOT GROUP.

Return every label exactly once. A label that groups with nothing forms a group
of one. For each group give a canonical_label: the clearest of its members,
chosen from the list, not invented.
"""


class MergedAttributeFamily(BaseModel):
    """One group of labels the model judged to name the same measurement."""
    canonical_label: str
    members: list[str]


def merge_families_that_mean_the_same_thing(
    family_of_each_phrase: dict[str, str],
    how_often_each_appears: Counter,
) -> dict[str, str]:
    """
    Ask the model which family labels name the same measurement.

    WHY THIS NEEDS A MODEL AT ALL. Everything up to here groups by spelling,
    which is deterministic, free, and blind in one specific way: "revenue" and
    "revenue from contracts with customers" share characters and group, while
    "workforce strength" and "team size" share almost none and never will.
    Recognising that two different phrases describe one quantity is a judgement
    about meaning, and that is the one job worth spending a model on.

    This is the same division as everywhere else in the project: code does the
    mechanical work, the model is asked only the question code cannot answer.

    Returns a mapping from the old family label to its merged label. On any
    failure it returns an empty mapping, leaving the spelling-based grouping
    untouched — a merge step that half-worked would be worse than one that did
    not run.
    """
    facts_per_family: Counter = Counter()
    for phrase, family in family_of_each_phrase.items():
        facts_per_family[family] += how_often_each_appears[phrase]

    labels_worth_asking_about = [
        label for label, count in facts_per_family.items()
        if count >= FEWEST_FACTS_FOR_A_FAMILY_TO_BE_WORTH_MERGING
    ]
    # Most facts first so the cap keeps what matters, then alphabetical so the
    # prompt text is identical on every run and the cache actually hits.
    labels_worth_asking_about.sort(key=lambda label: -facts_per_family[label])
    labels_worth_asking_about = sorted(labels_worth_asking_about[:MOST_LABELS_TO_SEND_AT_ONCE])

    if len(labels_worth_asking_about) < 2:
        return {}

    print(f"  asking the model about {len(labels_worth_asking_about)} family labels...")

    prompt = "\n".join([
        FAMILY_MERGE_INSTRUCTIONS,
        "",
        "THE LABELS:",
        *(f"- {label}" for label in labels_worth_asking_about),
    ])

    try:
        answer = llm_client.ask_the_model(
            prompt,
            prompt_version=FAMILY_MERGE_PROMPT_VERSION,
            expect_json=True,
            response_schema=list[MergedAttributeFamily],
        )
        groups = [MergedAttributeFamily.model_validate(g) for g in json.loads(answer)]
    except Exception as problem:
        print(f"  merge pass failed, keeping the spelling-based grouping: {problem}")
        database.record_failure(
            stage="normalize",
            kind="family_merge_failed",
            detail=f"{type(problem).__name__}: {problem}"[:1500],
        )
        return {}

    labels_we_asked_about = set(labels_worth_asking_about)
    merged_label_of_each_family: dict[str, str] = {}
    how_many_merges = 0

    for group in groups:
        # Only labels we actually sent may be grouped. A model inventing or
        # rewording a label would silently create a family nothing belongs to.
        real_members = [m for m in group.members if m in labels_we_asked_about]
        if len(real_members) < 2:
            continue

        canonical = (
            group.canonical_label
            if group.canonical_label in labels_we_asked_about
            else max(real_members, key=lambda label: facts_per_family[label])
        )
        for member in real_members:
            if member != canonical:
                merged_label_of_each_family[member] = canonical
                how_many_merges += 1

    print(f"  the model merged {how_many_merges} labels into "
          f"{len(set(merged_label_of_each_family.values()))} families")
    return merged_label_of_each_family


# ===========================================================================
# 4. Scope tags
# ===========================================================================

def turn_scope_into_tags(scope_raw: str | None) -> list[str]:
    """
    Turn a sentence of qualification into comparable tokens.

    "Includes permanent employees, contractual workers and last mile delivery
    partner agents" becomes the meaningful words in it. Step 7 then compares
    two facts' tags as sets: if one includes something the other does not, that
    is a scope difference, and a scope difference explains a value difference
    without either figure being wrong.

    Ordinary stopwords are dropped so that "includes" and "the" do not count as
    agreement between two otherwise unrelated scopes.
    """
    if not scope_raw:
        return []
    without_punctuation = re.sub(r"[^\w\s]", " ", scope_raw.lower())
    return sorted({
        word for word in without_punctuation.split()
        if word not in WORDS_THAT_CARRY_NO_MEANING and len(word) > 2
    })


# ===========================================================================
# Running it
# ===========================================================================

def load_the_grounded_facts() -> list[dict]:
    """
    Only grounded facts are normalised.

    A fact whose evidence could not be found is not going to be compared with
    anything, so spending effort making it comparable would be work in service
    of a conclusion we would refuse to draw.
    """
    return database.fetch_all_rows(
        """
        SELECT fact_id, doc_id, subject_raw, attribute_raw, value_raw, value_kind,
               unit, currency, period_raw, as_of_raw, scope_raw, evidence_text
        FROM facts
        WHERE grounded = TRUE
        ORDER BY fact_id
        """
    )


def print_the_biggest_families(
    family_of_each_phrase: dict[str, str],
    how_often_each_appears: Counter,
    how_many_to_show: int = 15,
) -> None:
    """
    Show the largest attribute families so they can be judged by eye.

    This is the one place in the step with a genuine judgement in it, so it
    deserves looking at rather than trusting.
    """
    members_of_each_family = defaultdict(list)
    for phrase, family in family_of_each_phrase.items():
        members_of_each_family[family].append(phrase)

    families_by_size = sorted(
        members_of_each_family.items(),
        key=lambda pair: -sum(how_often_each_appears[p] for p in pair[1]),
    )

    print()
    print("=" * 78)
    print(f"THE {how_many_to_show} BIGGEST ATTRIBUTE FAMILIES — do these belong together?")
    print("=" * 78)

    for family, members in families_by_size[:how_many_to_show]:
        total_facts = sum(how_often_each_appears[p] for p in members)
        print(f"\n  {family!r}  —  {len(members)} phrasings, {total_facts} facts")
        for member in sorted(members, key=lambda p: -how_often_each_appears[p])[:5]:
            if member != family:
                print(f"      also: {member!r}  ({how_often_each_appears[member]})")


def main() -> int:
    print("=" * 78)
    print("STEP 5 — normalising facts so they can be compared")
    print("=" * 78)

    config.stop_unless_these_settings_are_filled_in(["DATABASE_URL"])

    fact_rows = load_the_grounded_facts()
    if not fact_rows:
        print("No grounded facts. Run step_04_check_grounding first.")
        return 1
    print(f"\n{len(fact_rows):,} grounded facts to normalise")

    # --- the financial year end, learned from the documents ---------------
    every_period_phrase = [
        row["period_raw"] for row in fact_rows if row["period_raw"]
    ]
    financial_year_ends_on = work_out_the_financial_year_end(every_period_phrase)
    print(
        f"\nFinancial year end learned from the documents: "
        f"month {financial_year_ends_on[0]}, day {financial_year_ends_on[1]}"
    )
    print("  (read from every explicit 'year ended <date>' phrase — not hard-coded)")

    # --- values, periods, dates -------------------------------------------
    print("\nParsing values, periods and dates...")
    parsed_values = [
        normalise_one_fact_value(fact_row, financial_year_ends_on)
        for fact_row in fact_rows
    ]

    # --- entities ----------------------------------------------------------
    print("Resolving entities...")
    entity_id_of_each_fact, entity_rows = build_entities(fact_rows)

    # --- attribute families -------------------------------------------------
    print("Grouping attributes into families...")
    family_of_each_phrase, how_often_each_appears = group_attributes_into_families(
        [row["attribute_raw"] for row in fact_rows]
    )
    print(f"  spelling similarity produced "
          f"{len(set(family_of_each_phrase.values()))} families")

    # Spelling gets us variants of the same wording. Meaning gets us the same
    # measurement written two different ways, which is what a comparison across
    # three documents by three different authors actually needs.
    if "--no-llm" not in sys.argv[1:]:
        merged_label_of_each_family = merge_families_that_mean_the_same_thing(
            family_of_each_phrase, how_often_each_appears
        )
        for phrase, family in list(family_of_each_phrase.items()):
            if family in merged_label_of_each_family:
                family_of_each_phrase[phrase] = merged_label_of_each_family[family]

    family_rows = []
    members_of_each_family = defaultdict(list)
    for phrase, family in family_of_each_phrase.items():
        members_of_each_family[family].append(phrase)
    for family, members in members_of_each_family.items():
        family_rows.append({
            "family_id": make_a_readable_id("fam", family),
            "label": family,
            "member_phrases": database.as_json(sorted(members)),
        })

    # --- write everything back ---------------------------------------------
    print("Saving...")
    database.execute_sql("DELETE FROM entities")
    database.execute_sql("DELETE FROM attribute_families")
    database.insert_rows_in_batches("entities", entity_rows)
    database.insert_rows_in_batches("attribute_families", family_rows)

    updates = []
    for fact_row, parsed in zip(fact_rows, parsed_values):
        phrase = simplify_an_attribute(fact_row["attribute_raw"])
        family = family_of_each_phrase.get(phrase, phrase)
        updates.append((
            parsed["value_num"],
            parsed["multiplier"],
            parsed["currency"],
            parsed["period_start"],
            parsed["period_end"],
            parsed["as_of_date"],
            entity_id_of_each_fact[fact_row["fact_id"]],
            make_a_readable_id("fam", family),
            database.as_json(turn_scope_into_tags(fact_row["scope_raw"])),
            fact_row["fact_id"],
        ))

    database.update_rows_in_batches(
        """
        UPDATE facts
        SET value_num = %s, multiplier = %s, currency = %s,
            period_start = %s, period_end = %s, as_of_date = %s,
            entity_id = %s, attribute_family = %s, scope_tags = %s
        WHERE fact_id = %s
        """,
        updates,
    )

    # --- what we ended up with ---------------------------------------------
    how_many_with_a_number = sum(1 for p in parsed_values if p["value_num"] is not None)
    how_many_with_a_period = sum(1 for p in parsed_values if p["period_start"])
    how_many_with_an_as_of = sum(1 for p in parsed_values if p["as_of_date"])
    how_many_with_scope_tags = sum(1 for row in fact_rows if turn_scope_into_tags(row["scope_raw"]))

    print()
    print("=" * 78)
    print("RESULT")
    print("=" * 78)
    print(f"  facts normalised           : {len(fact_rows):,}")
    print(f"  with a comparable number   : {how_many_with_a_number:,}  "
          f"({how_many_with_a_number / len(fact_rows):.0%})")
    print(f"  with a real date range     : {how_many_with_a_period:,}  "
          f"({how_many_with_a_period / len(fact_rows):.0%})")
    print(f"  with a real as-of date     : {how_many_with_an_as_of:,}  "
          f"({how_many_with_an_as_of / len(fact_rows):.0%})")
    print(f"  with scope tags            : {how_many_with_scope_tags:,}  "
          f"({how_many_with_scope_tags / len(fact_rows):.0%})")
    print()
    print(f"  distinct entities          : {len(entity_rows):,}")
    print(f"  distinct attribute phrases : {len(how_often_each_appears):,}")
    print(f"  attribute families         : {len(family_rows):,}")

    print()
    print("=" * 78)
    print("THE BIGGEST ENTITIES")
    print("=" * 78)
    biggest = database.fetch_all_rows(
        """
        SELECT e.canonical_name, e.identifiers, count(f.fact_id) AS facts
        FROM entities e LEFT JOIN facts f ON f.entity_id = e.entity_id
        GROUP BY e.entity_id, e.canonical_name, e.identifiers
        ORDER BY facts DESC LIMIT 10
        """
    )
    for row in biggest:
        identifiers = row["identifiers"] or {}
        shown = f"  {row['canonical_name'][:46]:<48}{row['facts']:>6,} facts"
        if identifiers:
            shown += f"   ids: {list(identifiers.items())[:2]}"
        print(shown)

    print_the_biggest_families(family_of_each_phrase, how_often_each_appears)

    print()
    print("Facts are now comparable. Next: Step 6 finds pairs worth comparing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
