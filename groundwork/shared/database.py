"""
Everything that talks to PostgreSQL.

ONE IDEA DRIVES THIS WHOLE FILE.

Our database is hosted on Supabase, which means it lives on a machine somewhere
on the internet. Every single call travels there and back. That round trip costs
somewhere between 50 and 300 milliseconds depending on your connection.

Now do the arithmetic. The pipeline produces a few thousand facts. Saving them
one row at a time, at 150ms per row, is several minutes of the program doing
nothing but waiting. Saving them 200 at a time is a handful of round trips and
takes about a second.

So the main writing function here takes a LIST of rows. Nothing else in this
project inserts rows one at a time. If you ever find yourself writing a loop
with an insert inside it, stop: build a list, then hand the list to
`insert_rows_in_batches`.
"""

import re
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json

from groundwork.shared import config


# ---------------------------------------------------------------------------
# The connection
# ---------------------------------------------------------------------------

# We keep one connection open and reuse it, rather than opening a new one for
# every query. Opening a connection to a hosted database involves a network
# handshake and a TLS negotiation, which is far more expensive than the query
# itself. This variable holds that shared connection.
_shared_connection = None


def get_database_connection():
    """
    Return the shared database connection, opening it on first use.

    If the connection has been closed or dropped (a laptop sleeping, or wifi
    going away), this opens a fresh one rather than handing back a dead object.
    """
    global _shared_connection

    # "Not closed" is not the same as "usable". A connection to a hosted
    # database can be dropped at the other end — an idle timeout, a network
    # blip — and still report itself as open, then fail on the next query with
    # "SSL connection has been closed unexpectedly". That happened on a long
    # run, so we ask the connection a trivial question rather than trusting its
    # own opinion of itself.
    if _shared_connection is not None and not _shared_connection.closed:
        try:
            with _shared_connection.cursor() as checking_cursor:
                checking_cursor.execute("SELECT 1")
            return _shared_connection
        except Exception:
            try:
                _shared_connection.close()
            except Exception:
                pass
            _shared_connection = None

    config.stop_unless_these_settings_are_filled_in(["DATABASE_URL"])

    _shared_connection = psycopg.connect(
        config.DATABASE_URL,

        # Rows come back as dictionaries keyed by column name, so downstream
        # code reads row["value_num"] instead of row[11]. Position-based access
        # silently breaks the moment someone adds a column.
        row_factory=dict_row,

        # Each statement commits by itself. For a pipeline that appends batches
        # of rows this is what we want, and it removes a whole category of bug
        # where work is silently lost because nobody called commit().
        autocommit=True,
    )
    return _shared_connection


def close_database_connection() -> None:
    """Close the shared connection if one is open. Safe to call at any time."""
    global _shared_connection
    if _shared_connection is not None and not _shared_connection.closed:
        _shared_connection.close()
    _shared_connection = None


def check_database_is_reachable() -> bool:
    """
    Ask the database a trivial question and see if it answers.

    Used by the setup script and the tests to tell "your settings are wrong"
    apart from "your SQL is wrong", which otherwise look identical.
    """
    return try_connecting_and_describe_any_problem() is None


# Characters that act as separators inside a URL. A password containing one of
# these gets torn in half by whichever parser sees it first, so its pieces can
# turn up in an error message on their own, without the whole password ever
# appearing. See the note below about why this matters.
URL_SEPARATOR_CHARACTERS = r"[@:/?#&=]"

# Fragments shorter than this are not redacted. Redacting two-character pieces
# would blank out ordinary words in the error text and make it unreadable.
SHORTEST_FRAGMENT_WORTH_HIDING = 3


