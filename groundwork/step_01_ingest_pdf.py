"""
STEP 1 — turn a PDF into pages of clean, ordered text.

Run it with:
    .\\.venv\\Scripts\\python.exe -m groundwork.step_01_ingest_pdf

What this step is responsible for, and what it deliberately is not:
    IT DOES     read the PDF, work out what belongs with what on each page,
                pull footnote bodies out, flatten tables, throw away page
                furniture, and save one clean text string per page.
    IT DOES NOT decide what a fact is. That is Step 3's job.

---------------------------------------------------------------------------
WHY THIS FILE WORKS ON LINES INSTEAD OF BLOCKS
---------------------------------------------------------------------------
PyMuPDF can hand back page text at four levels of detail:

    blocks  ->  lines  ->  spans  ->  words
    (coarsest)                        (finest)

"blocks" is the obvious choice and it is wrong for our documents. On the
annual report's infographic page it merged text across columns and produced:

    "Count of 46-ft tractors 98,135 (1,5)"

which glues the label belonging to the value 753 onto the value 98,135, whose
real label is "Workforce strength". Every fact from that page would have been
wrong, and nothing would have crashed to tell us.

At "lines" level the same page is clean — no line crosses a column. So we take
lines and do the grouping ourselves, using two rules a human eye applies
without thinking:

    two pieces of text belong together when they sit in the same column,
    and when the vertical gap between them is small

The whole story is in docs/04_FAILURES.md, entry 5.

---------------------------------------------------------------------------
WHY EVERYTHING IS DECIDED BY POSITION AND FONT SIZE, NEVER BY WORDS
---------------------------------------------------------------------------
It would be easy to strip the running header by matching the company name, or
to find footnotes by looking for a phrase we noticed. The task brief forbids
document-specific rules and we will be tested on unseen PDFs, so every rule
here is about how text LOOKS:

    smallest text pressed against the top or bottom edge -> page furniture
    largest text near the top of the page                -> section heading
    small text low on the page starting with "(1)"       -> a footnote body

None of that mentions a company, a document, or a subject.
"""

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pymupdf

from groundwork.shared import config, database


# ===========================================================================
# Tuning numbers
#
# Every one of these is a judgement call. They are gathered here, named, and
# explained, so that tuning them later is a five-second job instead of a hunt
# through the code for a mystery number.
# ===========================================================================

# --- deciding what is page furniture -----------------------------------------

# Running headers and page numbers are set in the smallest type on the page.
# Real footnotes sit at 8pt in our documents, and real body text well above
# that, so 9 leaves room without swallowing content.
PAGE_FURNITURE_LARGEST_FONT_SIZE = 9.0

# ...and they are pressed against the very top or very bottom edge. Footnotes
# live higher up than this, which is what keeps them safe.
PAGE_FURNITURE_MARGIN_AS_FRACTION_OF_HEIGHT = 0.06

# --- finding footnote bodies -------------------------------------------------

# Footnote bodies sit in the lower part of the page. Measured on our documents
# they begin around 82% of the way down; 0.70 gives margin without reaching up
# into body text.
FOOTNOTE_REGION_BEGINS_AT_FRACTION_OF_HEIGHT = 0.70

# A footnote that runs onto a second line has no marker of its own, so we have
# to recognise the continuation some other way. Two conditions, both required:
# it is set in the same size as the footnote it continues, and it begins at
# roughly the same left edge.
#
# Both exist because of a real failure. On the presentation's balance-sheet
# page, footnote 4 absorbed "Total liabilities 2,036 2,308 ... 18" — table rows
# and the page number — because anything in the lower part of the page that did
# not start a new footnote was assumed to continue the previous one. Those rows
# are set in a different size and start in a different place, so both checks
# reject them.
FOOTNOTE_CONTINUATION_FONT_SIZE_TOLERANCE = 1.5
FOOTNOTE_CONTINUATION_LEFT_EDGE_TOLERANCE = 60.0

# --- grouping lines into visual blocks ---------------------------------------

# Two lines count as being in the same column when their horizontal ranges
# overlap, or when they very nearly touch. The tolerance matters because a
# superscript footnote marker sits just to the right of its number with a gap
# of about one point, and we want it kept with that number.
SAME_COLUMN_HORIZONTAL_GAP_TOLERANCE = 12.0

# When two ranges do overlap, a token overlap of a few points is not enough.
# They must share at least this much of the narrower range.
#
# Without this, one superscript bridged two separate tiles: the marker "(3,4)"
# sat at x187-227, touching ">33,200" (ending at x186) on its left and
# overlapping "18,793" (starting at x221) by six points on its right. Six
# points of overlap was enough to weld two unrelated columns into one block.
SMALLEST_MEANINGFUL_OVERLAP_AS_FRACTION_OF_NARROWER_RANGE = 0.25

# Only a genuinely small fragment — a superscript marker, a currency symbol —
# is allowed to join something it merely touches without overlapping. Anything
# wider than this has to earn its place with real overlap.
WIDEST_A_FRAGMENT_MAY_BE_AND_STILL_JOIN_BY_TOUCHING = 45.0

# Two stacked lines belong to the same block when the gap between them is no
# bigger than the shorter line's own height, plus a little slack.
SAME_BLOCK_VERTICAL_GAP_SLACK = 6.0

# --- working out reading order -----------------------------------------------

# A vertical strip of the page with no text in it at all, this wide or wider,
# is treated as a gutter between columns.
COLUMN_GUTTER_MINIMUM_FRACTION_OF_WIDTH = 0.04

# Above this many detected columns we are almost certainly looking at a poster
# or infographic rather than columns of prose, so we fall back to plain
# top-to-bottom order.
MOST_COLUMNS_A_PAGE_CAN_REALLY_HAVE = 4

