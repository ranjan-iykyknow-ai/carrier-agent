"""Read-only assistant tools (spec 3H).

Tool schemas, authorization, scope checks, result limits, and queries are
typed application code — never prompt-controlled. Every result returns bounded
structured facts plus the stable record identifiers that citation validation
accepts. No tool can write, send, book, or change compliance.
"""

import re
from dataclasses import dataclass, field

from django.db.models import Q

from apps.candidates.models import CarrierLoadCandidate
from apps.candidates.selectors import best_rates, market_context, offered_per_mile
from apps.freight.models import Carrier, Load
from apps.inquiries.models import Inquiry

_DIGITS = re.compile(r"\D")

SEARCH_LIMIT = 10
HISTORY_LIMIT = 15


class ToolFailure(Exception):
    def __init__(self, code: str, summary: str):
        super().__init__(summary)
        self.code = code
        self.summary = summary


@dataclass(frozen=True)
class ToolScope:
    snapshot: object
    load: object | None  # a load-scoped conversation may only read its own load


@dataclass
class ToolResult:
    summary: str
    facts: dict
    record_ids: list = field(default_factory=list)


def _require(arguments: dict, *names):
    values = []
    for name in names:
        value = arguments.get(name)
        if value in (None, ""):
            raise ToolFailure("invalid_arguments", f"missing required argument {name!r}")
        if not isinstance(value, str):
            raise ToolFailure("invalid_arguments", f"argument {name!r} must be a string")
        values.append(value.strip())
    return values if len(values) > 1 else values[0]


def _find_load(scope: ToolScope, external_load_id: str) -> Load:
    reference = _DIGITS.sub("", external_load_id) or external_load_id
    if scope.load is not None and scope.load.external_load_id != reference:
        raise ToolFailure("scope_denied", "this conversation is scoped to a single load; ask there")
    load = (
        Load.objects.filter(dataset_snapshot=scope.snapshot, external_load_id=reference)
        .select_related("equipment_type", "lane")
        .first()
    )
    if load is None:
        raise ToolFailure("not_found", f"no load {external_load_id!r} in the active snapshot")
    return load


def _find_carrier(scope: ToolScope, arguments: dict) -> Carrier:
    mc = _DIGITS.sub("", arguments.get("mc_number") or "")
    name = (arguments.get("company_name") or "").strip()
    queryset = Carrier.objects.filter(dataset_snapshot=scope.snapshot)
    carrier = None
    if mc:
        carrier = queryset.filter(mc_number_normalized=mc).first()
    if carrier is None and name:
        carrier = (
            queryset.filter(company_name__iexact=name).first()
            or queryset.filter(company_name__icontains=name).first()
        )
    if carrier is None:
        raise ToolFailure("not_found", "no carrier matches the given identity")
    return carrier


def _quote_facts(quote, load=None):
    if quote is None:
        return None
    return {
        "amount": str(quote.amount),
        "basis": quote.rate_basis,
        "role": quote.quote_type,
        "quote_id": str(quote.id),
    }


def _inquiry_row(inquiry):
    quote = next((q for q in inquiry.quotes.all() if q.is_current), None)
    return {
        "inquiry_id": str(inquiry.id),
        "carrier": inquiry.carrier.company_name if inquiry.carrier else None,
        "intent": inquiry.primary_intent,
        "availability": inquiry.availability_status,
        "review_status": inquiry.review_status,
        "summary": inquiry.summary[:300],
        "current_quote": _quote_facts(quote),
    }


def _get_load(scope, arguments):
    load = _find_load(scope, _require(arguments, "external_load_id"))
    context = market_context(load)
    per_mile = offered_per_mile(load)
    rates = best_rates(load)
    facts = {
        "external_load_id": load.external_load_id,
        "status": load.status,
        "origin": f"{load.origin_city}, {load.origin_state}",
        "destination": f"{load.destination_city}, {load.destination_state}",
        "equipment": load.equipment_type.display_name
        if load.equipment_type
        else load.equipment_type_raw,
        "pickup_date": str(load.pickup_date),
        "weight_lbs": load.weight_lbs,
        "distance_miles": str(load.distance_miles) if load.distance_miles else None,
        "offered_rate_usd": str(load.offered_rate_usd) if load.offered_rate_usd else None,
        "offered_per_mile": str(per_mile) if per_mile else None,
        # The deterministic 3G band position is the only allowed market judgment.
        "market_band_position": context.band_position if context else None,
        "lowest_overall_quote": str(rates.lowest_overall.all_in) if rates.lowest_overall else None,
        "lowest_eligible_quote": str(rates.lowest_eligible.all_in)
        if rates.lowest_eligible
        else None,
    }
    record_ids = [f"load:{load.external_load_id}"]
    if context:
        record_ids.append(f"market:{context.history.id}")
    return ToolResult(
        summary=f"load {load.external_load_id} facts with market band",
        facts=facts,
        record_ids=record_ids,
    )