def remove_the_password_from(some_text: str) -> str:
    """
    Replace the database password, and any recognisable piece of it, with '***'.

    Error messages from the driver sometimes echo parts of the connection
    string. Everything we print goes through here first, so a password never
    reaches a terminal, a log file, or a screenshot.

    Why this checks fragments as well as the whole password: if the password
    contains an '@', two different parsers disagree about where it ends. Python
    splits at the last '@', the database driver splits at the first. So the
    driver's error message can contain the tail of the password glued to the
    host name — a piece we would completely miss if we only searched for the
    password as one whole string. We learned that the hard way; it is written
    up in docs/04_FAILURES.md.
    """
    password_in_the_url = urlparse(config.DATABASE_URL).password
    if not password_in_the_url:
        return some_text

    cleaned_text = some_text.replace(password_in_the_url, "***")

    for password_fragment in re.split(URL_SEPARATOR_CHARACTERS, password_in_the_url):
        if len(password_fragment) >= SHORTEST_FRAGMENT_WORTH_HIDING:
            cleaned_text = cleaned_text.replace(password_fragment, "***")

    return cleaned_text


def try_connecting_and_describe_any_problem() -> str | None:
    """
    Attempt a connection. Return None if it worked, or the error if it did not.

    Why this exists as well as the boolean check: a bare True/False tells you
    something is wrong but not what, and "cannot connect" has at least five
    very different causes. Returning the driver's own message turns a guessing
    game into a five-second diagnosis.
    """
    try:
        connection = get_database_connection()
        with connection.cursor() as database_cursor:
            database_cursor.execute("SELECT 1 AS reachable")
            result_row = database_cursor.fetchone()

        if result_row is None or result_row["reachable"] != 1:
            return "Connected, but the database gave an unexpected answer."
        return None

    except Exception as connection_problem:
        problem_type = type(connection_problem).__name__
        problem_text = str(connection_problem).strip()
        return remove_the_password_from(f"{problem_type}: {problem_text}")


def describe_connection_target() -> str:
    """
    Show which server we are trying to reach, with the password removed.

    This is usually enough on its own to spot the most common mistake, which is
    using Supabase's direct connection string instead of the session pooler one.
    """
    if not config.DATABASE_URL:
        return "DATABASE_URL is empty."

    try:
        parsed_url = urlparse(config.DATABASE_URL)
    except ValueError as parsing_problem:
        return f"DATABASE_URL could not be parsed: {parsing_problem}"

    host_name = parsed_url.hostname or "(no host)"
    port_number = parsed_url.port or "(no port)"
    user_name = parsed_url.username or "(no user)"
    database_name = (parsed_url.path or "").lstrip("/") or "(no database name)"
    password_was_given = "yes" if parsed_url.password else "NO — this is the problem"

    # Supabase's pooler hosts all contain the word "pooler". The direct host
    # does not, and the direct host is IPv6-only on the free tier.
    if "pooler" in host_name:
        which_kind_of_host = "session/transaction pooler  (correct for IPv4)"
    elif "supabase" in host_name:
        which_kind_of_host = "DIRECT connection  (IPv6-only — likely the problem)"
    else:
        which_kind_of_host = "not a Supabase host"

    return "\n".join([
        f"  host          : {host_name}",
        f"  host type     : {which_kind_of_host}",
        f"  port          : {port_number}",
        f"  user          : {user_name}",
        f"  database      : {database_name}",
        f"  password given: {password_was_given}",
    ])


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def fetch_all_rows(sql_query: str, query_parameters: tuple = ()) -> list[dict]:
    """
    Run a SELECT and return every row as a dictionary.

    Always pass values through `query_parameters` rather than pasting them into
    the query string. psycopg then sends them separately from the SQL, which
    makes SQL injection impossible and handles quoting and dates for you.

        fetch_all_rows("SELECT * FROM facts WHERE doc_id = %s", (doc_id,))
    """
    connection = get_database_connection()
    with connection.cursor() as database_cursor:
        database_cursor.execute(sql_query, query_parameters)
        return database_cursor.fetchall()


