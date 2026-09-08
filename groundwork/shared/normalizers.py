"""
Turning what a document wrote into something we can compare.

Check the parsers with:
    .\\.venv\\Scripts\\python.exe -m groundwork.shared.normalizers

---------------------------------------------------------------------------
WHY THIS IS CODE AND NOT A PROMPT
---------------------------------------------------------------------------
We could ask the model to return 12400000000 instead of "1,240 crore". We
deliberately do not. Parsing here is deterministic, identical on every run, and
checkable by eye against a table of examples. The most important comparison in
the project — is 1,240 the same amount as 12,400 million? — should not depend
on output we cannot reproduce.

---------------------------------------------------------------------------
WHY EVERY BUG IN THIS FILE IS SILENT
---------------------------------------------------------------------------
Nothing here crashes when it is wrong. A missed "crore" does not raise; it just
makes a number a hundred million times too small, and that fact then quietly
disagrees with every other statement of the same quantity. A bracket read as
punctuation instead of a minus sign turns a loss into a profit.

That is why this file ends with a table of worked examples that prints
PASS/FAIL for each. It is the closest thing to a test suite the project keeps,
and it is here because this is where being wrong costs the most.

---------------------------------------------------------------------------
NOTHING HERE IS DOCUMENT-SPECIFIC
---------------------------------------------------------------------------
The scale words are the ones used in written English and Indian English
generally — crore, lakh, million, billion. The date patterns are ordinary date
patterns. Where a convention genuinely varies between documents, such as which
month a financial year ends in, it is READ FROM THE DOCUMENTS at runtime rather
than written in here. See `work_out_the_financial_year_end`.
"""

import re
import sys
from dataclasses import dataclass
from datetime import date


# ===========================================================================
# Numbers
# ===========================================================================

# How much each scale word multiplies by. Indian and international scales
# together, because financial documents mix them freely — often on one page.
SCALE_WORD_MULTIPLIERS = {
    "thousand": 1_000,
    "thousands": 1_000,
    "k": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "million": 1_000_000,
    "millions": 1_000_000,
    "mn": 1_000_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
    "billion": 1_000_000_000,
    "billions": 1_000_000_000,
    "bn": 1_000_000_000,
    "trillion": 1_000_000_000_000,
    "tn": 1_000_000_000_000,
}

# Words and symbols meaning "about", which we record rather than discard. A
# figure written ">2.8Bn" is a floor, not a measurement, and comparing it to an
# exact number as though both were precise would produce false disagreements.
SIGNS_A_NUMBER_IS_APPROXIMATE = (">", "<", "~", "≈", "+", "approx", "about", "over", "under")

# Currency written as a symbol or a code. Stripped before parsing, but the code
# is kept so amounts in different currencies are never compared as if equal.
CURRENCY_SYMBOLS_AND_CODES = {
    "₹": "INR", "rs.": "INR", "rs": "INR", "inr": "INR",
    "$": "USD", "us$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR",
    "£": "GBP", "gbp": "GBP",
}

# The numeric core: digits, with optional thousands separators and decimals.
NUMBER_PATTERN = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass
class ParsedNumber:
    """What we managed to work out from a written value."""
    value: float | None          # the canonical amount, all scaling applied
    multiplier: float            # what we multiplied by, so the maths is auditable
    is_negative: bool
    is_approximate: bool
    is_percentage: bool
    currency: str | None
    why_it_failed: str | None = None


def find_the_scale_word_in(some_text: str) -> tuple[float, str | None]:
    """
    Look for crore, million, lakh and friends. Returns the multiplier and the word.

    The boundary rule is "not next to a LETTER", not the usual word boundary,
    and that distinction was a real bug.

    `\\b` looks for a change between word and non-word characters. Digits are
    word characters, so in ">2.8Bn" there is no boundary between "8" and "Bn" —
    `\\bbn\\b` finds nothing, and 2.8 billion silently becomes 2.8. A number a
    billion times too small, with no error raised anywhere.

    Requiring only that no LETTER sits on either side fixes it: "8bn" and
    "18.8Mn" match, while "cr" inside "increase" and "k" inside "lakh" stay
    correctly ignored.
    """
    lowered = some_text.lower()
    for scale_word, multiplier in SCALE_WORD_MULTIPLIERS.items():
        pattern = rf"(?<![a-z]){re.escape(scale_word)}(?![a-z])"
        if re.search(pattern, lowered):
            return multiplier, scale_word
    return 1.0, None