def _get_load_inquiries(scope, arguments):
    load = _find_load(scope, _require(arguments, "external_load_id"))
    inquiries = (
        Inquiry.objects.filter(load=load)
        .exclude(review_status="rejected")
        .select_related("carrier")
        .prefetch_related("quotes")
        .order_by("-created_at")[:SEARCH_LIMIT]
    )
    rows = [_inquiry_row(inquiry) for inquiry in inquiries]
    return ToolResult(
        summary=f"{len(rows)} inquiries on load {load.external_load_id}",
        facts={"inquiries": rows},
        record_ids=[f"load:{load.external_load_id}"]
        + [f"inquiry:{row['inquiry_id']}" for row in rows],
    )


def _search_inquiries(scope, arguments):
    query = _require(arguments, "query")
    inquiries = Inquiry.objects.filter(
        communication_event__dataset_snapshot=scope.snapshot
    ).exclude(review_status="rejected")
    if scope.load is not None:
        inquiries = inquiries.filter(load=scope.load)
    inquiries = (
        inquiries.filter(
            Q(summary__icontains=query)
            | Q(carrier__company_name__icontains=query)
            | Q(load__external_load_id__icontains=query)
            | Q(primary_intent__icontains=query)
        )
        .select_related("carrier")
        .prefetch_related("quotes")
        .order_by("-created_at")[:SEARCH_LIMIT]
    )
    rows = [_inquiry_row(inquiry) for inquiry in inquiries]
    return ToolResult(
        summary=f"{len(rows)} inquiries matching {query!r}",
        facts={"inquiries": rows},
        record_ids=[f"inquiry:{row['inquiry_id']}" for row in rows],
    )


def _get_carrier_profile(scope, arguments):
    carrier = _find_carrier(scope, arguments)
    facts = {
        "carrier_id": str(carrier.id),
        "company_name": carrier.company_name,
        "mc_number": carrier.mc_number_raw,
        "dot_number": carrier.dot_number_raw,
        "authority_status": carrier.authority_status,
        "safety_rating": carrier.safety_rating,
        "insurance_expiry": str(carrier.insurance_expiry) if carrier.insurance_expiry else None,
        "onboarded": carrier.onboarded,
        "reliability_score": str(carrier.reliability_score)
        if carrier.reliability_score is not None
        else None,
        "loads_completed_with_goodlane": carrier.loads_completed_with_goodlane,
        "avg_response_time_hours": str(carrier.avg_response_time_hours)
        if carrier.avg_response_time_hours is not None
        else None,
    }
    return ToolResult(
        summary=f"profile for {carrier.company_name or 'unnamed carrier'}",
        facts=facts,
        record_ids=[f"carrier:{carrier.id}"],
    )


def _get_carrier_history(scope, arguments):
    carrier = _find_carrier(scope, arguments)
    inquiries = (
        Inquiry.objects.filter(carrier=carrier)
        .exclude(review_status="rejected")
        .select_related("load", "carrier")
        .prefetch_related("quotes")
        .order_by("-created_at")[:HISTORY_LIMIT]
    )
    rows = []
    for inquiry in inquiries:
        row = _inquiry_row(inquiry)
        row["load"] = inquiry.load.external_load_id if inquiry.load else None
        rows.append(row)
    return ToolResult(
        summary=f"{len(rows)} communications from {carrier.company_name or 'carrier'}",
        facts={"history": rows},
        record_ids=[f"carrier:{carrier.id}"] + [f"inquiry:{row['inquiry_id']}" for row in rows],
    )