# --- section headings ---------------------------------------------------------

# A heading has to be meaningfully bigger than the page's ordinary text.
HEADING_MUST_BE_THIS_MUCH_BIGGER_THAN_BODY_TEXT = 1.4

# ...and it has to be near the top.
HEADING_MUST_BE_WITHIN_THIS_FRACTION_OF_HEIGHT = 0.35

# --- tables --------------------------------------------------------------------

SMALLEST_TABLE_WORTH_KEEPING_ROWS = 2
SMALLEST_TABLE_WORTH_KEEPING_COLUMNS = 2

# --- how the assembled page text is laid out -----------------------------------

# These markers separate the three parts of a page's text. They are stored in
# the text itself, so character offsets recorded later stay meaningful.
TABLE_SECTION_MARKER = "[TABLES ON THIS PAGE]"
FOOTNOTE_SECTION_MARKER = "[FOOTNOTES ON THIS PAGE]"


# ===========================================================================
# Patterns
# ===========================================================================

# A footnote BODY: a line that begins with its own marker.
#   "(1) As of March 31, 2024"      "1. Includes..."      "* Excludes..."
FOOTNOTE_BODY_PATTERN = re.compile(
    r"^\s*(?:\(\s*(?P<bracketed>\d{1,2})\s*\)"
    r"|\[\s*(?P<squared>\d{1,2})\s*\]"
    r"|(?P<plain>\d{1,2})[.)]"
    r"|(?P<symbol>[*†‡§]))"
    r"\s+(?P<body>\S.*)$"
)

# A footnote MARKER inside running text: "98,135 (1,5)" or "revenue(2)"
FOOTNOTE_MARKER_PATTERN = re.compile(r"[\(\[]\s*(\d{1,2}(?:\s*,\s*\d{1,2})*)\s*[\)\]]")

# Invisible control characters that PDFs sometimes carry. We found a BEL
# character (\x07) sitting at the front of a footnote body in the annual
# report. They are not whitespace, so ordinary text tidying leaves them in
# place, where they would later break an exact-match grounding check for no
# visible reason.
CONTROL_CHARACTERS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ===========================================================================
# The shapes we pass around
# ===========================================================================

@dataclass
class TextLine:
    """One line of text on a page, with where it sits and how big it is."""
    text: str
    left: float
    top: float
    right: float
    bottom: float
    font_size: float

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def width(self) -> float:
        return self.right - self.left