def find_the_currency_in(some_text: str) -> str | None:
    """Spot a currency symbol or code."""
    lowered = some_text.lower()
    for symbol, code in CURRENCY_SYMBOLS_AND_CODES.items():
        if symbol in {"rs", "inr", "usd", "eur", "gbp", "us$"}:
            if re.search(rf"\b{re.escape(symbol)}\b", lowered):
                return code
        elif symbol in lowered:
            return code
    return None


def parse_number(
    value_raw: str,
    unit: str | None = None,
    currency: str | None = None,
) -> ParsedNumber:
    """
    Turn a written value into a comparable number.

        "1,240"            -> 1240
        "(1,008 Cr)"       -> -10080000000
        "(452"             -> -452          (unmatched bracket, see below)
        ">2.8Bn"           -> 2800000000, approximate
        "5.03%"            -> 5.03, percentage
        "98,135"           -> 98135

    THE UNMATCHED BRACKET IS NOT A TYPO. In accounting, brackets mean negative,
    and one of our models returns "(452" — keeping the opening bracket and
    dropping the closing one. A parser that required both would read a large
    loss as a large profit, silently. So an opening bracket alone is enough.

    The scale word is looked for in the value itself first and then in the unit,
    because documents put it in either place: "1,240 crore" and
    "1,240" with unit "crore" mean the same thing.
    """
    if not value_raw or not value_raw.strip():
        return ParsedNumber(None, 1.0, False, False, False, None, "empty value")

    text = value_raw.strip()
    lowered = text.lower()

    is_percentage = "%" in text or (unit or "").strip().lower() in {"%", "percent", "percentage"}
    is_approximate = any(sign in lowered for sign in SIGNS_A_NUMBER_IS_APPROXIMATE)

    # An opening bracket alone is enough. See the docstring.
    is_negative = text.startswith("(") or text.startswith("-")

    currency_code = currency or find_the_currency_in(text) or find_the_currency_in(unit or "")

    number_match = NUMBER_PATTERN.search(text)
    if number_match is None:
        return ParsedNumber(
            None, 1.0, is_negative, is_approximate, is_percentage, currency_code,
            f"no digits found in {value_raw!r}",
        )

    try:
        bare_number = float(number_match.group().replace(",", ""))
    except ValueError:
        return ParsedNumber(
            None, 1.0, is_negative, is_approximate, is_percentage, currency_code,
            f"could not read {number_match.group()!r} as a number",
        )

    # A percentage is already on its own scale. Multiplying "5.03%" by anything
    # found in a nearby unit would be nonsense.
    if is_percentage:
        multiplier = 1.0
    else:
        multiplier, _ = find_the_scale_word_in(text)
        if multiplier == 1.0 and unit:
            multiplier, _ = find_the_scale_word_in(unit)

    canonical_value = abs(bare_number) * multiplier
    if is_negative:
        canonical_value = -canonical_value

    return ParsedNumber(
        value=canonical_value,
        multiplier=multiplier,
        is_negative=is_negative,
        is_approximate=is_approximate,
        is_percentage=is_percentage,
        currency=currency_code,
    )


# ===========================================================================
# Dates
# ===========================================================================

MONTH_NUMBER_BY_NAME = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_MONTH_NAMES_PATTERN = "|".join(sorted(MONTH_NUMBER_BY_NAME, key=len, reverse=True))

# "March 31, 2024"  /  "Mar 31 2024"
DATE_AS_MONTH_DAY_YEAR = re.compile(
    rf"\b({_MONTH_NAMES_PATTERN})\.?\s+(\d{{1,2}})\b[,\s]+(\d{{4}})", re.IGNORECASE
)
# "31 March 2024"  /  "31st March, 2024"
DATE_AS_DAY_MONTH_YEAR = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES_PATTERN})\.?\s*,?\s*(\d{{4}})", re.IGNORECASE
)
# "31-03-2024" / "31/03/2024"
DATE_AS_DIGITS_DAY_FIRST = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b")
# "2024-03-31"
DATE_AS_DIGITS_YEAR_FIRST = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
# "March 2024" / "Mar '24" — a month with no day
MONTH_AND_YEAR_ONLY = re.compile(
    rf"\b({_MONTH_NAMES_PATTERN})\.?\s*'?\s*(\d{{2,4}})\b", re.IGNORECASE
)
# A bare four-digit year
YEAR_ONLY = re.compile(r"\b(19|20)(\d{2})\b")

LAST_DAY_OF_EACH_MONTH = {
    1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
    7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
}


def last_day_of(year: int, month: int) -> int:
    """The final day of a month, February in a leap year included."""
    if month == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        return 29
    return LAST_DAY_OF_EACH_MONTH[month]


