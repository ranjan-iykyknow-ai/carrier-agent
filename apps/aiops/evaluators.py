"""Deterministic offline evaluators (spec 3I).

Every metric is an explainable pass/fail (or explicitly not-applicable) against
hand-labelled content truth, tagged with a controlled failure category. Pure
functions: the runner supplies any directory facts they need — no queries and
no providers happen here, so CI exercises them with fixtures alone.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_DIGITS = re.compile(r"\D")

# Content-truth synonym map, mirroring the gold-label instructions. Kept
# code-owned and DB-free so the evaluator is pure.
_EQUIPMENT_SYNONYMS = {
    "box_truck": ("box truck", "box_truck", "straight truck", "26ft box", "box"),
    "sprinter_van": ("sprinter", "cargo van", "sprinter_van", "van"),
    "flatbed": ("flatbed", "flat bed"),
    "refrigerated": ("reefer", "refrigerated", "refer truck"),
}

CATEGORIES = {
    "intent": "intent_classification",
    "availability": "availability",
    "equipment": "equipment_normalization",
    "load_reference": "identifier_extraction",
    "mc_number": "identifier_extraction",
    "rate_value": "rate_extraction",
    "rate_role": "rate_role_confusion",
    "grounding_rate": "evidence_grounding",
    "carrier_resolution": "entity_resolution",
    "load_resolution": "entity_resolution",
}

GROUNDING_PASS_THRESHOLD = 0.75


@dataclass(frozen=True)
class FieldScore:
    metric: str
    passed: bool | None  # None = not applicable for this case
    expected: str
    actual: str
    category: str
    value: float | None = None  # numeric metrics (rates/ratios)


def _score(metric, passed, expected, actual, value=None) -> FieldScore:
    return FieldScore(
        metric=metric,
        passed=passed,
        expected=str(expected),
        actual=str(actual),
        category=CATEGORIES[metric],
        value=value,
    )


def _digits(value) -> str | None:
    if not value:
        return None
    normalized = _DIGITS.sub("", str(value))
    return normalized or None


def normalize_equipment(raw) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().casefold()
    for code, synonyms in _EQUIPMENT_SYNONYMS.items():
        if any(synonym in text for synonym in synonyms):
            return code
    return None


def _amounts_equal(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError):
        return False


def _carrier_positions(item) -> list[dict]:
    return [
        rate
        for rate in item.get("rates") or []
        if rate.get("role") in ("carrier_quote", "carrier_counteroffer")
    ]


def evaluate_extraction(item: dict, labels: dict) -> list[FieldScore]:
    """Score one extraction proposal item against its gold labels."""
    scores = []

    extracted_intent = (item.get("intents") or [None])[0]
    scores.append(
        _score(
            "intent",
            extracted_intent == labels["primary_intent"],
            labels["primary_intent"],
            extracted_intent,
        )
    )

    scores.append(
        _score(
            "availability",
            item.get("availability") == labels["availability"],
            labels["availability"],
            item.get("availability"),
        )
    )

    extracted_equipment = normalize_equipment(item.get("equipment"))
    scores.append(
        _score(
            "equipment",
            extracted_equipment == labels["equipment_code"],
            labels["equipment_code"],
            f"{item.get('equipment')} -> {extracted_equipment}",
        )
    )

    scores.append(
        _score(
            "load_reference",
            _digits(item.get("load_reference")) == labels["load_reference_digits"],
            labels["load_reference_digits"],
            item.get("load_reference"),
        )
    )

    if labels.get("mc_stated_but_garbled"):
        # A garbled MC has no single right answer; never punish either reading.
        scores.append(_score("mc_number", None, "garbled", item.get("mc_number")))
    else:
        scores.append(
            _score(
                "mc_number",
                _digits(item.get("mc_number")) == labels["mc_number_digits"],
                labels["mc_number_digits"],
                item.get("mc_number"),
            )
        )

    positions = _carrier_positions(item)
    expected_position = labels.get("current_carrier_position")
    if expected_position is None:
        value_passed = not positions
        scores.append(
            _score(
                "rate_value",
                value_passed,
                "no carrier position",
                [p.get("amount") for p in positions],
            )
        )
        scores.append(_score("rate_role", None, "n/a", "n/a"))
    else:
        last = positions[-1] if positions else None
        value_passed = last is not None and _amounts_equal(
            last.get("amount"), expected_position["amount"]
        )
        scores.append(
            _score(
                "rate_value",
                value_passed,
                expected_position["amount"],
                last.get("amount") if last else None,
            )
        )
        if value_passed:
            scores.append(
                _score(
                    "rate_role",
                    last.get("role") == expected_position["role"],
                    expected_position["role"],
                    last.get("role"),
                )
            )
        else:
            scores.append(_score("rate_role", None, expected_position["role"], "unscored"))

    # Grounding: of the facts the model DID extract for label-stated fields,
    # how many carry an evidence reference?
    checks = []
    if labels["mc_number_digits"] and item.get("mc_number"):
        checks.append(item.get("mc_number_evidence") is not None)
    if labels["load_reference_digits"] and item.get("load_reference"):
        checks.append(item.get("load_reference_evidence") is not None)
    if labels["equipment_code"] and item.get("equipment"):
        checks.append(item.get("equipment_evidence") is not None)
    if labels["availability"] != "not_stated" and item.get("availability") != "not_stated":
        checks.append(item.get("availability_evidence") is not None)
    if checks:
        rate = sum(checks) / len(checks)
        scores.append(
            _score(
                "grounding_rate",
                rate >= GROUNDING_PASS_THRESHOLD,
                f">= {GROUNDING_PASS_THRESHOLD}",
                f"{rate:.2f}",
                value=rate,
            )
        )
    else:
        scores.append(_score("grounding_rate", None, "n/a", "no gradeable fields"))
    return scores


def evaluate_resolution(
    *, carrier_status, load_status, labels, mc_in_directory, load_in_directory
) -> list[FieldScore]:
    """Outcome-level checks: did stated identifiers resolve the way the
    directory says they must? Expectations are derived from labels plus
    directory existence, so a missed extraction surfaces here too."""
    scores = []

    if labels["mc_number_digits"] and mc_in_directory:
        scores.append(
            _score(
                "carrier_resolution",
                carrier_status == "verified",
                "verified",
                carrier_status,
            )
        )
    else:
        # Without a stated in-directory MC the carrier may still verify by
        # envelope email or phone — no deterministic expectation exists.
        scores.append(_score("carrier_resolution", None, "n/a", carrier_status))

    if labels["load_reference_digits"]:
        if load_in_directory:
            scores.append(
                _score("load_resolution", load_status == "verified", "verified", load_status)
            )
        else:
            scores.append(
                _score(
                    "load_resolution",
                    load_status != "verified",
                    "not verified (reference unknown)",
                    load_status,
                )
            )
    else:
        scores.append(
            _score("load_resolution", load_status == "unmatched", "unmatched", load_status)
        )
    return scores


def aggregate_metrics(cases: list[list[FieldScore]]) -> dict:
    """Per-metric accuracy over applicable cases, plus one overall score."""
    metrics: dict[str, dict] = {}
    for scores in cases:
        for score in scores:
            row = metrics.setdefault(score.metric, {"passed": 0, "failed": 0, "not_applicable": 0})
            if score.passed is None:
                row["not_applicable"] += 1
            elif score.passed:
                row["passed"] += 1
            else:
                row["failed"] += 1
    accuracies = []
    for row in metrics.values():
        graded = row["passed"] + row["failed"]
        row["accuracy"] = round(row["passed"] / graded, 4) if graded else None
        if row["accuracy"] is not None:
            accuracies.append(row["accuracy"])
    return {
        "metrics": metrics,
        "score": round(sum(accuracies) / len(accuracies), 4) if accuracies else None,
    }