@dataclass
class VisualBlock:
    """
    A group of lines that belong together — one tile, one paragraph, one label
    with its number. This is what "blocks" mode was supposed to give us and got
    wrong.
    """
    lines: list[TextLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def left(self) -> float:
        return min(line.left for line in self.lines)

    @property
    def right(self) -> float:
        return max(line.right for line in self.lines)

    @property
    def top(self) -> float:
        return min(line.top for line in self.lines)

    @property
    def bottom(self) -> float:
        return max(line.bottom for line in self.lines)


@dataclass
class IngestedPage:
    """Everything we worked out about one page."""
    page_number: int
    text: str
    footnotes: dict[str, str]
    section_heading: str
    how_many_visual_blocks: int
    how_many_table_rows: int
    how_many_furniture_lines_removed: int
    how_many_lines_a_table_replaced: int


# ===========================================================================
# 1. Identifying the file
# ===========================================================================

def compute_file_fingerprint(pdf_path: Path) -> str:
    """
    Return a SHA-256 fingerprint of the file's bytes.

    Two files with the same fingerprint are byte-for-byte identical, whatever
    they are called. Stored as a UNIQUE column, this gives us upload
    idempotency for free: re-uploading a document we already processed is
    rejected by the database instead of being processed twice.
    """
    hasher = hashlib.sha256()
    with open(pdf_path, "rb") as file_being_read:
        # Read in pieces rather than all at once, so a very large PDF does not
        # have to fit in memory.
        for chunk_of_bytes in iter(lambda: file_being_read.read(65536), b""):
            hasher.update(chunk_of_bytes)
    return hasher.hexdigest()


def make_document_id(pdf_path: Path, file_fingerprint: str) -> str:
    """
    Build a readable, stable id for a document.

    Readable matters more than it sounds: you will be reading these ids in
    query results and error messages for the rest of the project, and
    "doc_02_delhivery_annual_report_a1b2c3d4" tells you something that a bare
    hash does not. The hash on the end keeps it unique.
    """
    name_without_extension = pdf_path.stem.lower()
    safe_name = re.sub(r"[^a-z0-9]+", "_", name_without_extension).strip("_")
    return f"doc_{safe_name[:48]}_{file_fingerprint[:8]}"


# ===========================================================================
# 2. Reading a page into lines
# ===========================================================================

def extract_lines_from_page(page) -> list[TextLine]:
    """
    Pull every line of text off a page, with its position and font size.

    We ask for "dict" rather than "blocks" because blocks merges across
    columns. See the note at the top of this file.
    """
    page_dictionary = page.get_text("dict")
    lines_on_this_page = []

    for block in page_dictionary["blocks"]:
        # Blocks of type 1 are images. They have no text to read.
        if block.get("type") != 0:
            continue

        for line in block["lines"]:
            spans_in_this_line = line["spans"]
            if not spans_in_this_line:
                continue

            joined_text = "".join(span["text"] for span in spans_in_this_line)
            text_without_control_characters = CONTROL_CHARACTERS_PATTERN.sub("", joined_text)
            tidied_text = " ".join(text_without_control_characters.split())
            if not tidied_text:
                continue

            left, top, right, bottom = line["bbox"]

            # A line can mix font sizes. The largest one describes what the
            # line is — a big number with a small superscript is a big number.
            largest_font_size_in_line = max(span["size"] for span in spans_in_this_line)

            lines_on_this_page.append(
                TextLine(
                    text=tidied_text,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    font_size=largest_font_size_in_line,
                )
            )

    return lines_on_this_page


def line_is_page_furniture(line: TextLine, page_height: float) -> bool:
    """
    True for running headers, page numbers, and the like.

    Two conditions, both required: the text is in the smallest type on the
    page, AND it is pressed against the top or bottom edge. Requiring both is
    what keeps real footnotes — small, but not in the margin — from being
    thrown away.

    Note what this does NOT do: match any words. A rule like "drop lines
    containing the company name" would work on these three PDFs and fail on
    the next one.
    """
    if line.font_size > PAGE_FURNITURE_LARGEST_FONT_SIZE:
        return False

    margin_size = page_height * PAGE_FURNITURE_MARGIN_AS_FRACTION_OF_HEIGHT
    is_in_the_top_margin = line.bottom < margin_size
    is_in_the_bottom_margin = line.top > (page_height - margin_size)

    return is_in_the_top_margin or is_in_the_bottom_margin


# ===========================================================================
# 3. Finding footnote bodies
# ===========================================================================

def find_footnote_bodies(lines: list[TextLine], page_height: float) -> tuple[dict[str, str], set[int]]:
    """
    Find the footnote definitions at the bottom of a page.

    Returns the marker-to-text mapping, and the positions of the lines that
    were used, so the caller can take them out of the main flow of the page
    instead of leaving them dangling mid-sentence.

    A footnote body has to satisfy two things: it is low on the page, and it
    starts with its own marker. Position alone would catch ordinary last
    paragraphs; the marker alone would catch numbered lists anywhere.

    Continuation lines are handled too. In our annual report, footnote 4 runs
    onto a second line beginning "once during such period." with no marker of
    its own. A line counts as continuing the previous footnote only if it is
    set in the same size and starts at roughly the same left edge — see the
    note beside those two tolerances for the failure that made them necessary.

    THE CALLER MUST PASS LINES SORTED TOP TO BOTTOM. This is not a style
    preference. PyMuPDF returns lines in the PDF's internal order, which on the
    presentation's balance-sheet page put table rows sitting ABOVE a footnote
    after it in the list — so they were read as continuing a footnote they
    physically precede.
    """
    footnote_region_starts_at = page_height * FOOTNOTE_REGION_BEGINS_AT_FRACTION_OF_HEIGHT

    footnotes_found: dict[str, str] = {}
    line_positions_used: set[int] = set()

    marker_of_the_footnote_being_read = None
    first_line_of_that_footnote: TextLine | None = None

    for line_position, line in enumerate(lines):
        if line.top < footnote_region_starts_at:
            continue

        match = FOOTNOTE_BODY_PATTERN.match(line.text)
        if match:
            marker = (
                match.group("bracketed")
                or match.group("squared")
                or match.group("plain")
                or match.group("symbol")
            )
            footnotes_found[marker] = match.group("body").strip()
            line_positions_used.add(line_position)
            marker_of_the_footnote_being_read = marker
            first_line_of_that_footnote = line
            continue

        if marker_of_the_footnote_being_read is None:
            continue

        difference_in_font_size = abs(line.font_size - first_line_of_that_footnote.font_size)
        difference_in_left_edge = abs(line.left - first_line_of_that_footnote.left)

        looks_like_more_of_the_same_footnote = (
            difference_in_font_size <= FOOTNOTE_CONTINUATION_FONT_SIZE_TOLERANCE
            and difference_in_left_edge <= FOOTNOTE_CONTINUATION_LEFT_EDGE_TOLERANCE
        )
        if not looks_like_more_of_the_same_footnote:
            # Something else lives down here — a table row, a page number.
            # Stop extending, and do not let anything below it rejoin either.
            marker_of_the_footnote_being_read = None
            first_line_of_that_footnote = None
            continue

        footnotes_found[marker_of_the_footnote_being_read] += " " + line.text
        line_positions_used.add(line_position)

    return footnotes_found, line_positions_used


def find_footnote_markers_in_text(some_text: str) -> list[str]:
    """
    List the footnote markers referred to in a piece of text.

    "98,135 (1,5)" gives ["1", "5"]. Step 2 uses this to decide which footnote
    bodies to paste onto a chunk, so that a number never travels without the
    rules for reading it.
    """
    markers_found = []
    for match in FOOTNOTE_MARKER_PATTERN.finditer(some_text):
        group_of_markers = match.group(1)
        for single_marker in group_of_markers.split(","):
            cleaned_marker = single_marker.strip()
            if cleaned_marker and cleaned_marker not in markers_found:
                markers_found.append(cleaned_marker)
    return markers_found


# ===========================================================================
# 4. Grouping lines into visual blocks
# ===========================================================================

def horizontal_ranges_belong_to_the_same_column(
    left_a: float, right_a: float,
    left_b: float, right_b: float,
) -> bool:
    """
    True when two horizontal ranges sit in the same column of the page.

    There are two ways to qualify, and they exist for two different reasons.

    THE NORMAL WAY — real overlap. The ranges must share at least a quarter of
    the narrower one. A token few points of overlap does not count: a
    superscript marker once overlapped a neighbouring column by six points and
    welded two unrelated tiles into a single block.

    THE EXCEPTION — a small fragment that merely touches. A superscript
    footnote marker sits about one point to the right of its number and shares
    no horizontal space with it at all, so pure overlap would orphan it. Only
    genuinely narrow fragments get this exception; anything wider has to earn
    its place with real overlap.
    """
    width_of_a = right_a - left_a
    width_of_b = right_b - left_b
    narrower_width = max(1.0, min(width_of_a, width_of_b))

    overlap_left_edge = max(left_a, left_b)
    overlap_right_edge = min(right_a, right_b)

    # Positive means they share space. Negative means there is a gap between
    # them, and how negative it is IS the size of that gap.
    width_of_overlap = overlap_right_edge - overlap_left_edge

    # THE EXCEPTION, checked first.
    #
    # A narrow fragment attaches to whatever it sits beside, as long as it is
    # not far away. Whether it touches, misses by a point, or clips the edge by
    # a point or two is an accident of typesetting, not a statement about which
    # column it belongs to. Checking the fraction first got this wrong: the
    # marker "(1)" beside the number "111" overlapped it by two points out of
    # twenty-seven, failed the fraction test, and was orphaned.
    if narrower_width <= WIDEST_A_FRAGMENT_MAY_BE_AND_STILL_JOIN_BY_TOUCHING:
        distance_apart = max(0.0, -width_of_overlap)
        return distance_apart <= SAME_COLUMN_HORIZONTAL_GAP_TOLERANCE

    # THE NORMAL RULE.
    #
    # Both pieces are substantial, so they have to genuinely share space. A
    # token few points of overlap does not count — that is how a superscript
    # once welded two unrelated tiles into one block.
    if width_of_overlap <= 0:
        return False

    share_of_the_narrower_range = width_of_overlap / narrower_width
    return (
        share_of_the_narrower_range
        >= SMALLEST_MEANINGFUL_OVERLAP_AS_FRACTION_OF_NARROWER_RANGE
    )


def line_shares_a_column_with_block(block: VisualBlock, line: TextLine) -> bool:
    """
    True when a line sits in the same vertical strip as a whole block.

    IMPORTANT: this compares against the block's FULL horizontal extent, not
    against its last line. That distinction caused a real bug.

    On the annual report's infographic, a tile is three lines:

        98,135              x 1002-1111   <- the value
        (1,5)               x 1112-1142   <- superscript, narrow, off to the right
        Workforce strength  x 1002-1094   <- the label

    Comparing "Workforce strength" against the block's LAST line compares it
    against the narrow marker at 1112-1142. They do not touch, so the label was
    pushed into a block of its own and the number lost its meaning. Comparing
    against the block's full range, 1002-1142, they overlap and stay together.
    """
    return horizontal_ranges_belong_to_the_same_column(
        block.left, block.right, line.left, line.right
    )


def line_is_vertically_close_to_block(block: VisualBlock, line: TextLine) -> bool:
    """
    True when a line sits close enough under a block to belong to it.

    The allowance is based on the SHORTEST line involved, never the tallest.
    That detail matters: a 52-point headline number above an 11-point label
    would, if we measured by the tall line, happily swallow the next tile 49
    points below it. Measuring by the shortest line keeps tile boundaries
    honest.
    """
    vertical_gap = line.top - block.bottom

    # Overlapping or touching is always close enough.
    if vertical_gap <= 0:
        return True

    shortest_line_in_the_block = min(
        line_in_block.height for line_in_block in block.lines
    )
    largest_gap_allowed = (
        min(shortest_line_in_the_block, line.height) + SAME_BLOCK_VERTICAL_GAP_SLACK
    )
    return vertical_gap <= largest_gap_allowed


def group_lines_into_visual_blocks(lines: list[TextLine]) -> list[VisualBlock]:
    """
    Put lines that visually belong together into the same block.

    This is the function that replaces the library's broken grouping.

    We walk the lines from top to bottom. Each line joins whichever open block
    it sits most closely beneath, provided they share a column and are
    vertically close. If nothing fits, it starts a block of its own.
    """
    lines_from_top_to_bottom = sorted(lines, key=lambda line: (line.top, line.left))

    blocks_being_built: list[VisualBlock] = []

    for line in lines_from_top_to_bottom:
        best_block_to_join = None
        smallest_vertical_gap_found = None

        for candidate_block in blocks_being_built:
            if not line_shares_a_column_with_block(candidate_block, line):
                continue
            if not line_is_vertically_close_to_block(candidate_block, line):
                continue

            vertical_gap = line.top - candidate_block.bottom
            if smallest_vertical_gap_found is None or vertical_gap < smallest_vertical_gap_found:
                smallest_vertical_gap_found = vertical_gap
                best_block_to_join = candidate_block

        if best_block_to_join is not None:
            best_block_to_join.lines.append(line)
        else:
            blocks_being_built.append(VisualBlock(lines=[line]))

    # Within a block, read top to bottom. Lines were appended in the order we
    # happened to meet them, which for a superscript sitting slightly above its
    # own number is not the order a person would read them in.
    for block in blocks_being_built:
        block.lines.sort(key=lambda line: (line.top, line.left))

    return blocks_being_built


# ===========================================================================
# 5. Reading order
# ===========================================================================

def find_column_gutters(blocks: list[VisualBlock], page_width: float) -> list[float]:
    """
    Find the empty vertical strips that separate columns of text.

    How it works: imagine shining a light down the page and marking every
    horizontal position where some text casts a shadow. The unlit strips are
    gutters. A strip wide enough to be a real gutter tells us where one column
    ends and the next begins.
    """
    number_of_slices = 200
    slice_width = page_width / number_of_slices
    slice_has_text_in_it = [False] * number_of_slices

    for block in blocks:
        first_slice = max(0, int(block.left / slice_width))
        last_slice = min(number_of_slices - 1, int(block.right / slice_width))
        for slice_index in range(first_slice, last_slice + 1):
            slice_has_text_in_it[slice_index] = True

    smallest_gutter_in_slices = int(
        number_of_slices * COLUMN_GUTTER_MINIMUM_FRACTION_OF_WIDTH
    )

    gutter_positions = []
    run_of_empty_slices = 0

    for slice_index, has_text in enumerate(slice_has_text_in_it):
        if not has_text:
            run_of_empty_slices += 1
            continue

        # We just hit text again, so an empty run ended here.
        if run_of_empty_slices >= smallest_gutter_in_slices:
            # Ignore the empty margin before the first text on the page.
            gutter_started_at_the_page_edge = (slice_index - run_of_empty_slices) == 0
            if not gutter_started_at_the_page_edge:
                gutter_positions.append(slice_index * slice_width)
        run_of_empty_slices = 0

    return gutter_positions


def sort_visual_blocks_into_reading_order(
    blocks: list[VisualBlock],
    page_width: float,
) -> list[VisualBlock]:
    """
    Put blocks in the order a person would read them.

    On a page laid out in columns, that means finishing one column before
    starting the next. On an ordinary page it means plain top-to-bottom.

    If we detect more columns than any real page of prose has, we are looking
    at a poster or an infographic rather than columns, and column order would
    be meaningless. In that case we fall back to top-to-bottom, which is at
    least predictable.
    """
    if not blocks:
        return []

    gutter_positions = find_column_gutters(blocks, page_width)

    number_of_columns = len(gutter_positions) + 1
    page_is_really_columns_of_prose = (
        1 < number_of_columns <= MOST_COLUMNS_A_PAGE_CAN_REALLY_HAVE
    )

    if not page_is_really_columns_of_prose:
        return sorted(blocks, key=lambda block: (block.top, block.left))

    def which_column_is_this_block_in(block: VisualBlock) -> int:
        column_index = 0
        for gutter_x in gutter_positions:
            if block.left >= gutter_x:
                column_index += 1
        return column_index

    return sorted(
        blocks,
        key=lambda block: (which_column_is_this_block_in(block), block.top, block.left),
    )


# ===========================================================================
# 6. Section heading
# ===========================================================================

def guess_section_heading(lines: list[TextLine]) -> str:
    """
    Guess what section of the document this page belongs to.

    A heading is the biggest text near the top of the page, and it has to be
    meaningfully bigger than the page's ordinary text — otherwise a page set
    entirely in one size would nominate its own first sentence.

    Step 2 pastes this onto every chunk from the page, so a passage carries
    its context even when read in isolation.
    """
    if not lines:
        return ""

    page_bottom = max(line.bottom for line in lines)
    if page_bottom <= 0:
        return ""

    font_sizes_on_this_page = sorted(line.font_size for line in lines)
    middle_position = len(font_sizes_on_this_page) // 2
    typical_body_font_size = font_sizes_on_this_page[middle_position]

    smallest_size_that_counts_as_a_heading = (
        typical_body_font_size * HEADING_MUST_BE_THIS_MUCH_BIGGER_THAN_BODY_TEXT
    )
    lowest_a_heading_can_be = page_bottom * HEADING_MUST_BE_WITHIN_THIS_FRACTION_OF_HEIGHT

    candidate_headings = [
        line for line in lines
        if line.font_size >= smallest_size_that_counts_as_a_heading
        and line.top <= lowest_a_heading_can_be
    ]
    if not candidate_headings:
        return ""

    # Biggest first; where two are the same size, the higher one wins.
    candidate_headings.sort(key=lambda line: (-line.font_size, line.top))
    return candidate_headings[0].text


# ===========================================================================
# 7. Tables
# ===========================================================================

def flatten_one_table(table_rows: list[list]) -> list[str]:
    """
    Rewrite a table as one readable line per row.

    Why this exists: plain text extraction on a table hands you a stream of
    loose numbers — "1240 980 620 415" — with no way to know which row or
    column any of them came from. Useless, and worse than useless if a model
    guesses.

    Flattened, each row keeps its own labels:

        Equity and Liabilities (Rs Cr) | Mar 23 | Mar 24
        Total equity | 9,177 | 9,145

    The header row stays directly above its data, so the column meanings
    travel with the numbers.
    """
    flattened_lines = []

    for single_row in table_rows:
        cell_texts = []
        for cell in single_row:
            if cell is None:
                cell_texts.append("")
                continue
            cell_texts.append(" ".join(str(cell).split()))

        # A row needs at least two filled cells to say anything: something
        # being described, and a value. One filled cell is a spacer, a heading,
        # or a stray piece of layout.
        #
        # This is also what stops a page's running header — which pdfplumber
        # happily reports as a two-column table — from being stored as data.
        how_many_cells_have_content = sum(1 for text in cell_texts if text)
        if how_many_cells_have_content < SMALLEST_TABLE_WORTH_KEEPING_COLUMNS:
            continue

        flattened_lines.append(" | ".join(cell_texts))

    return flattened_lines


def extract_and_flatten_tables(plumber_page, text_already_on_the_page: str) -> list[str]:
    """
    Pull the tables off a page and flatten them, skipping what we already have.

    The skipping matters. Some layouts are picked up both as ordinary text and
    as a table, and storing both would produce two copies of every fact on the
    page. Those duplicates would later be compared against each other and
    solemnly declared to corroborate, which is noise dressed up as a finding.
    """
    normalised_existing_text = " ".join(text_already_on_the_page.split()).lower()

    flattened_lines_to_keep = []
    tables_on_this_page = plumber_page.extract_tables()

    for table_number, table_rows in enumerate(tables_on_this_page, start=1):
        if len(table_rows) < SMALLEST_TABLE_WORTH_KEEPING_ROWS:
            continue
        if not table_rows[0] or len(table_rows[0]) < SMALLEST_TABLE_WORTH_KEEPING_COLUMNS:
            continue

        flattened_rows = flatten_one_table(table_rows)
        if not flattened_rows:
            continue

        rows_not_already_present = []
        for flattened_row in flattened_rows:
            row_content_only = " ".join(flattened_row.replace("|", " ").split()).lower()

            # Very short rows match almost anything, so judging them by
            # containment would throw away real data.
            if len(row_content_only) > 20 and row_content_only in normalised_existing_text:
                continue
            rows_not_already_present.append(flattened_row)

        # One surviving row is not a table. Requiring two is what finally
        # rejects the running header, whose second row has only a single
        # filled cell and is dropped above.
        if len(rows_not_already_present) < SMALLEST_TABLE_WORTH_KEEPING_ROWS:
            continue

        flattened_lines_to_keep.append(f"TABLE {table_number}")
        flattened_lines_to_keep.extend(rows_not_already_present)
        flattened_lines_to_keep.append("")

    return flattened_lines_to_keep


def collect_cell_texts_from_kept_tables(flattened_table_lines: list[str]) -> set[str]:
    """Gather every individual cell value from the tables we decided to keep."""
    cell_texts = set()
    for flattened_line in flattened_table_lines:
        if not flattened_line or flattened_line.startswith("TABLE "):
            continue
        for cell in flattened_line.split("|"):
            tidied_cell = " ".join(cell.split())
            if tidied_cell:
                cell_texts.add(tidied_cell.lower())
    return cell_texts


def remove_lines_already_covered_by_a_table(
    blocks: list[VisualBlock],
    cell_texts_from_tables: set[str],
) -> tuple[list[VisualBlock], int]:
    """
    Drop prose lines that a kept table already holds, and better.

    Why this is needed. On the presentation's balance-sheet page, the labels
    live in one column and the numbers in another, so line grouping produces
    one block of labels and separate blocks of loose numbers:

        Total equity           ...later...     9,177
        Borrowings                             114
        Lease liabilities                      534

    Nothing connects a label to its number, and "Borrowings" appears twice on
    that page — once under non-current liabilities, once under current. A model
    reading that has to guess, and a guess here produces a confident, wrong,
    fully-grounded fact.

    The table version of the same content has no such problem:

        Total equity | 9,177 | 9,145

    So where a kept table already contains a line, the prose copy carries
    strictly less information and is removed. The test is an exact match
    against a whole cell, not a substring, so a line is only dropped when the
    table demonstrably holds the same thing.

    Note the ordering with table extraction: rows already present in the prose
    were dropped from the tables first. Those rows are therefore not in
    `cell_texts_from_tables`, so this pass cannot remove the prose copy as
    well. Nothing falls through both sieves.
    """
    if not cell_texts_from_tables:
        return blocks, 0

    blocks_that_still_have_content = []
    how_many_lines_removed = 0

    for block in blocks:
        lines_worth_keeping = []
        for line in block.lines:
            if line.text.lower() in cell_texts_from_tables:
                how_many_lines_removed += 1
                continue
            lines_worth_keeping.append(line)

        if lines_worth_keeping:
            blocks_that_still_have_content.append(VisualBlock(lines=lines_worth_keeping))

    return blocks_that_still_have_content, how_many_lines_removed


# ===========================================================================
# 8. Putting one page together
# ===========================================================================

def build_page_text(
    blocks_in_reading_order: list[VisualBlock],
    flattened_table_lines: list[str],
    footnote_bodies: dict[str, str],
) -> str:
    """
    Assemble the single text string that represents this page.

    THIS STRING IS THE ONE SOURCE OF TRUTH FOR CHARACTER POSITIONS.

    Everything downstream — where a chunk starts, where a quoted piece of
    evidence sits, what the interface highlights when you click a fact —
    refers to positions inside this string. It is built once, stored, and
    never rebuilt. If it were ever regenerated differently, every stored
    position would quietly point at the wrong words and no error would appear.

    The page comes out in three parts, in this order:
        the prose, then the tables, then the footnotes.
    Footnotes go last because they are reference material, not part of the
    flow of reading. They stay inside this string, rather than only in the
    footnotes column, so that a fact quoting a footnote can still be traced
    to a real position on a real page.
    """
    sections = []

    prose_text = "\n\n".join(block.text for block in blocks_in_reading_order)
    if prose_text.strip():
        sections.append(prose_text)

    if flattened_table_lines:
        sections.append(TABLE_SECTION_MARKER + "\n" + "\n".join(flattened_table_lines).strip())

    if footnote_bodies:
        footnote_lines = [
            f"({marker}) {body}" for marker, body in sorted(footnote_bodies.items())
        ]
        sections.append(FOOTNOTE_SECTION_MARKER + "\n" + "\n".join(footnote_lines))

    return "\n\n".join(sections)


def check_every_block_survived_into_the_page_text(
    blocks: list[VisualBlock],
    assembled_page_text: str,
) -> list[str]:
    """
    Confirm each block's text really is present in the assembled page.

    This is one of the few inline checks in the project, and it is here for a
    specific reason: a bug in assembly does not crash. It produces a page that
    looks fine but is missing a tile, or has one mangled, and you would not
    find out until you clicked a fact in the interface and landed on the wrong
    sentence. Loud now beats silent later.

    Returns a list of complaints, empty when all is well.
    """
    complaints = []
    for block in blocks:
        for line in block.lines:
            if line.text not in assembled_page_text:
                complaints.append(
                    f"line missing from assembled page text: {line.text[:60]!r}"
                )
    return complaints


def ingest_one_page(page, plumber_page, page_number: int) -> tuple[IngestedPage, list[str]]:
    """Turn one page of a PDF into an IngestedPage. Returns it plus any complaints."""
    page_height = page.rect.height
    page_width = page.rect.width

    all_lines = extract_lines_from_page(page)

    # ORDER MATTERS HERE, and getting it wrong is subtle.
    #
    # Page furniture is removed BEFORE footnotes are read. The running footer
    # sits lower on the page than the last footnote, so when footnotes were
    # read first, the continuation rule cheerfully absorbed
    # "2 3 Delhivery Limited Annual Report 2023-24" onto the end of footnote 5.
    # The footnote then carried text the document never put there — and that
    # footnote is exactly what tells us the scope of a headline number.
    lines_without_page_furniture = []
    how_many_furniture_lines_removed = 0

    for line in all_lines:
        if line_is_page_furniture(line, page_height):
            how_many_furniture_lines_removed += 1
            continue
        lines_without_page_furniture.append(line)

    # Sorting top-to-bottom before looking for footnotes is required, not
    # tidiness. PyMuPDF returns lines in the PDF's internal order, which is not
    # reading order — on one page it listed table rows sitting ABOVE a footnote
    # after that footnote, and they were absorbed as its continuation.
    lines_without_page_furniture.sort(key=lambda line: (line.top, line.left))

    footnote_bodies, line_positions_used_by_footnotes = find_footnote_bodies(
        lines_without_page_furniture, page_height
    )

    lines_for_the_main_flow = [
        line
        for line_position, line in enumerate(lines_without_page_furniture)
        if line_position not in line_positions_used_by_footnotes
    ]

    visual_blocks = group_lines_into_visual_blocks(lines_for_the_main_flow)
    blocks_in_reading_order = sort_visual_blocks_into_reading_order(visual_blocks, page_width)

    section_heading = guess_section_heading(lines_for_the_main_flow)

    prose_text_so_far = "\n\n".join(block.text for block in blocks_in_reading_order)
    flattened_table_lines = extract_and_flatten_tables(plumber_page, prose_text_so_far)

    # Where a table now holds the same content, drop the prose copy — it has
    # labels and numbers pulled apart into different columns, and the table has
    # them joined up.
    cell_texts_from_tables = collect_cell_texts_from_kept_tables(flattened_table_lines)
    blocks_in_reading_order, how_many_lines_a_table_replaced = (
        remove_lines_already_covered_by_a_table(
            blocks_in_reading_order, cell_texts_from_tables
        )
    )

    assembled_page_text = build_page_text(
        blocks_in_reading_order, flattened_table_lines, footnote_bodies
    )

    complaints = check_every_block_survived_into_the_page_text(
        blocks_in_reading_order, assembled_page_text
    )

    how_many_table_rows = sum(
        1 for line in flattened_table_lines
        if line and not line.startswith("TABLE ")
    )

    ingested_page = IngestedPage(
        page_number=page_number,
        text=assembled_page_text,
        footnotes=footnote_bodies,
        section_heading=section_heading,
        how_many_visual_blocks=len(blocks_in_reading_order),
        how_many_table_rows=how_many_table_rows,
        how_many_furniture_lines_removed=how_many_furniture_lines_removed,
        how_many_lines_a_table_replaced=how_many_lines_a_table_replaced,
    )
    return ingested_page, complaints


# ===========================================================================
# 9. A whole document
# ===========================================================================

def ingest_one_pdf(pdf_path: Path, how_many_pages_at_most: int | None = None) -> dict:
    """
    Read a whole PDF and save its pages to the database.

    Returns a summary for printing. Pages are saved in one batched write at the
    end of the document rather than one page at a time, because the database is
    hosted and every write costs a round trip.
    """
    file_fingerprint = compute_file_fingerprint(pdf_path)
    document_id = make_document_id(pdf_path, file_fingerprint)

    opened_pdf = pymupdf.open(pdf_path)
    plumber_pdf = pdfplumber.open(pdf_path)

    total_pages_in_file = len(opened_pdf)
    pages_to_read = total_pages_in_file
    if how_many_pages_at_most is not None:
        pages_to_read = min(pages_to_read, how_many_pages_at_most)

    ingested_pages: list[IngestedPage] = []
    all_complaints: list[str] = []
    pages_that_failed: list[int] = []

    for page_index in range(pages_to_read):
        page_number = page_index + 1
        try:
            ingested_page, complaints = ingest_one_page(
                opened_pdf[page_index],
                plumber_pdf.pages[page_index],
                page_number,
            )
            ingested_pages.append(ingested_page)
            for complaint in complaints:
                all_complaints.append(f"page {page_number}: {complaint}")

        except Exception as page_problem:
            # One awkward page must never stop a 100-page document. Record it
            # and move on — the failures table is a deliverable, not a log.
            pages_that_failed.append(page_number)
            database.record_failure(
                stage="ingest",
                kind=type(page_problem).__name__,
                detail=f"{page_problem}",
                doc_id=document_id,
                page_no=page_number,
            )

    opened_pdf.close()
    plumber_pdf.close()

    # Re-ingesting a document REPLACES it completely. This is deliberate, not
    # laziness: the character positions inside pages.raw_text are the anchor
    # that every chunk and every piece of evidence downstream points at. If the
    # page text changes even slightly, everything built on top of it is stale
    # and must be rebuilt. ON DELETE CASCADE removes those dependents for us.
    database.execute_sql("DELETE FROM documents WHERE doc_id = %s", (document_id,))

    database.insert_rows_in_batches(
        "documents",
        [{
            "doc_id": document_id,
            "filename": pdf_path.name,
            "sha256": file_fingerprint,
            "page_count": total_pages_in_file,
            "status": "ingested",
        }],
    )

    page_rows = [
        {
            "doc_id": document_id,
            "page_no": ingested_page.page_number,
            "raw_text": ingested_page.text,
            "section_heading": ingested_page.section_heading,
            "footnotes": database.as_json(ingested_page.footnotes),
        }
        for ingested_page in ingested_pages
    ]
    database.insert_rows_in_batches("pages", page_rows)

    pages_with_almost_no_text = [
        ingested_page.page_number
        for ingested_page in ingested_pages
        if len(ingested_page.text.strip()) < 100
    ]

    return {
        "document_id": document_id,
        "filename": pdf_path.name,
        "pages_in_file": total_pages_in_file,
        "pages_read": len(ingested_pages),
        "pages_that_failed": pages_that_failed,
        "pages_with_almost_no_text": pages_with_almost_no_text,
        "total_characters": sum(len(p.text) for p in ingested_pages),
        "total_visual_blocks": sum(p.how_many_visual_blocks for p in ingested_pages),
        "total_table_rows": sum(p.how_many_table_rows for p in ingested_pages),
        "total_furniture_lines_removed": sum(
            p.how_many_furniture_lines_removed for p in ingested_pages
        ),
        "total_lines_a_table_replaced": sum(
            p.how_many_lines_a_table_replaced for p in ingested_pages
        ),
        "pages_with_footnotes": [
            p.page_number for p in ingested_pages if p.footnotes
        ],
        "total_footnotes": sum(len(p.footnotes) for p in ingested_pages),
        "complaints": all_complaints,
        "ingested_pages": ingested_pages,
    }


# ===========================================================================
# 10. Running it
# ===========================================================================

def print_summary_for_one_document(summary: dict) -> None:
    """Print what we got out of one PDF, in a form worth actually reading."""
    print(f"\n{summary['filename']}")
    print(f"  document id            : {summary['document_id']}")
    print(f"  pages read             : {summary['pages_read']} of {summary['pages_in_file']}")
    print(f"  characters of text     : {summary['total_characters']:,}")
    print(f"  visual blocks found    : {summary['total_visual_blocks']:,}")
    print(f"  table rows kept        : {summary['total_table_rows']:,}")
    print(f"  furniture lines removed: {summary['total_furniture_lines_removed']:,}")
    print(
        f"  prose lines a table replaced: "
        f"{summary['total_lines_a_table_replaced']:,}"
    )
    print(
        f"  footnotes found        : {summary['total_footnotes']} "
        f"across {len(summary['pages_with_footnotes'])} pages"
    )
    if summary["pages_with_footnotes"]:
        print(f"     first few pages     : {summary['pages_with_footnotes'][:12]}")

    if summary["pages_with_almost_no_text"]:
        print(
            f"  pages with no text     : {summary['pages_with_almost_no_text']}"
            "   <- images or blank"
        )
    if summary["pages_that_failed"]:
        print(f"  PAGES THAT FAILED      : {summary['pages_that_failed']}")
    if summary["complaints"]:
        print(f"  ASSEMBLY COMPLAINTS    : {len(summary['complaints'])}")
        for complaint in summary["complaints"][:5]:
            print(f"     {complaint}")


def print_one_page_so_you_can_check_it(summary: dict, page_number: int) -> None:
    """
    Print one page's assembled text in full.

    There is no test suite, so this is how we check the step really worked:
    you read this next to the actual PDF page and confirm they agree.
    """
    matching_pages = [
        page for page in summary["ingested_pages"] if page.page_number == page_number
    ]
    if not matching_pages:
        print(f"\n(page {page_number} was not read)")
        return

    page = matching_pages[0]
    print()
    print("=" * 78)
    print(f"FULL TEXT OF {summary['filename']} PAGE {page.page_number}")
    print(f"section heading detected: {page.section_heading!r}")
    print("=" * 78)
    print(page.text)
    print("-" * 78)
    print(f"footnotes captured: {page.footnotes}")


def main() -> int:
    command_line_arguments = sys.argv[1:]

    how_many_pages_at_most = None
    if "--max-pages" in command_line_arguments:
        position = command_line_arguments.index("--max-pages")
        how_many_pages_at_most = int(command_line_arguments[position + 1])

    print("=" * 78)
    print("STEP 1 — reading PDFs into pages")
    print("=" * 78)

    config.stop_unless_these_settings_are_filled_in(["DATABASE_URL"])

    pdf_paths = sorted(config.INPUT_PDF_FOLDER.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs found in {config.INPUT_PDF_FOLDER}")
        return 1

    print(f"\nFound {len(pdf_paths)} PDF(s) in {config.INPUT_PDF_FOLDER}")
    if how_many_pages_at_most:
        print(f"Reading at most {how_many_pages_at_most} pages from each.")

    all_summaries = []
    for pdf_path in pdf_paths:
        summary = ingest_one_pdf(pdf_path, how_many_pages_at_most)
        all_summaries.append(summary)
        print_summary_for_one_document(summary)

    # The infographic page is the one that broke the naive approach, so it is
    # the page most worth looking at by eye.
    for summary in all_summaries:
        if "annual-report" in summary["filename"]:
            print_one_page_so_you_can_check_it(summary, page_number=2)
            break

    print()
    print("=" * 78)
    print("WHAT IS NOW IN THE DATABASE")
    print("=" * 78)
    print(f"  documents : {database.count_rows_in_table('documents')}")
    print(f"  pages     : {database.count_rows_in_table('pages')}")
    print(f"  failures  : {database.count_rows_in_table('failures')}")

    total_complaints = sum(len(s["complaints"]) for s in all_summaries)
    total_failed_pages = sum(len(s["pages_that_failed"]) for s in all_summaries)

    print()
    if total_complaints == 0 and total_failed_pages == 0:
        print("SUCCESS — every page read, every block accounted for.")
        return 0

    print(f"FINISHED WITH PROBLEMS — {total_failed_pages} failed pages, "
          f"{total_complaints} assembly complaints. Read the output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
