"""
Settings for the whole project.

Every setting lives in the `.env` file at the project root. Nothing else in this
codebase reads environment variables directly — everything asks this module.

Why bother with that rule? Because when a setting is wrong at 2am, you want
exactly one file to look in, not fifteen.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Where things live on disk
#
# We work these paths out from THIS FILE's own location, not from the folder
# the user happened to run the command in. That means a script behaves the same
# whether you launch it from the project root, from inside tests/, or from
# Streamlit — all of which use different working directories.
# ---------------------------------------------------------------------------

THIS_FILE_PATH = Path(__file__).resolve()

# config.py -> shared/ -> groundwork/ -> project root
PROJECT_ROOT_FOLDER = THIS_FILE_PATH.parent.parent.parent

DATA_FOLDER = PROJECT_ROOT_FOLDER / "data"
INPUT_PDF_FOLDER = DATA_FOLDER / "input_pdfs"
LLM_CACHE_FOLDER = DATA_FOLDER / "llm_cache"
DOCS_FOLDER = PROJECT_ROOT_FOLDER / "docs"

ENV_FILE_PATH = PROJECT_ROOT_FOLDER / ".env"


# Read the .env file into the process environment. If the file is missing,
# load_dotenv does nothing and every setting below falls back to its default —
# which is what makes the "is this filled in?" check further down necessary.
load_dotenv(ENV_FILE_PATH)


# ---------------------------------------------------------------------------
# Small readers, so every setting below reads the same way
# ---------------------------------------------------------------------------

def read_from_streamlit_secrets(setting_name: str) -> str | None:
    """
    Look for a setting in Streamlit's secrets store, if we are inside Streamlit.

    Needed for deployment. On a hosting platform there is no `.env` file —
    secrets are supplied by the platform instead, and Streamlit Community Cloud
    supplies them through `st.secrets`.

    Everything is wrapped in try/except on purpose: this module is imported by
    the command-line pipeline scripts too, where Streamlit is not running and
    touching `st.secrets` raises. A missing secrets store is a normal state,
    not an error.
    """
    try:
        import streamlit
        value = streamlit.secrets[setting_name]
    except Exception:
        return None
    return str(value)


def read_text_setting(setting_name: str, default_value: str = "") -> str:
    """
    Read a setting as text, with surrounding whitespace stripped.

    Three places are tried, in this order:
      1. an environment variable — which is also how `.env` values arrive
      2. Streamlit's secrets store, when deployed
      3. the default given here

    The environment wins so that a deployed app can still be overridden without
    editing its secrets.
    """
    from_the_environment = os.getenv(setting_name)
    if from_the_environment:
        return from_the_environment.strip()

    from_streamlit = read_from_streamlit_secrets(setting_name)
    if from_streamlit:
        return from_streamlit.strip()

    return default_value.strip()


def read_whole_number_setting(setting_name: str, default_value: int) -> int:
    """
    Read a setting as a whole number.

    If someone types something that is not a number into .env we fall back to
    the default rather than crashing, because a bad batch size should not stop
    the whole pipeline.
    """
    raw_value = read_text_setting(setting_name)
    if not raw_value:
        return default_value
    try:
        return int(raw_value)
    except ValueError:
        return default_value


# ---------------------------------------------------------------------------
# The settings themselves
# ---------------------------------------------------------------------------

# --- Secrets. These have no sensible default; they must come from .env. ---

DATABASE_URL = read_text_setting("DATABASE_URL")
GOOGLE_API_KEY = read_text_setting("GOOGLE_API_KEY")

# --- Choices with a reasonable default ---

# Always pin an exact model version here, never an alias like
# "gemini-flash-latest". The LLM cache key is built from the model NAME, so if
# an alias were silently repointed at a different model the key would not
# change and we would go on serving answers produced by a model we are no
# longer using.
#
# Free-tier limits READ FROM THE PROVIDER'S DASHBOARD on 2026-09-08. Do not
# guess these; we did, and were wrong by a factor of twelve.
#
#   gemini-3.5-flash-lite   15 requests/min   500 requests/day
#   gemini-3.6-flash         5 requests/min    20 requests/day
#   gemini-3.7-flash         5 requests/min    20 requests/day
#   gemini-3.8-flash         5 requests/min    20 requests/day
#
# Flash Lite is the only model whose daily allowance can carry the bulk
# extraction work at all. The larger models are worth saving for the small
# number of genuinely hard judgements in Step 7.
GEMINI_MODEL = read_text_setting("GEMINI_MODEL", "gemini-3.5-flash-lite")

# A different, stronger model for the small number of genuinely hard
# judgements in adjudication.
#
# This is the constraint turned into a design. Flash Lite has the daily
# allowance to read 227 pages; Flash 3.7 has 20 requests a day, which is
# useless for bulk work and ample for judgement — because the rule tree settles
# almost every pair on its own and only a handful ever reach a model.
# Cheap model for volume, good model for thinking.
# Defaults to the model that actually has allowance left. Pointing this at
# gemini-3.7-flash is the better idea in principle and was tried: with its 20
# requests already spent for the day, all but one call failed after five
# retries each, costing seven minutes and changing nothing. A stronger model is
# only stronger if it will answer.
ADJUDICATION_MODEL = read_text_setting("ADJUDICATION_MODEL", "gemini-3.5-flash-lite")

# A hard ceiling on how many pairs may be sent to the adjudication model, so a
# run can never quietly consume a whole day's allowance of the scarce one.
MOST_PAIRS_TO_ASK_THE_MODEL_ABOUT = read_whole_number_setting(
    "MOST_PAIRS_TO_ASK_THE_MODEL_ABOUT", 15
)

# The largest number of pages a single upload through the interface may
# process.
#
# This matters as soon as the app is deployed somewhere public. The API key is
# ours, the daily allowance is ours, and one visitor uploading a 400-page
# document would spend the lot in a single click. Raise it for local use; keep
# it low anywhere a stranger can reach it.
MOST_PAGES_ONE_UPLOAD_MAY_PROCESS = read_whole_number_setting(
    "MOST_PAGES_ONE_UPLOAD_MAY_PROCESS", 50
)

# How many rows we send to the database in one write. The database is hosted,
# so every write is a round trip over the internet. See the docstring at the
# top of database.py for why this number matters so much.
DATABASE_BATCH_SIZE = read_whole_number_setting("DATABASE_BATCH_SIZE", 200)

# How many LLM calls run at the same time. Kept low on purpose: the free tier
# limits requests per minute, so more threads buy nothing and risk a ban.
LLM_CONCURRENT_REQUESTS = read_whole_number_setting("LLM_CONCURRENT_REQUESTS", 4)

# The free tier allows only a handful of requests a minute. We space our own
# requests out to stay under that, because being throttled costs far more time
# than waiting politely does — a rejected request still burns part of the daily
# allowance on some tiers.
# Set this BELOW your model's real per-minute limit, not at it. Our previous
# value of 10 was double what the Flash models actually allow.
LLM_REQUESTS_PER_MINUTE = read_whole_number_setting("LLM_REQUESTS_PER_MINUTE", 12)

# Part of the LLM cache key. Bump this string whenever the extraction prompt
# changes, so improved prompts correctly ignore answers cached from old ones.
EXTRACTION_PROMPT_VERSION = read_text_setting("EXTRACTION_PROMPT_VERSION", "v1")


# ---------------------------------------------------------------------------
# Checking that the settings we need are actually filled in
# ---------------------------------------------------------------------------

# The placeholder text that ships in .env.example. If a setting still holds one
# of these, the user copied the template but never filled it in — which is a
# different (and much more common) problem than the setting being absent.
PLACEHOLDER_VALUES_THAT_MEAN_NOT_FILLED_IN = {
    "paste-your-key-here",
    "postgresql://user:password@host:5432/postgres",
}


def a_setting_is_filled_in(setting_value: str) -> bool:
    """True only if the setting has a real value, not blank and not a placeholder."""
    if not setting_value:
        return False
    return setting_value not in PLACEHOLDER_VALUES_THAT_MEAN_NOT_FILLED_IN


def find_missing_required_settings(required_setting_names: list[str]) -> list[str]:
    """
    Return the names of any settings that are still blank or still a placeholder.

    Returns a list rather than raising on the first problem, so the user is told
    about all the missing settings at once instead of discovering them one run
    at a time.
    """
    missing_setting_names = []
    for setting_name in required_setting_names:
        current_value = globals().get(setting_name, "")
        if not a_setting_is_filled_in(current_value):
            missing_setting_names.append(setting_name)
    return missing_setting_names


def stop_unless_these_settings_are_filled_in(required_setting_names: list[str]) -> None:
    """
    Raise a clear, actionable error if any required setting is missing.

    Every script calls this before it does any real work, so a misconfigured
    project fails immediately with instructions rather than failing later with
    a confusing database or API error.
    """
    missing_setting_names = find_missing_required_settings(required_setting_names)
    if not missing_setting_names:
        return

    missing_list = "\n".join(f"  - {name}" for name in missing_setting_names)
    raise RuntimeError(
        "These settings are missing or still hold the example placeholder:\n"
        f"{missing_list}\n\n"
        f"Fix: open this file and fill them in ->  {ENV_FILE_PATH}\n"
        "If the file does not exist, copy .env.example to .env first."
    )


# ---------------------------------------------------------------------------
# A safe way to look at the current settings
# ---------------------------------------------------------------------------

def hide_middle_of_secret(secret_value: str) -> str:
    """
    Turn a secret into something safe to print: 'AIzaSy...9kQw'.

    Used so we can debug 'did it read my key?' without the key ending up in a
    terminal log, a screenshot, or a screen share.
    """
    if not a_setting_is_filled_in(secret_value):
        return "(not set)"
    if len(secret_value) <= 12:
        return "***"
    return f"{secret_value[:6]}...{secret_value[-4:]}"


def describe_current_settings() -> str:
    """Build a human-readable summary of the settings, with secrets masked."""
    lines = [
        "Ground Work settings",
        "--------------------",
        f"project root            : {PROJECT_ROOT_FOLDER}",
        f".env file found         : {ENV_FILE_PATH.exists()}",
        f"input pdf folder        : {INPUT_PDF_FOLDER}",
        f"llm cache folder        : {LLM_CACHE_FOLDER}",
        "",
        f"DATABASE_URL            : {hide_middle_of_secret(DATABASE_URL)}",
        f"GOOGLE_API_KEY          : {hide_middle_of_secret(GOOGLE_API_KEY)}",
        f"GEMINI_MODEL            : {GEMINI_MODEL}",
        f"DATABASE_BATCH_SIZE     : {DATABASE_BATCH_SIZE}",
        f"LLM_CONCURRENT_REQUESTS : {LLM_CONCURRENT_REQUESTS}",
        f"LLM_REQUESTS_PER_MINUTE : {LLM_REQUESTS_PER_MINUTE}",
        f"EXTRACTION_PROMPT_VERSION: {EXTRACTION_PROMPT_VERSION}",
    ]
    return "\n".join(lines)


# Running `python -m groundwork.shared.config` prints the summary. Handy as a
# first check that .env is being found and read at all.
if __name__ == "__main__":
    print(describe_current_settings())
