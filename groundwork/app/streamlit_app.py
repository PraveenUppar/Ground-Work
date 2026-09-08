"""
Ground Work — the interface.

Run it with:
    .\\.venv\\Scripts\\streamlit.exe run groundwork\\app\\streamlit_app.py

---------------------------------------------------------------------------
WHAT THIS IS FOR
---------------------------------------------------------------------------
The task brief asks for a simple way to upload PDFs and inspect the results.
That is what this is — not a product, and deliberately not a graph
visualisation, because the brief says a graph alone is not the answer.

The thing worth looking at is not that facts exist. It is that every single
one can be traced back to a character range on a page, and that every verdict
comes with a written reason you can disagree with.

So every screen here is built around one idea: NOTHING IS SHOWN WITHOUT ITS
EVIDENCE. A fact always arrives with its quote and its page. A verdict always
arrives with both facts and the reasoning that produced it.
"""

import sys
import time
from pathlib import Path

import streamlit as st

PROJECT_ROOT_FOLDER = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT_FOLDER) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOLDER))

from groundwork import (
    step_01_ingest_pdf,
    step_02_build_chunks,
    step_03_extract_facts,
    step_04_check_grounding,
    step_05_normalize_facts,
    step_06_find_candidate_pairs,
    step_07_adjudicate_pairs,
)
from groundwork.shared import config, database


st.set_page_config(page_title="Ground Work", layout="wide")


# How each verdict is named and coloured. Plain words rather than symbols, so
# the screen reads as a sentence rather than a legend.
HOW_TO_SHOW_EACH_VERDICT = {
    "corroborates": ("AGREE", "green", "These say the same thing"),
    "contradicts": ("DISAGREE", "red", "These genuinely disagree"),
    "reconciled": ("EXPLAINED", "orange", "They differ, and context explains why"),
    "superseded": ("CHANGED", "blue", "A state that changed over time"),
    "unrelated": ("NOT COMPARABLE", "gray", "Not really about the same thing"),
    "undecided": ("NOT SETTLED", "gray", "Neither rule nor model settled it"),
    "candidate": ("NOT JUDGED", "gray", "Not yet compared"),
}


# One place for the small amount of styling this interface uses. Kept here
# rather than scattered inline so the look stays consistent between screens.
STYLES = """
<style>
.source-box {
    background: #f1f5f9;
    border-left: 4px solid #475569;
    border-radius: 4px;
    padding: 0.55rem 0.8rem;
    margin: 0.35rem 0 0.9rem 0;
    font-size: 0.82rem;
    line-height: 1.5;
    color: #1e293b;
}
.source-box .where { font-weight: 700; }
.source-box .trust { color: #475569; }
.page-view {
    white-space: pre-wrap;
    font-family: ui-monospace, Consolas, monospace;
    font-size: 0.78rem;
    line-height: 1.5;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: 0.8rem;
    max-height: 26rem;
    overflow-y: auto;
}
.stage-done    { color: #15803d; }
.stage-running { color: #b45309; font-weight: 700; }
.stage-waiting { color: #94a3b8; }
</style>
"""


# ===========================================================================
# Reading from the database
# ===========================================================================

