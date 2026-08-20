"""The versioned code-owned compliance policy (spec 3G).

A rule change requires a code change, tests, a new version string, and
reassessment of affected candidates. Prompts and AI can never change these
rules. Unrecognized vocabulary is unknown — never silently blocked or passed.
Carrier free-text notes are context only and are never an input here.
"""

from datetime import date

POLICY_VERSION = "goodlane_demo_eligibility_v1"

_AUTHORITY_PASS = {"ACTIVE"}
_AUTHORITY_FAIL = {"INACTIVE", "REVOKED", "SUSPENDED"}
_SAFETY_PASS = {"Satisfactory"}
_SAFETY_FAIL = {"Unsatisfactory"}


def assess_authority(raw: str | None) -> str:
    if raw in _AUTHORITY_PASS:
        return "pass"
    if raw in _AUTHORITY_FAIL:
        return "fail"
    # CONDITIONAL, null, and anything unrecognized: the carrier may operate but
    # requires review.
    return "unknown"


def assess_safety(raw: str | None) -> str:
    if raw in _SAFETY_PASS:
        return "pass"
    if raw in _SAFETY_FAIL:
        return "fail"
    return "unknown"


def assess_insurance(expiry: date | None, pickup_date: date) -> str:
    """Compared with the load pickup date — never the system or demo clock."""
    if expiry is None:
        return "unknown"
    return "pass" if expiry >= pickup_date else "fail"


def compliance_overall(*component_results: str) -> str:
    if "fail" in component_results:
        return "fail"
    if "unknown" in component_results:
        return "needs_review"
    return "pass"
