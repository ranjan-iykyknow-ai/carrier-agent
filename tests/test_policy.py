"""The versioned code-owned compliance policy (spec 3G): goodlane_demo_eligibility_v1."""

from datetime import date

from apps.candidates.policy import (
    POLICY_VERSION,
    assess_authority,
    assess_insurance,
    assess_safety,
    compliance_overall,
)


class TestAuthority:
    def test_active_passes(self):
        assert assess_authority("ACTIVE") == "pass"

    def test_inactive_revoked_suspended_fail(self):
        for value in ("INACTIVE", "REVOKED", "SUSPENDED"):
            assert assess_authority(value) == "fail"

    def test_conditional_is_unknown_never_silently_blocked_or_passed(self):
        assert assess_authority("CONDITIONAL") == "unknown"

    def test_null_and_unrecognized_are_unknown(self):
        assert assess_authority(None) == "unknown"
        assert assess_authority("PENDING-WEIRD-VALUE") == "unknown"


class TestSafety:
    def test_satisfactory_passes(self):
        assert assess_safety("Satisfactory") == "pass"

    def test_unsatisfactory_fails(self):
        assert assess_safety("Unsatisfactory") == "fail"

    def test_conditional_null_and_unrecognized_are_unknown(self):
        assert assess_safety("Conditional") == "unknown"
        assert assess_safety(None) == "unknown"
        assert assess_safety("Probationary") == "unknown"


class TestInsurance:
    def test_expiry_on_pickup_date_passes(self):
        assert assess_insurance(date(2026, 5, 23), date(2026, 5, 23)) == "pass"

    def test_expiry_after_pickup_passes(self):
        assert assess_insurance(date(2027, 1, 15), date(2026, 5, 23)) == "pass"

    def test_expiry_before_pickup_fails(self):
        # Blue Ridge: expired 2026-05-15, pickup 2026-05-23.
        assert assess_insurance(date(2026, 5, 15), date(2026, 5, 23)) == "fail"

    def test_missing_expiry_is_unknown(self):
        assert assess_insurance(None, date(2026, 5, 23)) == "unknown"


class TestOverall:
    def test_any_failure_fails(self):
        assert compliance_overall("pass", "pass", "fail") == "fail"
        assert compliance_overall("fail", "unknown", "pass") == "fail"

    def test_unknown_without_failure_needs_review(self):
        assert compliance_overall("unknown", "pass", "pass") == "needs_review"

    def test_all_passing_passes(self):
        assert compliance_overall("pass", "pass", "pass") == "pass"

    def test_policy_version_is_pinned(self):
        assert POLICY_VERSION == "goodlane_demo_eligibility_v1"