@st.cache_data(ttl=30)
def load_the_overall_numbers() -> dict:
    counts = {}
    for table in ("documents", "pages", "chunks", "facts", "entities", "relations"):
        counts[table] = database.count_rows_in_table(table)
    grounding = database.fetch_one_row(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE grounded) AS grounded,
               count(*) FILTER (WHERE grounded AND value_in_evidence) AS checkable
        FROM facts
        """
    )
    counts.update(grounding or {})
    return counts


@st.cache_data(ttl=30)
def load_the_documents() -> list[dict]:
    return database.fetch_all_rows(
        """
        SELECT d.doc_id, d.filename, d.page_count,
               (SELECT count(*) FROM facts f WHERE f.doc_id = d.doc_id) AS facts,
               (SELECT count(*) FROM facts f WHERE f.doc_id = d.doc_id AND f.grounded)
                   AS grounded
        FROM documents d ORDER BY d.filename
        """
    )


@st.cache_data(ttl=30)
def load_the_entities(fewest_facts: int = 2) -> list[dict]:
    return database.fetch_all_rows(
        """
        SELECT e.entity_id, e.canonical_name, count(f.fact_id) AS facts
        FROM entities e LEFT JOIN facts f ON f.entity_id = e.entity_id
        GROUP BY e.entity_id, e.canonical_name
        HAVING count(f.fact_id) >= %s
        ORDER BY facts DESC
        """,
        (fewest_facts,),
    )


def search_the_facts(
    search_text: str, document_id: str | None, entity_id: str | None,
    only_grounded: bool, how_many: int,
) -> list[dict]:
    conditions = []
    parameters: list = []

    if only_grounded:
        conditions.append("f.grounded")
    if document_id:
        conditions.append("f.doc_id = %s")
        parameters.append(document_id)
    if entity_id:
        conditions.append("f.entity_id = %s")
        parameters.append(entity_id)
    if search_text:
        conditions.append(
            "(f.attribute_raw ILIKE %s OR f.subject_raw ILIKE %s "
            "OR f.evidence_text ILIKE %s OR f.value_raw ILIKE %s)"
        )
        parameters.extend([f"%{search_text}%"] * 4)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    parameters.append(how_many)

    return database.fetch_all_rows(
        f"""
        SELECT f.*, d.filename
        FROM facts f JOIN documents d USING (doc_id)
        {where}
        ORDER BY f.confidence DESC NULLS LAST, f.fact_id
        LIMIT %s
        """,
        tuple(parameters),
    )


FACT_COLUMNS_FOR_BOTH_SIDES = """
    {t}.fact_id AS {p}fact_id, {t}.subject_raw AS {p}subject_raw,
    {t}.attribute_raw AS {p}attribute_raw, {t}.value_raw AS {p}value_raw,
    {t}.unit AS {p}unit, {t}.currency AS {p}currency,
    {t}.period_raw AS {p}period_raw, {t}.as_of_raw AS {p}as_of_raw,
    {t}.scope_raw AS {p}scope_raw, {t}.evidence_text AS {p}evidence_text,
    {t}.page_no AS {p}page_no, {t}.char_start AS {p}char_start,
    {t}.char_end AS {p}char_end, {t}.source_kind AS {p}source_kind,
    {t}.confidence AS {p}confidence, {t}.doc_id AS {p}doc_id,
    d{t}.filename AS {p}filename
"""

RELATION_QUERY = f"""
    SELECT r.relation_id, r.verdict, r.method, r.explanation, r.bridging_fact_id,
           {FACT_COLUMNS_FOR_BOTH_SIDES.format(t='a', p='a_')},
           {FACT_COLUMNS_FOR_BOTH_SIDES.format(t='b', p='b_')}
    FROM relations r
    JOIN facts a ON a.fact_id = r.fact_a
    JOIN facts b ON b.fact_id = r.fact_b
    JOIN documents da ON da.doc_id = a.doc_id
    JOIN documents db ON db.doc_id = b.doc_id
    WHERE {{where}}
    ORDER BY {{order}}
    LIMIT {{limit}}
