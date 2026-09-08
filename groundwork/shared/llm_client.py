"""
The one place in this project that talks to a language model.

Run a self-check with:
    .\\.venv\\Scripts\\python.exe -m groundwork.shared.llm_client

---------------------------------------------------------------------------
WHY EVERY CALL GOES THROUGH ONE FUNCTION
---------------------------------------------------------------------------
Caching, retries, rate limiting and error handling live here and nowhere else.
Two payoffs. The obvious one is that these are written once instead of in
every step that needs a model. The one that matters more: if Google throttles
us at hour nineteen, swapping to a different provider means editing one
function, not hunting through the whole codebase at the worst possible moment.

---------------------------------------------------------------------------
THE CACHE IS NOT AN OPTIMISATION. IT IS HOW THE PROJECT SURVIVES.
---------------------------------------------------------------------------
We are on a free tier with a daily request allowance, and we will re-run this
pipeline many times while getting the prompt right. Without a cache the
allowance is gone by mid-afternoon and no more work can be done that day.

With one, a re-run costs nothing for every passage whose text and prompt have
not changed. Only genuinely new work costs an API call.

The cache lives on local disk, never in the database. It is just files: reading
one takes microseconds, where a hosted database would add a network round trip
to the very thing that needs to be instant. It would also bloat the database
with text we never query.

---------------------------------------------------------------------------
WHAT THE CACHE KEY IS MADE OF, AND WHY EACH PART IS THERE
---------------------------------------------------------------------------
    the exact prompt text  — different input must mean a different answer
    the model name         — a different model gives a different answer
    the prompt version     — OUR OWN version string, from .env

That third one is the subtle one. When we improve the extraction prompt, every
cached answer was produced by the OLD prompt. Without a version in the key we
would keep serving those stale answers and conclude, wrongly, that improving
the prompt changed nothing. Bumping EXTRACTION_PROMPT_VERSION in .env
invalidates them cleanly.

Note also what this means for batching in Step 5: the prompt text IS the key,
so several chunks packed into one request must be packed the same way every
run. Group them in a stable order or every run looks new and the cache never
hits.
"""

import hashlib
import json
import logging
import sys
import threading
import time
from pathlib import Path

from google import genai
from google.genai import types

from groundwork.shared import config


# The SDK logs a warning about automatic function calling on every single
# generate_content call, even when no tools are involved and it does not apply
# to us. Over a few hundred requests it buries the output that matters, and
# noise you learn to ignore is how you miss the one warning that counts.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)


# ===========================================================================
# Tuning numbers
# ===========================================================================

# How many times to retry a request the provider refused for a reason that
# might not happen again — a rate limit, a momentary server error.
HOW_MANY_TIMES_TO_RETRY = 4

# Wait this long before the first retry, then double it each time: 2, 4, 8, 16.
# Backing off rather than retrying immediately is the difference between
# waiting out a rate limit and being throttled harder for hammering.
SECONDS_TO_WAIT_BEFORE_FIRST_RETRY = 2.0

# Phrases that mean "try again in a moment" rather than "this will never work".
# Retrying a genuinely broken request just wastes the daily allowance, so we
# only retry when the message looks temporary.
SIGNS_A_FAILURE_IS_WORTH_RETRYING = (
    "429",
    "resource_exhausted",
    "resource exhausted",
    "quota",
    "rate limit",
    "too many requests",
    "503",
    "500",
    "unavailable",
    "internal error",
    "deadline",
    "timeout",
)


# ===========================================================================
# Counters, so we can report what a run actually cost
# ===========================================================================

_how_many_answers_came_from_the_cache = 0
_how_many_requests_we_actually_sent = 0
_how_many_requests_failed_for_good = 0
_counter_lock = threading.Lock()


def reset_the_counters() -> None:
    """Start counting again. Called at the beginning of a pipeline step."""
    global _how_many_answers_came_from_the_cache
    global _how_many_requests_we_actually_sent
    global _how_many_requests_failed_for_good
    with _counter_lock:
        _how_many_answers_came_from_the_cache = 0
        _how_many_requests_we_actually_sent = 0
        _how_many_requests_failed_for_good = 0


