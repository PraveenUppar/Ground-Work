"""
The shape of a fact.

This file is the single most important design decision in the project, so it is
worth being clear about what it is doing and why.

---------------------------------------------------------------------------
A SENTENCE CANNOT BE COMPARED. A SLOTTED CLAIM CAN.
---------------------------------------------------------------------------
Take this out of an annual report:

    "Revenue for the year ended 31 March 2024 was Rs 1,240 crore on a
     consolidated basis."

As text it can only be compared to another sentence by how similar the words
look, which tells you nothing about whether the two AGREE. Broken into slots it
can be lined up field by field against any other claim, and you can say exactly
which field differs.

---------------------------------------------------------------------------
THE QUALIFIERS ARE THE POINT
---------------------------------------------------------------------------
Most extraction schemas stop at subject, attribute and value. Ours does not,
and that is the difference between a system that reports contradictions and a
system that reports nonsense.

Revenue of 1,240 and revenue of 980 do not disagree if one covers FY24 and the
other FY23. Two headcounts do not disagree if one counts partner agents and the
other does not. Without period, as-of date and scope you cannot tell a real
contradiction from a difference that context fully explains — and the brief
asks for exactly that distinction as a deliverable.

---------------------------------------------------------------------------
WHY QUALIFIERS ARE CAPTURED AS WRITTEN, NOT AS DATES
---------------------------------------------------------------------------
`period_text` holds "year ended 31 March 2024", not a pair of dates. The
division of labour is deliberate:

    THE MODEL READS. It is good at recognising that a phrase describes a
    period, wherever that phrase happens to sit on the page.

    OUR CODE PARSES. Turning that phrase into dates in Python is
    deterministic, identical on every run, and checkable by eye.

Asking a model to do the date arithmetic would make the most important
comparison in the project depend on something we cannot reproduce or verify.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# What kind of thing a value is. This decides which column it lands in and how
# it can be compared: numbers by size, dates by order, text by meaning.
ValueKind = Literal["number", "date", "text"]

# Where in the passage a fact came from. Recorded because these do not deserve
# equal trust. A sentence carries its own meaning. A table row depends on our
# having paired it with the right headers. A figure label was matched to its
# number by position on the page, which is a guess.
SourceKind = Literal["prose", "table_row", "footnote", "heading", "figure_label"]


class ExtractedFact(BaseModel):
    """
    One claim, as the model reports it, before any of our own processing.

    This is the raw output shape. Entity resolution, number parsing and date
    parsing all happen later — nothing here has been interpreted by us yet,
    which is what makes it possible to check our interpretation afterwards.
    """

    chunk_id: str = Field(
        description="The id of the passage this fact came from, copied exactly."
    )

    # --- what the claim is about ------------------------------------------

    subject: Optional[str] = Field(
        default=None,
        description=(
            "The named thing the claim is about, exactly as the passage names "
            "it. Use null when the passage is plainly about the document's own "
            "subject without naming it — as a page of highlights usually is."
        ),
    )
    attribute: str = Field(
        description="The property being claimed, in the passage's own words."
    )

    # --- the value --------------------------------------------------------

    value_raw: str = Field(
        description=(
            "The value exactly as written, keeping commas, symbols and any "
            "greater-than sign. Never tidied or converted."
        )
    )
    value_kind: ValueKind
    unit: Optional[str] = None
    currency: Optional[str] = None

    # --- the qualifiers, copied as written --------------------------------

    period_text: Optional[str] = Field(
        default=None,
        description="The span of time the claim covers, copied as written.",
    )
    as_of_text: Optional[str] = Field(
        default=None,
        description="The single date at which the claim holds, copied as written.",
    )
    scope: Optional[str] = Field(
        default=None,
        description="Any stated qualification limiting what the value covers.",
    )

    # --- the proof --------------------------------------------------------

    evidence: str = Field(
        description=(
            "The exact text from the passage that states this fact, copied "
            "character for character."
        )
    )
    source_kind: SourceKind

    # ----------------------------------------------------------------------

    @field_validator("subject", "attribute", "value_raw", "evidence", mode="before")
    @classmethod
    def tidy_the_edges_only(cls, value):
        """
        Strip surrounding whitespace, and turn empty strings into None.

        Deliberately does NOT tidy the inside of the text. `evidence` has to
        survive an exact-match search against the source in Step 6, so
        collapsing its spaces here would break the very check it exists for.

        Models often return "" where they mean "not stated". Treating that as
        None keeps one idea from having two spellings.
        """
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value

    @field_validator("unit", "currency", "period_text", "as_of_text", "scope", mode="before")
    @classmethod
    def treat_empty_and_none_words_as_missing(cls, value):
        """
        Normalise the many ways a model says "nothing here" into None.

        A qualifier that is missing must be genuinely missing. If one fact says
        `period_text = "null"` and another says None, they would be compared as
        different periods, and a contradiction would be invented out of two
        spellings of nothing.
        """
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.lower() in {"", "null", "none", "n/a", "na", "not stated", "unknown"}:
            return None
        return stripped


class FactsFromOneRequest(BaseModel):
    """
    Everything the model returned for one request.

    One request carries several passages, so this is a flat list and each fact
    names its own passage. See the note on batching in step_03_extract_facts.
    """

    facts: list[ExtractedFact]
