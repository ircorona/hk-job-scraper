"""
Shared analysis utilities — skill extraction, language detection, salary parsing.
Extracted from export_excel.py so both generate_data.py and export_excel.py
can import without circular dependencies.
"""

import re
import pandas as pd

# ==============================================================================
# SKILLS (HK market)
# ==============================================================================

HARD_SKILLS = {
    "excel", "power bi", "powerbi", "tableau", "looker", "qlik", "sap", "erp", "crm", "salesforce",
    "python", "sql", "r", "java", "javascript", "typescript", "html", "css", "react", "node.js",
    "angular", "vue", "golang", "go", "c++", "c#", ".net", "swift", "kotlin",
    "mysql", "postgresql", "mongodb", "snowflake", "bigquery", "azure", "aws", "gcp",
    "machine learning", "deep learning", "ai", "nlp", "airflow", "dbt", "git", "docker", "kubernetes",
    "terraform", "jenkins", "ci/cd", "api", "rest", "graphql", "microservices",
    "figma", "photoshop", "illustrator", "sketch",
    "seo", "sem", "google ads", "meta ads", "facebook ads",
    "bloomberg", "reuters", "murex", "calypso", "swift messaging",
    "compliance", "aml", "kyc", "risk management", "ifrs", "gaap",
    "hubspot", "jira", "confluence",
    "scrum", "agile", "prince2", "pmp",
    "dashboard", "kpi", "reporting", "bi",
}

SOFT_SKILLS = {"leadership", "teamwork", "communication", "negotiation", "presentation", "analytical"}

# Chinese-language requirement detection.
#
# The field is still called "mandarin" for frontend compatibility, but it now means
# "this job wants a Chinese language". The old version only looked for the literal
# words "mandarin" and "putonghua", so it missed CANTONESE entirely - which is the
# actual blocker for this user - and missed the very common HK phrasing "written and
# spoken English and Chinese". It also required the word order "mandarin required",
# so "fluent in Mandarin" fell through to a vague 'Yes'. Result: 70 of 1,515 jobs
# flagged, every one of them 'Yes', and roles that hard-require Cantonese sailed
# through as clean.
CHINESE_TERMS = (
    r'(?:mandarin|cantonese|putonghua|guangdonghua|'
    r'chinese|中文|普通話|普通话|廣東話|广东话|粵語|粤语)'
)
# Cues that make it a hard requirement. Checked in a window around the mention, so
# word order does not matter ("fluent in Cantonese" and "Cantonese fluency" both hit).
REQUIRED_CUES = (
    r'(?:required|requirement|mandatory|must\s+(?:be|have|possess)|essential|'
    r'fluen\w*|proficien\w*|native|command of|competent|business[- ]level|'
    r'able to (?:speak|communicate)|speak and write|written and spoken|'
    r'spoken and written|bilingual|trilingual|'
    # HK job ads very often phrase it as a plain skills line with no cue verb:
    # "Excellent communication skills in English and Chinese"
    r'communication skills|language skills|verbal and written|'
    r'speak\w*|written|spoken|read and write)'
)
# "Mandarin is preferred but NOT ESSENTIAL" must not trip the required cue.
NEGATED_REQUIRED = (
    r'not\s+(?:strictly\s+)?(?:essential|required|mandatory|a\s+requirement|necessary)'
)
PREFERRED_CUES = (
    r'(?:preferred|preferable|advantageous|desirable|an advantage|a plus|'
    r'nice to have|welcome|beneficial|would be|is a plus|bonus)'
)
# Mentions that are NOT a language requirement at all - company names, markets,
# region descriptors. Without these, "Greater China region" reads as a language ask.
FALSE_POSITIVE_CUES = (
    r'(?:greater china|china market|mainland china|china team|china office|'
    r'china business|chinese market|china region|china and|bank of china|'
    r'china mobile|china telecom|china unicom|air china|china life)'
)


def _chinese_windows(text, radius=140):
    """Yield text windows around each Chinese-language mention.

    The window stops at line breaks: each bullet of an ad is its own line, and a
    soft cue in the NEIGHBOURING bullet must not soften this one. Robert Walters
    95085886 (2026-10-08) read "Banking ... would be an advantage.\n Fluent in
    written and spoken Chinese and English." and came out 'Preferred'.
    """
    for m in re.finditer(CHINESE_TERMS, text, re.IGNORECASE):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        start = max(line_start, m.start() - radius)
        end = min(line_end, m.end() + radius)
        yield text[start:end], m.group(0)


def extract_keywords(desc, hard_only=False):
    if not desc or pd.isna(desc): return []
    text = str(desc).lower()
    pool = HARD_SKILLS if hard_only else HARD_SKILLS | SOFT_SKILLS
    return sorted(kw for kw in pool if re.search(rf'\b{re.escape(kw)}\b', text))


def detect_mandarin(desc):
    """Return 'Required', 'Preferred', 'Yes' or '' for a Chinese-language ask.

    'Required' is the one that matters: the user speaks Spanish and English with
    HSK1 Mandarin and no Cantonese, and will not overclaim a language. Those roles
    should be filtered out of recommendations entirely, not merely flagged.
    """
    if not desc or pd.isna(desc):
        return ""
    text = str(desc)
    if not re.search(CHINESE_TERMS, text, re.IGNORECASE):
        return ""

    best = ""
    for window, term in _chinese_windows(text):
        # "Greater China market" / "Bank of China" are not language requirements
        if term.lower() in ("chinese", "china") and \
           re.search(FALSE_POSITIVE_CUES, window, re.IGNORECASE):
            continue

        # Drop explicit negations first, so "preferred but not essential" cannot
        # trip the required cue on the word "essential".
        cleaned = re.sub(NEGATED_REQUIRED, " ", window, flags=re.IGNORECASE)
        has_required = bool(re.search(REQUIRED_CUES, cleaned, re.IGNORECASE))
        has_preferred = bool(re.search(PREFERRED_CUES, window, re.IGNORECASE))

        if has_preferred and not re.search(
                r'(?:required|mandatory|must\b)', cleaned, re.IGNORECASE):
            # softly-worded ask, e.g. "Cantonese would be an advantage"
            best = best or "Preferred"
            continue
        if has_required:
            return "Required"
        best = best or "Yes"
    return best


def extract_max_salary(sal):
    """Extract max monthly salary in HKD from salary string."""
    if not sal or pd.isna(sal): return 0
    text = str(sal).replace(",", "").replace("HK$", "").replace("HK", "")
    nums = re.findall(r'\d+', text)
    vals = [int(n) for n in nums if 5000 < int(n) < 500000]
    return max(vals) if vals else 0


def clean_salary(sal, median):
    if not sal or pd.isna(sal):
        return f"~HK${median:,} (estimated)"
    s = str(sal)
    if "negotiable" in s.lower() or "competitive" in s.lower():
        return f"~HK${median:,} (estimated)"
    if extract_max_salary(s) < 5000:
        return f"~HK${median:,} (estimated)"
    return s