def describe_what_this_run_cost() -> str:
    """A one-line summary of cache hits against real API calls."""
    total_asked = _how_many_answers_came_from_the_cache + _how_many_requests_we_actually_sent
    return (
        f"prompts asked: {total_asked}   "
        f"from cache: {_how_many_answers_came_from_the_cache}   "
        f"sent to the API: {_how_many_requests_we_actually_sent}   "
        f"gave up on: {_how_many_requests_failed_for_good}"
    )


def how_many_requests_we_actually_sent() -> int:
    return _how_many_requests_we_actually_sent


# ===========================================================================
# The cache
# ===========================================================================

def build_cache_key(prompt_text: str, model_name: str, prompt_version: str) -> str:
    """
    Turn everything that could change the answer into one short key.

    A hash rather than the text itself because prompts are thousands of
    characters long and this becomes a filename.
    """
    everything_that_matters = "\n---\n".join([prompt_version, model_name, prompt_text])
    return hashlib.sha256(everything_that_matters.encode("utf-8")).hexdigest()


def path_of_cached_answer(cache_key: str) -> Path:
    config.LLM_CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
    return config.LLM_CACHE_FOLDER / f"{cache_key}.json"


def read_cached_answer(cache_key: str) -> str | None:
    """Return a previously stored answer, or None if we have not seen this prompt."""
    cache_file = path_of_cached_answer(cache_key)
    if not cache_file.exists():
        return None
    try:
        stored = json.loads(cache_file.read_text(encoding="utf-8"))
        return stored["response"]
    except (json.JSONDecodeError, KeyError, OSError):
        # A half-written or corrupted cache file is not worth crashing over.
        # Treat it as a miss; it will be overwritten with a good answer.
        return None


def write_answer_to_cache(
    cache_key: str, prompt_text: str, response_text: str, model_name: str
) -> None:
    """
    Store an answer, alongside the prompt that produced it.

    Keeping the prompt costs disk we have plenty of and answers the question
    you always end up asking: what did we ACTUALLY send? Reconstructing that
    later from the code is guesswork.
    """
    cache_file = path_of_cached_answer(cache_key)
    stored = {
        "model": model_name,
        "prompt": prompt_text,
        "response": response_text,
        "stored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Write to a temporary name and rename into place. A rename is atomic, so an
    # interrupted run cannot leave a half-written file that later reads as a
    # valid but truncated answer.
    temporary_file = cache_file.with_suffix(".json.partial")
    temporary_file.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    temporary_file.replace(cache_file)


def describe_the_cache() -> str:
    """Say how many answers are stored and how much room they take."""
    if not config.LLM_CACHE_FOLDER.exists():
        return "cache folder does not exist yet"
    cached_files = list(config.LLM_CACHE_FOLDER.glob("*.json"))
    total_bytes = sum(file.stat().st_size for file in cached_files)
    return f"{len(cached_files)} cached answers, {total_bytes / 1024 / 1024:.1f} MB"


# ===========================================================================
# Staying under the rate limit
# ===========================================================================

_time_the_last_request_was_sent = 0.0
_rate_limit_lock = threading.Lock()


def wait_our_turn_before_sending() -> None:
    """
    Pause until enough time has passed since the last request.

    Several threads share this, so the spacing is global rather than per
    thread. Without that, four workers would fire four requests at once and
    trip the per-minute limit immediately.
    """
    global _time_the_last_request_was_sent

    seconds_between_requests = 60.0 / max(1, config.LLM_REQUESTS_PER_MINUTE)

    with _rate_limit_lock:
        seconds_since_last = time.monotonic() - _time_the_last_request_was_sent
        if seconds_since_last < seconds_between_requests:
            time.sleep(seconds_between_requests - seconds_since_last)
        _time_the_last_request_was_sent = time.monotonic()


def a_failure_is_worth_retrying(problem: Exception) -> bool:
    """True when the error looks temporary rather than permanent."""
    described = f"{type(problem).__name__}: {problem}".lower()
    return any(sign in described for sign in SIGNS_A_FAILURE_IS_WORTH_RETRYING)


# ===========================================================================
# Talking to the model
# ===========================================================================

_shared_client = None
_client_lock = threading.Lock()


def get_model_client():
    """Build the API client once and reuse it."""
    global _shared_client
    if _shared_client is not None:
        return _shared_client

    with _client_lock:
        if _shared_client is None:
            config.stop_unless_these_settings_are_filled_in(["GOOGLE_API_KEY"])
            _shared_client = genai.Client(api_key=config.GOOGLE_API_KEY)
    return _shared_client


def send_one_request_to_the_model(
    prompt_text: str,
    expect_json: bool,
    response_schema=None,
    model_name: str | None = None,
) -> str:
    """
    Send a prompt and return the raw text of the answer. No caching, no retries.

    Everything else in this file is about not having to call this.
    """
    client = get_model_client()

    request_settings = None
    if expect_json or response_schema is not None:
        # Asking the provider to guarantee the shape is far more reliable than
        # asking the model nicely in the prompt and repairing what comes back.
        # With a schema attached the provider enforces the field names and
        # types for us, which removes most of the parsing failures a free-text
        # JSON answer would produce.
        request_settings = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
        )

    response = client.models.generate_content(
        model=model_name or config.GEMINI_MODEL,
        contents=prompt_text,
        config=request_settings,
    )

    answer = response.text
    if answer is None:
        # An empty answer usually means the model refused or the response was
        # cut off by a safety filter. Say so plainly rather than returning
        # None and letting it fail somewhere further away.
        raise RuntimeError(
            "The model returned no text. This usually means the response was "
            "blocked or stopped early. "
            f"Reason given: {getattr(response, 'prompt_feedback', 'none given')}"
        )
    return answer