def make_a_date(year: int, month: int, day: int) -> date | None:
    """Build a date, returning None rather than raising on nonsense."""
    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= last_day_of(year, month)):
        return None
    if not (1900 <= year <= 2100):
        return None
    return date(year, month, day)


def expand_two_digit_year(year_text: str) -> int:
    """Turn "24" into 2024. Documents write "Mar '24" and mean this century."""
    year_number = int(year_text)
    if year_number >= 100:
        return year_number
    return 2000 + year_number


def parse_date(some_text: str) -> date | None:
    """
    Read a date out of a phrase, in whatever form the document wrote it.

    A month with no day becomes the LAST day of that month, and a bare year the
    last day of that year. This is not arbitrary: these phrases nearly always
    appear as "as at" or "period ended", which mean the end of the span.
    """
    if not some_text:
        return None
    text = some_text.strip()

    match = DATE_AS_DIGITS_YEAR_FIRST.search(text)
    if match:
        return make_a_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = DATE_AS_MONTH_DAY_YEAR.search(text)
    if match:
        month = MONTH_NUMBER_BY_NAME[match.group(1).lower()]
        return make_a_date(int(match.group(3)), month, int(match.group(2)))

    match = DATE_AS_DAY_MONTH_YEAR.search(text)
    if match:
        month = MONTH_NUMBER_BY_NAME[match.group(2).lower()]
        return make_a_date(int(match.group(3)), month, int(match.group(1)))

    match = DATE_AS_DIGITS_DAY_FIRST.search(text)
    if match:
        return make_a_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))

    match = MONTH_AND_YEAR_ONLY.search(text)
    if match:
        month = MONTH_NUMBER_BY_NAME[match.group(1).lower()]
        year = expand_two_digit_year(match.group(2))
        return make_a_date(year, month, last_day_of(year, month))

    match = YEAR_ONLY.search(text)
    if match:
        year = int(match.group(0))
        return make_a_date(year, 12, 31)

    return None


# ===========================================================================
# Periods
# ===========================================================================

HOW_MANY_MONTHS_EACH_WORD_MEANS = {
    "twelve": 12, "12": 12, "year": 12, "annual": 12, "full year": 12,
    "nine": 9, "9": 9,
    "six": 6, "6": 6, "half": 6,
    "three": 3, "3": 3, "quarter": 3,
    "one": 1, "1": 1, "month": 1,
}

# "for the nine months period ended December 31, 2021"
PERIOD_OF_N_MONTHS_ENDED = re.compile(
    r"\b(twelve|nine|six|three|one|\d{1,2})\s*[-\s]?months?\b[^.]{0,20}?\bended\b",
    re.IGNORECASE,
)
# "for the year ended 31 March 2024"
PERIOD_OF_A_YEAR_ENDED = re.compile(r"\b(?:year|fiscal|financial year)\s+ended\b", re.IGNORECASE)
# "quarter ended March 31, 2024"  /  "Q4 FY24"
PERIOD_OF_A_QUARTER_ENDED = re.compile(r"\bquarter\s+ended\b", re.IGNORECASE)
QUARTER_AND_FINANCIAL_YEAR = re.compile(r"\bQ([1-4])\s*[-\s]?FY\s*'?(\d{2,4})", re.IGNORECASE)
# "FY24", "FY2024", "Fiscal 2024", "fiscal year 2024"
FINANCIAL_YEAR_ONLY = re.compile(r"\b(?:FY|fiscal(?:\s+year)?)\s*'?(\d{2,4})\b", re.IGNORECASE)


def step_back_months(from_date: date, how_many_months: int) -> date:
    """
    The first day of a span of N months ending on the given date.

    "nine months ended 31 December 2021" begins on 1 April 2021 — nine months
    inclusive of December, so we step back eight month boundaries and take the
    first of that month.
    """
    month = from_date.month - (how_many_months - 1)
    year = from_date.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


@dataclass
class ParsedPeriod:
    """A span of time, and how confident we are that we read it right."""
    start: date | None
    end: date | None
    how_many_months: int | None
    why_it_failed: str | None = None