"""


def search_the_relations(
    verdicts: list[str], only_across_documents: bool,
    search_text: str, how_many: int,
) -> list[dict]:
    conditions = ["r.verdict = ANY(%s)"]
    parameters: list = [verdicts]

    if only_across_documents:
        conditions.append("a.doc_id <> b.doc_id")
    if search_text:
        conditions.append("(a.attribute_raw ILIKE %s OR b.attribute_raw ILIKE %s)")
        parameters.extend([f"%{search_text}%"] * 2)

    parameters.append(how_many)
    return database.fetch_all_rows(
        RELATION_QUERY.format(
            where=" AND ".join(conditions),
            order="(a.confidence + b.confidence) DESC NULLS LAST",
            limit="%s",
        ),
        tuple(parameters),
    )


# ===========================================================================
# Showing a fact, always with its evidence
# ===========================================================================

def describe_a_value(fact: dict) -> str:
    value = fact["value_raw"] or ""
    if fact.get("unit"):
        value += f" {fact['unit']}"
    if fact.get("currency"):
        value = f"{fact['currency']} {value}"
    return value


def show_the_qualifiers(fact: dict) -> None:
    """
    Show period, as-at and scope.

    Shown even when empty, deliberately. A missing qualifier is information —
    it is often why two figures could not be told apart, and hiding the gap
    would make the system look more certain than it is.
    """
    bits = []
    if fact.get("period_raw"):
        bits.append(f"**period** {fact['period_raw']}")
    if fact.get("as_of_raw"):
        bits.append(f"**as at** {fact['as_of_raw']}")
    if fact.get("scope_raw"):
        bits.append(f"**scope** {fact['scope_raw']}")
    st.markdown(" &nbsp;·&nbsp; ".join(bits) if bits else "_no qualifiers stated_")


def show_the_evidence(fact: dict) -> None:
    """
    Show the exact quote, then where it lives, prominently.

    The source line is the whole argument of this project — a fact is only
    worth anything because it can be traced to a character range on a page —
    so it is set apart rather than tucked into small grey caption text.
    """
    st.markdown(f"> {fact['evidence_text'] or '(no evidence)'}")

    confidence = fact.get("confidence") or 0
    st.markdown(
        f"<div class='source-box'>"
        f"<span class='where'>{fact.get('filename', 'unknown file')}"
        f" &nbsp;·&nbsp; page {fact['page_no']}"
        f" &nbsp;·&nbsp; characters {fact['char_start']}–{fact['char_end']}</span><br>"
        f"<span class='trust'>source type: {fact.get('source_kind', 'unknown')}"
        f" &nbsp;·&nbsp; confidence {confidence:.2f}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


def show_the_surrounding_page_text(fact: dict) -> None:
    """
    Show the source page with the quoted evidence marked inside it.

    Character offsets have been carried from stage one to here precisely so
    this can work. Seeing the quote sitting in its own paragraph is what turns
    "the system says so" into something a person can check in five seconds.
    """
    page = database.fetch_one_row(
        "SELECT raw_text FROM pages WHERE doc_id = %s AND page_no = %s",
        (fact["doc_id"], fact["page_no"]),
    )
    if not page or fact["char_start"] is None:
        st.info("No stored page text for this fact.")
        return

    text = page["raw_text"] or ""
    start, end = fact["char_start"], fact["char_end"]
    window_start = max(0, start - 500)
    window_end = min(len(text), end + 500)

    st.markdown("**The source page, with the quoted evidence marked**")
    st.markdown(
        f"<div class='page-view'>"
        f"{'…' if window_start else ''}{text[window_start:start]}"
        f"<mark style='background:#fde047'><b>{text[start:end]}</b></mark>"
        f"{text[end:window_end]}{'…' if window_end < len(text) else ''}</div>",
        unsafe_allow_html=True,
    )


def show_one_fact(fact: dict, heading: str | None = None) -> None:
    if heading:
        st.markdown(f"**{heading}**")
    subject = fact.get("subject_raw") or "_(the document's own subject)_"
    st.markdown(f"{subject} — **{fact['attribute_raw']}** = `{describe_a_value(fact)}`")
    show_the_qualifiers(fact)
    show_the_evidence(fact)


def show_one_relation(relation: dict, index: int) -> None:
    """One adjudicated pair: both facts, the verdict, and the reasoning."""
    label, colour, plain_english = HOW_TO_SHOW_EACH_VERDICT.get(
        relation["verdict"], ("?", "gray", relation["verdict"])
    )
    across = relation["a_doc_id"] != relation["b_doc_id"]

    header = (
        f"{label}  —  {relation['a_attribute_raw'][:44]}  ·  "
        f"{relation['a_value_raw']} against {relation['b_value_raw']}"
        + ("   ·   across two documents" if across else "")
    )

    with st.expander(header, expanded=(index < 2)):
        left, right = st.columns(2)
        with left:
            show_one_fact({k[2:]: v for k, v in relation.items() if k.startswith("a_")},
                          "FACT A")
        with right:
            show_one_fact({k[2:]: v for k, v in relation.items() if k.startswith("b_")},
                          "FACT B")

        st.markdown("---")
        st.markdown(
            f":{colour}[**{label}** — {plain_english}]"
            f" &nbsp;·&nbsp; decided by `{relation['method']}`"
        )
        st.markdown(relation["explanation"] or "_no explanation recorded_")

        if relation["bridging_fact_id"]:
            bridging = database.fetch_one_row(
                """
                SELECT f.*, d.filename FROM facts f JOIN documents d USING (doc_id)
                WHERE f.fact_id = %s
                """,
                (relation["bridging_fact_id"],),
            )
            if bridging:
                st.success("Reconciled by a third fact accounting for exactly the gap:")
                show_one_fact(bridging, "THE BRIDGING FACT")

        if st.checkbox("Show both source pages", key=f"src_{relation['relation_id']}"):
            for prefix, side in (("a_", "Fact A"), ("b_", "Fact B")):
                st.markdown(f"**{side}**")
                show_the_surrounding_page_text(
                    {k[2:]: v for k, v in relation.items() if k.startswith(prefix)}
                )


# ===========================================================================
# The pages
# ===========================================================================

def page_overview() -> None:
    st.title("Ground Work")
    st.markdown(
        "A fact knowledge layer. It reads PDFs, pulls out the claims inside "
        "them, **proves each claim against the source text**, and then compares "
        "claims to say whether they agree, disagree, or only look like they "
        "disagree."
    )

    numbers = load_the_overall_numbers()
    if not numbers.get("total"):
        st.warning("No facts yet. Upload a PDF, or run the pipeline scripts.")
        return

    a, b, c, d = st.columns(4)
    a.metric("Documents", f"{numbers['documents']:,}")
    b.metric("Pages read", f"{numbers['pages']:,}")
    c.metric("Facts extracted", f"{numbers['total']:,}")
    d.metric("Comparisons made", f"{numbers['relations']:,}")

    st.markdown("### How much of this is proved?")
    grounded_share = numbers["grounded"] / max(1, numbers["total"])
    st.progress(grounded_share)
    st.markdown(
        f"**{numbers['grounded']:,} of {numbers['total']:,} facts "
        f"({grounded_share:.1%})** carry a quote that was found, character for "
        "character, in the document they claim to come from. The rest were "
        "**rejected** — the model produced evidence that does not exist, and "
        "the check caught it."
    )
    st.markdown("### Documents")
    for document in load_the_documents():
        share = document["grounded"] / max(1, document["facts"])
        st.markdown(
            f"**{document['filename']}** &nbsp;·&nbsp; {document['page_count']} pages "
            f"&nbsp;·&nbsp; {document['facts']:,} facts, "
            f"{document['grounded']:,} proved ({share:.0%})"
        )


def page_browse_facts() -> None:
    st.title("Facts")
    st.caption(
        "Every fact carries the exact words it came from, and the page and "
        "character range where they sit."
    )

    documents = load_the_documents()
    entities = load_the_entities()

    a, b, c = st.columns([2, 2, 3])
    document_choice = a.selectbox(
        "Document", ["all"] + [d["filename"] for d in documents]
    )
    entity_choice = b.selectbox(
        "Entity", ["all"] + [f"{e['canonical_name']} ({e['facts']})" for e in entities[:60]]
    )
    search_text = c.text_input("Search attribute, subject, value or evidence")

    left, right = st.columns([2, 3])
    only_grounded = left.checkbox(
        "Only facts proved against their source", value=True,
        help="Uncheck to browse the rejected ones — the extraction failures.",
    )
    how_many = right.slider("How many to show", 10, 200, 40)

    document_id = next(
        (d["doc_id"] for d in documents if d["filename"] == document_choice), None
    )
    entity_id = next(
        (e["entity_id"] for e in entities
         if f"{e['canonical_name']} ({e['facts']})" == entity_choice), None
    )

    facts = search_the_facts(search_text, document_id, entity_id, only_grounded, how_many)
    st.markdown(f"**{len(facts)} facts**")

    for fact in facts:
        label = (
            f"{fact['attribute_raw'][:60]} = {describe_a_value(fact)}"
            f"   ·   page {fact['page_no']}"
        )
        if not fact["grounded"]:
            label = "REJECTED  —  " + label
        with st.expander(label):
            show_one_fact(fact)
            if not fact["grounded"]:
                st.error(
                    "This fact was rejected: the quoted evidence could not be "
                    "found in the document it claims to come from."
                )
            elif st.checkbox("Show it in the source page",
                             key=f"page_{fact['fact_id']}"):
                show_the_surrounding_page_text(fact)


def page_the_four_cases() -> None:
    st.title("The four cases")
    st.markdown(
        "The brief asks for one example of each. These are selected by query, "
        "not chosen by hand — they are whatever the system actually produced."
    )

    st.markdown("## 1 · Corroborated across documents, expressed differently")
    st.caption("Same claim, different documents, different wording, often "
               "different units.")
    for index, relation in enumerate(database.fetch_all_rows(RELATION_QUERY.format(
        where="""r.verdict = 'corroborates' AND a.doc_id <> b.doc_id
                 AND lower(a.attribute_raw) <> lower(b.attribute_raw)""",
        order="(a.confidence + b.confidence) DESC",
        limit="3",
    ))):
        show_one_relation(relation, index)

    st.markdown("## 2 · A genuine or likely contradiction")
    st.caption("Both state the same moment, both come from sources we trust, "
               "and the difference is modest. A 99 percent gap usually means "
               "two unrelated things were compared, not that documents disagree.")
    for index, relation in enumerate(database.fetch_all_rows(RELATION_QUERY.format(
        where="""r.verdict = 'contradicts'
                 AND a.confidence >= 0.75 AND b.confidence >= 0.75
                 AND a.value_in_evidence AND b.value_in_evidence
                 AND a.value_num IS NOT NULL AND b.value_num IS NOT NULL
                 AND coalesce(a.period_end, a.as_of_date) IS NOT NULL
                 AND coalesce(a.period_end, a.as_of_date)
                     = coalesce(b.period_end, b.as_of_date)
                 AND abs(a.value_num - b.value_num)
                     / greatest(abs(a.value_num), abs(b.value_num), 1)
                     BETWEEN 0.01 AND 0.5""",
        order="(a.doc_id <> b.doc_id) DESC, (a.confidence + b.confidence) DESC",
        limit="3",
    ))):
        show_one_relation(relation, index)

    st.markdown("## 3 · An apparent contradiction explained by context")
    st.caption("Two figures differ, and a third documented quantity accounts "
               "for exactly the gap. The system shows its arithmetic.")
    for index, relation in enumerate(database.fetch_all_rows(RELATION_QUERY.format(
        where="r.method = 'derivation'",
        order="abs(a.value_num - b.value_num) DESC",
        limit="2",
    ))):
        show_one_relation(relation, index)

    st.markdown("### And a state that changed, which is not a contradiction at all")
    st.caption("A director appointed and later resigned. The documents agree "
               "perfectly; the world moved between them. Telling that apart "
               "from a disagreement is the difference between a useful tool "
               "and an alarm that cries wolf.")
    for index, relation in enumerate(database.fetch_all_rows(RELATION_QUERY.format(
        where="r.verdict = 'superseded'", order="r.relation_id", limit="3",
    ))):
        show_one_relation(relation, index)

    st.markdown("## 4 · An extraction failure, and how it was handled")
    numbers = load_the_overall_numbers()
    rejected = numbers["total"] - numbers["grounded"]
    st.error(
        f"**{rejected:,} of {numbers['total']:,} facts "
        f"({rejected / max(1, numbers['total']):.0%}) were rejected** because "
        "their quoted evidence does not exist in the document."
    )
    st.markdown(
        "Asked to quote a table row verbatim, the model instead **assembled** "
        "evidence, gluing a row's label to the one value it meant:"
    )
    st.code('returned:  "Bad debt written off\\n0.02"\n'
            'on page:   "Bad debt written off | 0.02 | 0.44"')
    st.markdown(
        "Helpful in intent — it was showing which number it claimed — but that "
        "string appears nowhere in the document. Every one was caught.\n\n"
        "**How it was handled:** the prompt was fixed, not the check. Accepting "
        "looser evidence would have validated exactly the mistake the check "
        "exists to catch. Grounding went from 76 to 92 percent on the test "
        "document, with every surviving quote exact."
    )
    st.info(
        "Thirty-seven failures are written up in `docs/04_FAILURES.md`, "
        "including five this project caused itself — among them a derivation "
        "check that proved a gap of 4 using an unrelated quantity of 4, and a "
        "word-boundary bug that made `>2.8Bn` parse as 2.8."
    )

    st.markdown("**A sample of quotes that could not be found:**")
    for row in database.fetch_all_rows(
        """
        SELECT detail FROM failures WHERE stage = 'ground'
          AND detail LIKE %s ORDER BY random() LIMIT 6
        """,
        ("%not found on page%",),
    ):
        detail = row["detail"]
        st.caption(detail[detail.find("not found"):][:170])


def page_browse_relations() -> None:
    st.title("Comparisons")
    st.caption("Every verdict comes with a written reason you can disagree with.")

    chosen = st.multiselect(
        "Verdicts to show",
        list(HOW_TO_SHOW_EACH_VERDICT),
        default=["contradicts", "corroborates", "superseded"],
        format_func=lambda v: f"{HOW_TO_SHOW_EACH_VERDICT[v][0]} ({v})",
    )
    a, b = st.columns([1, 2])
    only_across = a.checkbox(
        "Only across two documents", value=True,
        help="A disagreement inside one document is more often our own "
             "extraction error than a real finding.",
    )
    search_text = b.text_input("Search the attribute")
    how_many = st.slider("How many to show", 5, 100, 20)

    if not chosen:
        st.info("Pick at least one verdict.")
        return

    relations = search_the_relations(chosen, only_across, search_text, how_many)
    st.markdown(f"**{len(relations)} comparisons**")
    for index, relation in enumerate(relations):
        show_one_relation(relation, index)


# ===========================================================================
# Uploading, with progress a person can follow
# ===========================================================================

# Every stage, with what it does and roughly how long it takes relative to the
# others. Written out in full because a progress bar that only counts steps
# tells you nothing about what the machine is doing or why one step is slow.
THE_SEVEN_STAGES = [
    ("Reading the PDF",
     "Pulling text lines with their positions, extracting tables, finding "
     "footnotes and stripping page furniture.", "fast"),
    ("Cutting pages into passages",
     "Grouping lines into blocks, then attaching each passage's section "
     "heading and the footnotes it refers to.", "fast"),
    ("Extracting claims",
     "Sending each passage to the model and asking for structured claims with "
     "verbatim evidence. This is the slow stage — everything else is local.",
     "SLOW"),
    ("Proving every claim in the corpus",
     "Searching for every quoted piece of evidence in the passage it claims to "
     "come from. Anything not found is rejected. This runs over ALL documents, "
     "not only the new one — see the note in the code for why.", "fast"),
    ("Normalising",
     "Parsing numbers, dates and periods; resolving entities; grouping "
     "attribute phrases that mean the same thing.", "medium"),
    ("Finding pairs worth comparing",
     "Grouping facts by entity and attribute so we compare hundreds of pairs "
     "rather than millions.", "fast"),
    ("Deciding what each pair means",
     "Walking the rule tree over every pair, then asking the model about the "
     "handful the rules cannot settle.", "medium"),
]


def render_the_stage_list(placeholder, current_stage: int, results: list[str]) -> None:
    """
    Draw all seven stages at once: what is done, what is running, what is left.

    A bar that only says "3/7" leaves you staring at a frozen screen with no
    idea whether that is normal. Showing every stage, what each one does, and
    which is slow means a long wait is understood rather than merely endured.
    """
    lines = []
    for position, (name, description, speed) in enumerate(THE_SEVEN_STAGES):
        number = position + 1
        if position < current_stage:
            lines.append(
                f"<div class='stage-done'><b>{number}. {name} — done</b><br>"
                f"<span style='font-size:0.85em'>{results[position]}</span></div>"
            )
        elif position == current_stage:
            speed_note = "  ·  this is the slow one" if speed == "SLOW" else ""
            lines.append(
                f"<div class='stage-running'>{number}. {name} — running now"
                f"{speed_note}<br>"
                f"<span style='font-weight:400;font-size:0.85em'>{description}</span></div>"
            )
        else:
            lines.append(
                f"<div class='stage-waiting'>{number}. {name} — waiting<br>"
                f"<span style='font-size:0.85em'>{description}</span></div>"
            )
    placeholder.markdown(
        "<div style='line-height:1.55'>" + "<br>".join(lines) + "</div>",
        unsafe_allow_html=True,
    )


def page_upload() -> None:
    st.title("Upload a PDF")
    st.markdown(
        "Runs all seven stages on a new document: read it, cut it into "
        "passages, extract claims, **prove each claim against the source**, "
        "normalise, and compare against everything already stored."
    )

    uploaded = st.file_uploader("Choose a PDF", type=["pdf"])

    page_ceiling = config.MOST_PAGES_ONE_UPLOAD_MAY_PROCESS
    how_many_pages = st.number_input(
        "Pages to read", min_value=1, max_value=page_ceiling,
        value=min(15, page_ceiling),
        help="Each page costs model requests against a shared daily allowance, "
             "so uploads are capped. Raise MOST_PAGES_ONE_UPLOAD_MAY_PROCESS "
             "when running this on your own machine with your own key.",
    )
    st.caption(
        f"Capped at {page_ceiling} pages per upload. The API allowance is "
        "shared by everyone using this app, and one large document would spend "
        "a whole day of it in a single click."
    )

    if uploaded is None:
        return
    if not st.button("Run the pipeline", type="primary"):
        return

    saved_to = config.INPUT_PDF_FOLDER / uploaded.name
    saved_to.parent.mkdir(parents=True, exist_ok=True)
    saved_to.write_bytes(uploaded.getbuffer())

    page_limit = int(how_many_pages)
    started_at = time.time()

    progress_bar = st.progress(0.0)
    elapsed_note = st.empty()
    stage_list = st.empty()
    results: list[str] = []

    def advance(stage_index: int, result_of_previous: str | None = None) -> None:
        if result_of_previous is not None:
            results.append(result_of_previous)
        progress_bar.progress(stage_index / len(THE_SEVEN_STAGES))
        elapsed_note.caption(
            f"stage {min(stage_index + 1, len(THE_SEVEN_STAGES))} of "
            f"{len(THE_SEVEN_STAGES)}  ·  {time.time() - started_at:.0f} seconds so far"
        )
        render_the_stage_list(stage_list, stage_index, results)

    try:
        advance(0)
        summary = step_01_ingest_pdf.ingest_one_pdf(saved_to, page_limit)
        document_row = {"doc_id": summary["document_id"], "filename": uploaded.name}

        advance(1, f"{summary['pages_read']} pages read, "
                   f"{summary['total_footnotes']} footnotes captured, "
                   f"{summary['total_table_rows']} table rows kept")

        chunks = step_02_build_chunks.build_chunks_for_one_document(document_row)
        advance(2, f"{chunks['chunks']} passages, "
                   f"{chunks['chunks_with_footnote_context']} carrying footnote "
                   f"scope, median {chunks['median_words']} words")

        extracted = step_03_extract_facts.extract_facts_for_one_document(
            document_row, None
        )
        advance(3, f"{extracted['facts']} claims from {extracted['requests']} "
                   f"model requests  ·  {extracted['with_a_period']} carry a "
                   f"period, {extracted['with_a_scope']} carry a scope")

        # Grounding runs over EVERY document, not just the one just uploaded.
        #
        # This was a real bug. Grounding only the new document, then running
        # pairing and comparison across the whole corpus, means any other
        # document whose facts are not currently grounded silently drops out of
        # every comparison — and nothing reports it, because zero pairs is a
        # valid outcome. It happened: re-extracting a document resets its
        # grounded flag, and the next upload produced a dashboard reading
        # "0 of 4,965 facts proved" with no error anywhere.
        #
        # Grounding is pure string searching with no API calls, so running it
        # over everything costs seconds and makes the pipeline self-healing.
        database.execute_sql("DELETE FROM failures WHERE stage = 'ground'")
        fact_rows = step_04_check_grounding.load_facts_with_their_source_text(None)
        grounding = [step_04_check_grounding.ground_one_fact(r) for r in fact_rows]
        step_04_check_grounding.save_what_we_learned(grounding)
        step_04_check_grounding.record_the_rejections(
            grounding, {r["fact_id"]: r for r in fact_rows}
        )
        proved = sum(1 for r in grounding if r["grounded"])
        if proved == 0:
            raise RuntimeError(
                f"None of the {len(grounding)} facts could be proved against "
                "their source. Something is wrong upstream — stopping rather "
                "than building comparisons on nothing."
            )
        advance(4, f"{proved} of {len(grounding)} claims proved across all "
                   f"documents ({proved / max(1, len(grounding)):.0%})  ·  "
                   f"{len(grounding) - proved} rejected")

        step_05_normalize_facts.main()
        advance(5, "values, dates and periods parsed; entities and attribute "
                   "families resolved across the whole corpus")

        step_06_find_candidate_pairs.main()
        pairs = database.count_rows_in_table("relations")
        if pairs == 0:
            # Zero pairs is a valid outcome for a corpus of one short document,
            # so nothing downstream would complain. That silence is exactly how
            # the earlier failure went unnoticed, so we complain here instead.
            raise RuntimeError(
                "No pairs worth comparing were produced. Either no facts are "
                "grounded, or normalisation did not assign entities."
            )
        advance(6, f"{pairs:,} pairs worth comparing")

        step_07_adjudicate_pairs.main()
        advance(7, "every pair judged, each with a written reason")

    except Exception as problem:
        st.error(f"The pipeline stopped: {type(problem).__name__}: {problem}")
        st.caption("Whatever finished before the failure was saved — every "
                   "stage writes as it goes.")
        return

    progress_bar.progress(1.0)
    st.cache_data.clear()
    st.success(
        f"Finished in {time.time() - started_at:.0f} seconds. Look at "
        "**The four cases** or **Comparisons** to see how this document "
        "relates to the others."
    )


# ===========================================================================
# Getting started
# ===========================================================================

PAGES = {
    "Overview": page_overview,
    "Facts": page_browse_facts,
    "The four cases": page_the_four_cases,
    "Comparisons": page_browse_relations,
    "Upload a PDF": page_upload,
}


def main() -> None:
    st.markdown(STYLES, unsafe_allow_html=True)

    st.sidebar.title("Ground Work")
    st.sidebar.caption("A fact knowledge layer for PDFs")

    missing = config.find_missing_required_settings(["DATABASE_URL"])
    if missing:
        st.error(
            "DATABASE_URL is not set. Copy `.env.example` to `.env` and fill it in."
        )
        return

    choice = st.sidebar.radio("Go to", list(PAGES), label_visibility="collapsed")
    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Nothing here is shown without its evidence. Every fact carries the "
        "exact words it came from and the page they sit on; every verdict "
        "carries the reasoning that produced it."
    )
    PAGES[choice]()


main()
