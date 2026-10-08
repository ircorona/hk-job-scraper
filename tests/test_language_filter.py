"""Chinese-language requirement detection.

The user speaks Spanish (native) and English (bilingual), with HSK1 Mandarin and no
Cantonese, and will not overclaim a language. Any role that hard-requires Chinese is
a dead end, so it must be detected and filtered out rather than recommended.

The original detector only matched the literal words "mandarin" and "putonghua". It
missed CANTONESE completely - the actual blocker - and missed the standard HK phrasing
"good command of both written and spoken English and Chinese". AEON Credit's Manager,
Fraud and Authorization was recommended to the user before anyone read that line.
"""
import pytest

from utils.analysis import detect_mandarin


# --- the ones that burned us -------------------------------------------------

def test_aeon_phrasing_is_required():
    """The exact line from AEON Credit that made the role a dead end."""
    jd = ("Good command of both written and spoken English and Chinese "
          "(Mandarin and Cantonese).")
    assert detect_mandarin(jd) == "Required"


def test_cantonese_alone_is_detected():
    """Cantonese was invisible to the old detector."""
    assert detect_mandarin("Fluent Cantonese speaker required.") == "Required"


def test_cantonese_without_the_word_mandarin():
    jd = "Candidates must be able to communicate in Cantonese with local suppliers."
    assert detect_mandarin(jd) == "Required"


def test_chinese_without_naming_a_dialect():
    jd = "Excellent communication skills in English and Chinese."
    assert detect_mandarin(jd) == "Required"


def test_soft_cue_in_previous_bullet_does_not_soften():
    """Robert Walters 95085886: the 'advantage' belonged to the banking bullet above,
    yet the role showed as 'Chinese preferred' and reached Apply first."""
    jd = ("Strong communication, presentation and stakeholder management skills.\n"
          "Banking or financial services experience would be an advantage.\n"
          "Fluent in written and spoken Chinese and English.")
    assert detect_mandarin(jd) == "Required"


# --- word order must not matter ---------------------------------------------

@pytest.mark.parametrize("jd", [
    "Fluent in Mandarin",
    "Mandarin fluency is required",
    "Proficiency in Putonghua",
    "Native Cantonese speaker",
    "Must be able to speak Mandarin",
])
def test_required_regardless_of_word_order(jd):
    assert detect_mandarin(jd) == "Required", f"missed: {jd}"


# --- preferred is not the same as required ----------------------------------

@pytest.mark.parametrize("jd", [
    "Mandarin is preferred but not essential",
    "Cantonese would be an advantage",
    "Knowledge of Chinese is a plus",
])
def test_preferred_is_not_required(jd):
    assert detect_mandarin(jd) == "Preferred", f"over-flagged: {jd}"


# --- must not fire on non-language mentions ---------------------------------

@pytest.mark.parametrize("jd", [
    "Supporting our Greater China region operations.",
    "Experience with the China market is useful.",
    "Reporting into Bank of China (Hong Kong).",
])
def test_market_and_company_mentions_are_not_language_requirements(jd):
    assert detect_mandarin(jd) != "Required", f"false positive: {jd}"


def test_english_only_job_is_clean():
    jd = ("Excellent communication skills in English (written). Ability to work "
          "independently with business stakeholders, IT teams and vendors.")
    assert detect_mandarin(jd) == ""


def test_empty_input():
    assert detect_mandarin("") == ""
    assert detect_mandarin(None) == ""