def _get_candidate_assessment(scope, arguments):
    load = _find_load(scope, _require(arguments, "external_load_id"))
    carrier = _find_carrier(scope, arguments)
    candidate = (
        CarrierLoadCandidate.objects.filter(carrier=carrier, load=load)
        .select_related("current_eligibility_assessment", "current_quote")
        .prefetch_related("current_eligibility_assessment__reasons")
        .first()
    )
    if candidate is None:
        raise ToolFailure("not_found", "this carrier is not a candidate on that load")
    assessment = candidate.current_eligibility_assessment
    facts = {
        "carrier": carrier.company_name,
        "load": load.external_load_id,
        "final_status": assessment.final_status if assessment else None,
        "reasons": [
            {"code": reason.code, "severity": reason.severity, "details": reason.details}
            for reason in (assessment.reasons.all() if assessment else [])
        ],
        "current_quote": _quote_facts(candidate.current_quote),
    }
    record_ids = [f"carrier:{carrier.id}", f"load:{load.external_load_id}"]
    if assessment:
        record_ids.append(f"assessment:{assessment.id}")
    if candidate.current_quote_id:
        record_ids.append(f"quote:{candidate.current_quote_id}")
    return ToolResult(
        summary=f"eligibility of {carrier.company_name} on {load.external_load_id}",
        facts=facts,
        record_ids=record_ids,
    )


def _get_market_rate_context(scope, arguments):
    load = _find_load(scope, _require(arguments, "external_load_id"))
    context = market_context(load)
    if context is None:
        return ToolResult(
            summary="no market history for this exact lane and equipment",
            facts={"available": False},
            record_ids=[f"load:{load.external_load_id}"],
        )
    history = context.history
    facts = {
        "available": True,
        "lane": str(load.lane),
        "week_start": str(history.week_start),
        "minimum_rate_per_mile": str(history.minimum_rate_per_mile),
        "average_rate_per_mile": str(history.average_rate_per_mile),
        "maximum_rate_per_mile": str(history.maximum_rate_per_mile),
        "offered_per_mile": str(offered_per_mile(load)) if offered_per_mile(load) else None,
        "band_position": context.band_position,
    }
    return ToolResult(
        summary=f"market band for load {load.external_load_id}",
        facts=facts,
        record_ids=[f"load:{load.external_load_id}", f"market:{history.id}"],
    )


_HANDLERS = {
    "get_load": _get_load,
    "get_load_inquiries": _get_load_inquiries,
    "search_inquiries": _search_inquiries,
    "get_carrier_profile": _get_carrier_profile,
    "get_carrier_history": _get_carrier_history,
    "get_candidate_assessment": _get_candidate_assessment,
    "get_market_rate_context": _get_market_rate_context,
}

_LOAD_ARG = {"type": "string", "description": "The load's external id, e.g. 29372450"}
_CARRIER_ARGS = {
    "mc_number": {"type": "string", "description": "Carrier MC number if known"},
    "company_name": {"type": "string", "description": "Carrier company name if known"},
}


def _schema(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def tool_schemas() -> list[dict]:
    return [
        _schema(
            "get_load",
            "Facts for one load: route, equipment, pickup, offered rate, deterministic "
            "market band position, lowest overall and lowest eligible quotes.",
            {"external_load_id": _LOAD_ARG},
            ["external_load_id"],
        ),
        _schema(
            "get_load_inquiries",
            "All carrier inquiries touching one load, with availability, review state, "
            "and current quotes.",
            {"external_load_id": _LOAD_ARG},
            ["external_load_id"],
        ),
        _schema(
            "search_inquiries",
            "Search inquiries by free text across summaries, carrier names, load ids, "
            "and intents. Bounded results.",
            {"query": {"type": "string", "description": "Search terms"}},
            ["query"],
        ),
        _schema(
            "get_carrier_profile",
            "One carrier's compliance and performance profile.",
            _CARRIER_ARGS,
            [],
        ),
        _schema(
            "get_carrier_history",
            "A carrier's communication history across loads.",
            _CARRIER_ARGS,
            [],
        ),
        _schema(
            "get_candidate_assessment",
            "The deterministic eligibility assessment for one carrier on one load, "
            "with reasons and the current quote.",
            {"external_load_id": _LOAD_ARG, **_CARRIER_ARGS},
            ["external_load_id"],
        ),
        _schema(
            "get_market_rate_context",
            "Market rate history and the deterministic band position for a load's "
            "exact lane and equipment.",
            {"external_load_id": _LOAD_ARG},
            ["external_load_id"],
        ),
    ]


def execute_tool(name: str, arguments: dict, scope: ToolScope) -> ToolResult:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolFailure("unknown_tool", f"tool {name!r} does not exist")
    if not isinstance(arguments, dict):
        raise ToolFailure("invalid_arguments", "tool arguments must be an object")
    return handler(scope, arguments)