def ask_the_model(
    prompt_text: str,
    prompt_version: str | None = None,
    expect_json: bool = False,
    use_the_cache: bool = True,
    response_schema=None,
    model_name: str | None = None,
) -> str:
    """
    Ask the model something, and return its answer as text.

    THIS IS THE ONLY FUNCTION IN THE PROJECT THAT CALLS A LANGUAGE MODEL.

    Checks the cache first. On a miss, waits its turn, sends the request, and
    retries a few times with a growing pause if the failure looks temporary.
    A successful answer is cached before being returned.

    NOTE ON `response_schema` AND THE CACHE: the schema shapes the answer but
    is deliberately not part of the cache key — turning a class into a stable
    key is fragile. Changing a schema therefore means bumping the caller's
    `prompt_version`, exactly as changing the prompt text does. Every caller
    that passes a schema owns a version string for this reason.
    """
    global _how_many_answers_came_from_the_cache
    global _how_many_requests_we_actually_sent
    global _how_many_requests_failed_for_good

    if prompt_version is None:
        prompt_version = config.EXTRACTION_PROMPT_VERSION

    # The model name is part of the cache key, so a prompt answered by one
    # model is never served up as though a different model had answered it.
    model_to_use = model_name or config.GEMINI_MODEL
    cache_key = build_cache_key(prompt_text, model_to_use, prompt_version)

    if use_the_cache:
        answer_we_already_have = read_cached_answer(cache_key)
        if answer_we_already_have is not None:
            with _counter_lock:
                _how_many_answers_came_from_the_cache += 1
            return answer_we_already_have

    seconds_to_wait = SECONDS_TO_WAIT_BEFORE_FIRST_RETRY
    last_problem = None
    how_many_attempts_we_made = 0

    for attempt_number in range(HOW_MANY_TIMES_TO_RETRY + 1):
        how_many_attempts_we_made += 1
        try:
            wait_our_turn_before_sending()
            answer = send_one_request_to_the_model(
                prompt_text, expect_json, response_schema, model_to_use
            )

            with _counter_lock:
                _how_many_requests_we_actually_sent += 1

            write_answer_to_cache(cache_key, prompt_text, answer, model_to_use)
            return answer

        except Exception as problem:
            last_problem = problem

            if not a_failure_is_worth_retrying(problem):
                # A malformed request or a bad key will fail identically every
                # time. Retrying only spends more of the daily allowance.
                break

            if attempt_number == HOW_MANY_TIMES_TO_RETRY:
                break

            time.sleep(seconds_to_wait)
            seconds_to_wait *= 2

    with _counter_lock:
        _how_many_requests_failed_for_good += 1

    # Report the attempts we actually made, not the maximum we allow. A
    # permanent failure stops after one try, and saying "gave up after 5
    # attempts" would send you hunting for a flakiness problem that is not
    # there — as it did the first time this ran.
    raise RuntimeError(
        f"Gave up after {how_many_attempts_we_made} "
        f"attempt{'s' if how_many_attempts_we_made != 1 else ''}. "
        f"Last problem was {type(last_problem).__name__}: {last_problem}"
    )