def fetch_one_row(sql_query: str, query_parameters: tuple = ()) -> dict | None:
    """Run a SELECT and return the first row, or None if there were no rows."""
    connection = get_database_connection()
    with connection.cursor() as database_cursor:
        database_cursor.execute(sql_query, query_parameters)
        return database_cursor.fetchone()


def count_rows_in_table(table_name: str) -> int:
    """Return how many rows a table currently holds."""
    # The table name cannot be passed as a parameter — parameters are for
    # values, not for names. sql.Identifier quotes it properly instead.
    count_query = sql.SQL("SELECT COUNT(*) AS row_count FROM {table_name}").format(
        table_name=sql.Identifier(table_name)
    )
    connection = get_database_connection()
    with connection.cursor() as database_cursor:
        database_cursor.execute(count_query)
        result_row = database_cursor.fetchone()
    return result_row["row_count"]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def execute_sql(sql_statement: str, statement_parameters: tuple = ()) -> None:
    """
    Run a single statement that does not return rows: UPDATE, DELETE, and so on.

    For inserting many rows use `insert_rows_in_batches` instead — see the note
    at the top of this file for why.
    """
    connection = get_database_connection()
    with connection.cursor() as database_cursor:
        database_cursor.execute(sql_statement, statement_parameters)


def run_sql_script(sql_script_text: str) -> None:
    """
    Run a block of SQL that may contain several statements separated by ';'.

    Used for creating the tables. Not for anything that takes user input — it
    sends the text straight to the database.
    """
    connection = get_database_connection()
    with connection.cursor() as database_cursor:
        database_cursor.execute(sql_script_text)


def build_insert_statement(
    table_name: str,
    column_names: list[str],
    skip_rows_that_already_exist: bool,
):
    """
    Build an INSERT statement with a placeholder for each column.

    Produces, for example:
        INSERT INTO "facts" ("fact_id", "value_num") VALUES (%s, %s)

    Table and column names go through sql.Identifier, which quotes them safely.
    The values themselves are never put into the string — they travel separately
    as parameters.
    """
    quoted_column_names = [sql.Identifier(name) for name in column_names]
    one_placeholder_per_column = [sql.Placeholder() for _ in column_names]

    insert_statement = sql.SQL(
        "INSERT INTO {table_name} ({column_list}) VALUES ({placeholder_list})"
    ).format(
        table_name=sql.Identifier(table_name),
        column_list=sql.SQL(", ").join(quoted_column_names),
        placeholder_list=sql.SQL(", ").join(one_placeholder_per_column),
    )

    if skip_rows_that_already_exist:
        # Makes re-running a pipeline step harmless: rows that clash with an
        # existing primary key are quietly ignored instead of raising an error
        # and killing the run.
        insert_statement = insert_statement + sql.SQL(" ON CONFLICT DO NOTHING")

    return insert_statement


def check_every_row_has_the_same_columns(rows_to_insert: list[dict]) -> list[str]:
    """
    Confirm all rows share the same keys, and return those keys.

    Why this check exists: we build one INSERT statement and reuse it for the
    whole batch. If row 47 has an extra key, the values would silently line up
    with the wrong columns. Better to fail loudly here with a clear message.
    """
    column_names = list(rows_to_insert[0].keys())
    expected_columns = set(column_names)

    for row_position, single_row in enumerate(rows_to_insert):
        if set(single_row.keys()) != expected_columns:
            unexpected = set(single_row.keys()) - expected_columns
            missing = expected_columns - set(single_row.keys())
            raise ValueError(
                f"Row {row_position} does not have the same columns as row 0.\n"
                f"  unexpected columns: {sorted(unexpected) or 'none'}\n"
                f"  missing columns   : {sorted(missing) or 'none'}\n"
                "Every row in one insert must have identical keys."
            )

    return column_names