def parse_period(
    period_text: str,
    financial_year_ends_on: tuple[int, int] = (3, 31),
) -> ParsedPeriod:
    """
    Turn a written period into a start and an end date.

        "year ended 31 March 2024"            -> 2023-04-01 .. 2024-03-31
        "nine months period ended 31 Dec 2021" -> 2021-04-01 .. 2021-12-31
        "Q4 FY24"                              -> 2024-01-01 .. 2024-03-31
        "FY24"                                 -> 2023-04-01 .. 2024-03-31

    `financial_year_ends_on` is a (month, day) pair and is NOT hard-coded — it
    is worked out from the documents themselves by
    `work_out_the_financial_year_end`, because an Indian annual report and an
    American one end their years in different months and the same "FY24" means
    different things in each.
    """
    if not period_text or not period_text.strip():
        return ParsedPeriod(None, None, None, "no period given")

    text = period_text.strip()
    financial_year_end_month, financial_year_end_day = financial_year_ends_on

    # "Q4 FY24" — no explicit dates, so work it out from the financial year.
    quarter_match = QUARTER_AND_FINANCIAL_YEAR.search(text)
    if quarter_match:
        quarter_number = int(quarter_match.group(1))
        year_the_financial_year_ends = expand_two_digit_year(quarter_match.group(2))
        end_of_financial_year = date(
            year_the_financial_year_ends, financial_year_end_month, financial_year_end_day
        )
        start_of_financial_year = step_back_months(end_of_financial_year, 12)

        months_into_the_year = (quarter_number - 1) * 3
        quarter_start_month = start_of_financial_year.month + months_into_the_year
        quarter_start_year = start_of_financial_year.year
        while quarter_start_month > 12:
            quarter_start_month -= 12
            quarter_start_year += 1

        quarter_start = date(quarter_start_year, quarter_start_month, 1)
        quarter_end_month = quarter_start_month + 2
        quarter_end_year = quarter_start_year
        while quarter_end_month > 12:
            quarter_end_month -= 12
            quarter_end_year += 1
        quarter_end = date(
            quarter_end_year, quarter_end_month, last_day_of(quarter_end_year, quarter_end_month)
        )
        return ParsedPeriod(quarter_start, quarter_end, 3)

    end_date = parse_date(text)

    if end_date is not None:
        months_match = PERIOD_OF_N_MONTHS_ENDED.search(text)
        if months_match:
            word = months_match.group(1).lower()
            how_many_months = HOW_MANY_MONTHS_EACH_WORD_MEANS.get(word)
            if how_many_months is None:
                how_many_months = int(word) if word.isdigit() else 12
            return ParsedPeriod(
                step_back_months(end_date, how_many_months), end_date, how_many_months
            )

        if PERIOD_OF_A_QUARTER_ENDED.search(text):
            return ParsedPeriod(step_back_months(end_date, 3), end_date, 3)

        if PERIOD_OF_A_YEAR_ENDED.search(text):
            return ParsedPeriod(step_back_months(end_date, 12), end_date, 12)

    # "FY24" or "Fiscal 2024" with no other clue.
    financial_year_match = FINANCIAL_YEAR_ONLY.search(text)
    if financial_year_match:
        year_it_ends = expand_two_digit_year(financial_year_match.group(1))
        period_end = date(year_it_ends, financial_year_end_month, financial_year_end_day)
        return ParsedPeriod(step_back_months(period_end, 12), period_end, 12)

    # A date and nothing describing a span. Treat it as a single day rather
    # than inventing a length the document never stated.
    if end_date is not None:
        return ParsedPeriod(end_date, end_date, None)

    return ParsedPeriod(None, None, None, f"could not read a period from {period_text!r}")


def work_out_the_financial_year_end(period_phrases: list[str]) -> tuple[int, int]:
    """
    Learn from the documents which month and day their financial year ends on.

    THIS IS WHY "FY24" CAN BE PARSED WITHOUT HARD-CODING A COUNTRY. We look at
    every phrase that names an explicit year end — "year ended 31 March 2024" —
    and take the month and day that appears most often. An Indian report gives
    31 March, an American one 31 December, and neither is written into the code.

    Falls back to 31 December, the international default, when the documents
    never say.
    """
    how_often_each_ending_appears: dict[tuple[int, int], int] = {}

    for phrase in period_phrases:
        if not phrase or not PERIOD_OF_A_YEAR_ENDED.search(phrase):
            continue
        end_date = parse_date(phrase)
        if end_date is None:
            continue
        key = (end_date.month, end_date.day)
        how_often_each_ending_appears[key] = how_often_each_ending_appears.get(key, 0) + 1

    if not how_often_each_ending_appears:
        return (12, 31)

    return max(how_often_each_ending_appears.items(), key=lambda pair: pair[1])[0]


# ===========================================================================
# Checking the parsers by eye
# ===========================================================================