def check_the_api_key_works() -> tuple[bool, str]:
    """
    Make one tiny real request to prove the key and the model name are right.

    Worth doing before a long run. Discovering a bad key after twenty minutes
    of chunking is a bad way to spend twenty minutes.
    """
    try:
        answer = ask_the_model(
            "Reply with exactly the word: ready",
            prompt_version="api-key-check",
            use_the_cache=False,
        )
        return True, answer.strip()[:80]
    except Exception as problem:
        return False, f"{type(problem).__name__}: {problem}"


# ===========================================================================
# Running it
# ===========================================================================

def main() -> int:
    print("=" * 78)
    print("LLM CLIENT — self check")
    print("=" * 78)

    print(f"\nmodel        : {config.GEMINI_MODEL}")
    print(f"api key      : {config.hide_middle_of_secret(config.GOOGLE_API_KEY)}")
    print(f"rate limit   : {config.LLM_REQUESTS_PER_MINUTE} requests per minute")
    print(f"cache folder : {config.LLM_CACHE_FOLDER}")
    print(f"cache now    : {describe_the_cache()}")

    print("\n1. Sending one real request to check the key and model name...")
    key_works, what_came_back = check_the_api_key_works()
    if not key_works:
        print(f"   FAILED — {what_came_back}\n")
        print("   Most likely causes:")
        print("     - GOOGLE_API_KEY in .env is wrong or expired")
        print("     - GEMINI_MODEL names a model your key cannot use")
        print("     - you are out of quota for today")
        print("   Get a key at https://aistudio.google.com")
        return 1
    print(f"   OK — the model replied: {what_came_back!r}")

    print("\n2. Checking the cache actually caches...")
    reset_the_counters()
    a_question = "In one short sentence, what is a footnote in a document?"

    ask_the_model(a_question, prompt_version="self-check")
    requests_after_the_first_ask = how_many_requests_we_actually_sent()

    ask_the_model(a_question, prompt_version="self-check")
    requests_after_the_second_ask = how_many_requests_we_actually_sent()

    if requests_after_the_second_ask == requests_after_the_first_ask:
        print("   OK — asking the same thing twice sent only one request.")
    else:
        print("   PROBLEM — the second identical question was sent again.")
        print("   The cache is not working, and a full run will burn the daily quota.")
        return 1

    print("\n3. Checking a changed prompt version bypasses the cache...")
    requests_before = how_many_requests_we_actually_sent()
    ask_the_model(a_question, prompt_version="self-check-different-version")
    if how_many_requests_we_actually_sent() > requests_before:
        print("   OK — a new prompt version correctly ignored the old answer.")
    else:
        print("   PROBLEM — a new prompt version reused a stale answer.")
        print("   Improving the extraction prompt would appear to change nothing.")
        return 1

    print(f"\n{describe_what_this_run_cost()}")
    print(f"cache now : {describe_the_cache()}")
    print("\nSUCCESS — the model is reachable and the cache behaves correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