def insert_rows_in_batches(
    table_name: str,
    rows_to_insert: list[dict],
    skip_rows_that_already_exist: bool = False,
    batch_size: int | None = None,
) -> int:
    """
    Insert many rows using as few network round trips as possible.

    This is the main writing function in the project.

        insert_rows_in_batches("facts", [{"fact_id": "f1", ...}, ...])

    Each dictionary is one row: keys are column names, values are the values.
    Every dictionary must have the same keys.

    Set `skip_rows_that_already_exist=True` to make the insert safe to re-run
    when some rows are already there.

    Returns the number of rows sent.
    """
    if not rows_to_insert:
        return 0

    column_names = check_every_row_has_the_same_columns(rows_to_insert)

    if batch_size is None:
        batch_size = config.DATABASE_BATCH_SIZE

    insert_statement = build_insert_statement(
        table_name=table_name,
        column_names=column_names,
        skip_rows_that_already_exist=skip_rows_that_already_exist,
    )

    # psycopg wants each row as a tuple of values in the same order as the
    # column list, so we convert the dictionaries here.
    rows_as_tuples = [
        tuple(single_row[column_name] for column_name in column_names)
        for single_row in rows_to_insert
    ]

    connection = get_database_connection()
    number_of_rows_sent = 0

    with connection.cursor() as database_cursor:
        for batch_start in range(0, len(rows_as_tuples), batch_size):
            one_batch = rows_as_tuples[batch_start:batch_start + batch_size]

            # executemany sends the whole batch in one go rather than one
            # statement at a time. This single line is the reason the whole
            # pipeline takes seconds instead of minutes.
            database_cursor.executemany(insert_statement, one_batch)

            number_of_rows_sent += len(one_batch)

    return number_of_rows_sent


def update_rows_in_batches(
    update_statement: str,
    values_for_each_row: list[tuple],
    batch_size: int | None = None,
) -> int:
    """
    Run the same UPDATE for many rows, in as few round trips as possible.

    The counterpart to `insert_rows_in_batches`, and it exists for the same
    reason: the database is on the other side of the internet, so updating
    three thousand rows one statement at a time is minutes of pure waiting.

        update_rows_in_batches(
            "UPDATE facts SET grounded = %s WHERE fact_id = %s",
            [(True, "f1"), (False, "f2"), ...],
        )

    Each tuple supplies the placeholders for one row, in order.
    """
    if not values_for_each_row:
        return 0

    if batch_size is None:
        batch_size = config.DATABASE_BATCH_SIZE

    connection = get_database_connection()
    number_of_rows_updated = 0

    with connection.cursor() as database_cursor:
        for batch_start in range(0, len(values_for_each_row), batch_size):
            one_batch = values_for_each_row[batch_start:batch_start + batch_size]
            database_cursor.executemany(update_statement, one_batch)
            number_of_rows_updated += len(one_batch)

    return number_of_rows_updated


def as_json(python_value):
    """
    Wrap a dictionary or list so it can be stored in a JSONB column.

    Without this wrapper psycopg tries to store a Python dict as text and the
    database rejects it. Any value going into `pages.footnotes`,
    `entities.aliases`, `facts.scope_tags` and similar must be wrapped.
    """
    return Json(python_value)


# ---------------------------------------------------------------------------
# Recording failures
# ---------------------------------------------------------------------------

def record_failure(
    stage: str,
    kind: str,
    detail: str,
    doc_id: str | None = None,
    page_no: int | None = None,
    chunk_id: str | None = None,
) -> None:
    """
    Write one row to the `failures` table.

    Called whenever something goes wrong that we do not want to crash on: a
    page that will not parse, an LLM answer that is not valid JSON, a quoted
    piece of evidence that cannot be found in its source.

    This table is a graded deliverable, not a debug log. It is where the
    "extraction or reasoning failure" case in the brief comes from, so record
    enough detail that the failure can be understood weeks later.
    """
    insert_rows_in_batches(
        "failures",
        [{
            "doc_id": doc_id,
            "page_no": page_no,
            "chunk_id": chunk_id,
            "stage": stage,
            "kind": kind,
            "detail": detail,
        }],
    )