def run_the_check_table() -> int:
    """
    Print every tricky case with what we got and what we expected.

    This exists in place of a test suite, and only for this file, because every
    mistake here is silent. A missed "crore" does not crash — it makes a number
    ten million times too small, and no error is ever raised.
    """
    number_checks = [
        ("1,240", None, 1240.0),
        ("98,135", None, 98135.0),
        ("1,240", "crore", 12_400_000_000.0),
        ("1,240 crore", None, 12_400_000_000.0),
        ("₹1,240 Cr", None, 12_400_000_000.0),
        ("412.6", "million", 412_600_000.0),
        ("(1,008)", None, -1008.0),
        ("(452", None, -452.0),                 # unmatched bracket, see parse_number
        ("(1,008 Cr)", None, -10_080_000_000.0),
        ("5.03%", None, 5.03),
        ("(5.6%)", None, -5.6),
        (">2.8Bn", None, 2_800_000_000.0),
        (">33,200", None, 33200.0),
        ("18.8Mn Sq ft", None, 18_800_000.0),
        ("4.8", "Mn tonnes", 4_800_000.0),
        ("9,177", None, 9177.0),
        ("no digits at all", None, None),
        ("3,851", "lakh", 385_100_000.0),
        ("40", "percent", 40.0),
        ("1.6", "%", 1.6),
    ]

    date_checks = [
        ("March 31, 2024", date(2024, 3, 31)),
        ("31 March 2024", date(2024, 3, 31)),
        ("31-03-2024", date(2024, 3, 31)),
        ("2024-03-31", date(2024, 3, 31)),
        ("Mar '24", date(2024, 3, 31)),
        ("December 31, 2021", date(2021, 12, 31)),
        ("30 June 2024", date(2024, 6, 30)),
        ("2023", date(2023, 12, 31)),
        ("February 2024", date(2024, 2, 29)),   # leap year
        ("nonsense", None),
    ]

    period_checks = [
        ("year ended 31 March 2024", date(2023, 4, 1), date(2024, 3, 31)),
        ("nine months period ended December 31, 2021", date(2021, 4, 1), date(2021, 12, 31)),
        ("Q4 FY24", date(2024, 1, 1), date(2024, 3, 31)),
        ("Q1 FY24", date(2023, 4, 1), date(2023, 6, 30)),
        ("FY24", date(2023, 4, 1), date(2024, 3, 31)),
        ("Fiscal 2021", date(2020, 4, 1), date(2021, 3, 31)),
        ("quarter ended March 31, 2024", date(2024, 1, 1), date(2024, 3, 31)),
        ("six months ended September 30, 2023", date(2023, 4, 1), date(2023, 9, 30)),
    ]

    how_many_failed = 0

    print("=" * 78)
    print("NUMBERS")
    print("=" * 78)
    print(f"  {'written':<22}{'unit':<12}{'expected':>20}{'got':>20}")
    for value_raw, unit, expected in number_checks:
        got = parse_number(value_raw, unit).value
        it_passed = (got is None and expected is None) or (
            got is not None and expected is not None and abs(got - expected) < 0.001
        )
        if not it_passed:
            how_many_failed += 1
        marker = "   " if it_passed else "  FAIL ->"
        print(f"  {value_raw:<22}{str(unit or ''):<12}{str(expected):>20}{str(got):>20}{marker}")

    print()
    print("=" * 78)
    print("DATES")
    print("=" * 78)
    for text, expected in date_checks:
        got = parse_date(text)
        it_passed = got == expected
        if not it_passed:
            how_many_failed += 1
        marker = "   " if it_passed else "  FAIL"
        print(f"  {text:<34}{str(expected):>14}{str(got):>14}{marker}")

    print()
    print("=" * 78)
    print("PERIODS  (financial year ending 31 March, learned from the documents)")
    print("=" * 78)
    for text, expected_start, expected_end in period_checks:
        parsed = parse_period(text, financial_year_ends_on=(3, 31))
        it_passed = parsed.start == expected_start and parsed.end == expected_end
        if not it_passed:
            how_many_failed += 1
        marker = "   " if it_passed else "  FAIL"
        print(f"  {text:<44}{str(parsed.start)} .. {str(parsed.end)}{marker}")
        if not it_passed:
            print(f"  {'':<44}expected {expected_start} .. {expected_end}")

    print()
    if how_many_failed == 0:
        print("ALL CHECKS PASSED")
        return 0
    print(f"{how_many_failed} CHECKS FAILED — read the table above.")
    return 1


if __name__ == "__main__":
    sys.exit(run_the_check_table())
