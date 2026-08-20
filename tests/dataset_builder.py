"""Builds miniature Goodlane-format dataset packages for seed tests."""

import csv
import io
import json
import wave
from pathlib import Path

DEFAULT_LOADS = [
    {
        "load_id": "29372450",
        "origin_city": "Philadelphia",
        "origin_state": "PA",
        "origin_zip": "19146",
        "destination_city": "New York",
        "destination_state": "NY",
        "destination_zip": "10001",
        "distance_miles": "97",
        "equipment_type": "Box Truck",
        "weight_lbs": "4100",
        "pickup_date": "2026-05-23",
        "pickup_window": "08:00-12:00",
        "delivery_date": "2026-05-23",
        "offered_rate_usd": "420",
        "status": "open",
        "shipper_name": "Jumpstart Athletics",
        "internal_notes": "needs liftgate",
    },
    {
        "load_id": "29372399",
        "origin_city": "Allentown",
        "origin_state": "PA",
        "origin_zip": "18101",
        "destination_city": "Philadelphia",
        "destination_state": "PA",
        "destination_zip": "19103",
        "distance_miles": "60",
        "equipment_type": "Box Truck",
        "weight_lbs": "",
        "pickup_date": "2026-05-22",
        "pickup_window": "morning",
        "delivery_date": "2026-05-22",
        "offered_rate_usd": "250",
        "status": "open",
        "shipper_name": "",
        "internal_notes": "",
    },
    {
        "load_id": "29372501",
        "origin_city": "Camden",
        "origin_state": "NJ",
        "origin_zip": "08103",
        "destination_city": "Baltimore",
        "destination_state": "MD",
        "destination_zip": "21201",
        "distance_miles": "115",
        "equipment_type": "Refrigerated",
        "weight_lbs": "8500",
        "pickup_date": "2026-05-25",
        "pickup_window": "",
        "delivery_date": "2026-05-25",
        "offered_rate_usd": "520",
        "status": "open",
        "shipper_name": "",
        "internal_notes": "temp controlled 34-38F",
    },
]

DEFAULT_CARRIERS = [
    {
        "mc_number": "712843",
        "dot_number": "2987341",
        "company_name": "Blue Ridge Transport LLC",
        "primary_contact": "Carlos Mendez",
        "email": "cmendez@blueridgetransport.com",
        "phone": "610-555-0093",
        "address": "Bethlehem, PA 18015",
        "equipment_types": ["Sprinter Van", "Box Truck"],
        "preferred_lanes": ["PA-NJ", "NJ-NY", "PA-PA"],
        "home_base_zip": "18015",
        "factoring_company": "RTS Financial",
        "payment_terms_preference": "factored",
        "reliability_score": 3.9,
        "loads_completed_with_goodlane": 4,
        "avg_response_time_hours": 1.1,
        "insurance_expiry": "2026-05-15",
        "authority_status": "ACTIVE",
        "safety_rating": "Satisfactory",
        "notes": "INSURANCE EXPIRED - do not book until updated cert received.",
        "onboarded": True,
    },
    {
        # No MC: identity falls back to the namespaced normalized email.
        # No company name either: an unknown name is business data, not an error.
        "mc_number": None,
        "dot_number": None,
        "company_name": None,
        "primary_contact": "Pat Doyle",
        "email": "pat@keystoneoddjobs.com",
        "phone": None,
        "address": "York, PA 17401",
        "equipment_types": ["Box Truck"],
        "preferred_lanes": ["PA-PA"],
        "home_base_zip": "17401",
        "factoring_company": None,
        "payment_terms_preference": "unknown",
        "reliability_score": None,
        "loads_completed_with_goodlane": 0,
        "avg_response_time_hours": None,
        "insurance_expiry": None,
        "authority_status": None,
        "safety_rating": None,
        "notes": "",
        "onboarded": False,
    },
    {
        "mc_number": "1198743",
        "dot_number": None,
        "company_name": "Mountain State Transport",
        "primary_contact": "Rob Galloway",
        "email": "rob@mountainstatetransport.net",
        "phone": "412-555-0344",
        "address": "Pittsburgh, PA 15219",
        "equipment_types": ["Flatbed", "Box Truck"],
        "preferred_lanes": ["PA-PA", "PA-OH"],
        "home_base_zip": "15219",
        "factoring_company": None,
        "payment_terms_preference": "quick_pay",
        "reliability_score": 3.5,
        "loads_completed_with_goodlane": 1,
        "avg_response_time_hours": 3.2,
        "insurance_expiry": "2027-01-15",
        "authority_status": "CONDITIONAL",
        "safety_rating": "Conditional",
        "notes": "CONDITIONAL authority - verify before booking any load.",
        "onboarded": False,
    },
]

DEFAULT_RATES = [
    {
        "week_start": "2026-05-11",
        "origin_state": "PA",
        "destination_state": "NY",
        "equipment_type": "Box Truck",
        "avg_rate_per_mile": "2.94",
        "min_rate_per_mile": "2.59",
        "max_rate_per_mile": "3.47",
        "load_volume": "3",
    },
    {
        "week_start": "2026-05-11",
        "origin_state": "PA",
        "destination_state": "PA",
        "equipment_type": "Box Truck",
        "avg_rate_per_mile": "3.10",
        "min_rate_per_mile": "2.80",
        "max_rate_per_mile": "3.60",
        "load_volume": "5",
    },
]

DEFAULT_EMAILS = [
    {
        "email_id": "CE0058",
        "timestamp": "2026-05-18T14:00:00Z",
        "from_name": "Desmond Okafor",
        "from_email": "desmond@atlanticcarriersinc.com",
        "to_email": "dispatch@goodlanelogistics.com",
        "subject": "Carrier inquiry - load #29372450",
        "body": "Can do. What's the all-in?",
        "mc_number": "567234",
        "load_reference": "29372450",
        "equipment_mentioned": "Box Truck",
        "rate_quoted_usd": None,
        "intent": "terse",
    },
    {
        "email_id": "CE0042",
        "timestamp": "2026-05-18T01:00:00+00:00",
        "from_name": "Priyanka Mehta",
        "from_email": "priyanka@northboundexpress.com",
        "to_email": "dispatch@goodlanelogistics.com",
        "subject": "Re: Load 29372399",
        "body": "We can do this but not at $240. Our floor on this lane is $280.",
        "mc_number": "774321",
        "load_reference": "29372399",
        "equipment_mentioned": "Sprinter Van",
        "rate_quoted_usd": None,
        "intent": "counter",
    },
    {
        "email_id": "CE0074",
        "timestamp": "2026-05-19T09:30:00Z",
        "from_name": "Sandra Pellegrino",
        "from_email": "sandra.p.freight@gmail.com",
        "to_email": "dispatch@goodlanelogistics.com",
        "subject": "Box Truck available",
        "body": "Interested. Box Truck ready. MC#321654. What does it pay?",
        "mc_number": "321654",
        "load_reference": "29372450",
        "equipment_mentioned": "Refrigerated",
        "rate_quoted_usd": None,
        "intent": "terse",
    },
]

DEFAULT_CALLS = ["call_012_rate_negotiation.wav", "call_021_availability_check.wav"]


def write_wav(path: Path, seconds: float = 1.0, rate: int = 8000, tone: int = 1) -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes([tone, 0]) * int(seconds * rate))
    path.write_bytes(buffer.getvalue())


def build_package(
    root: Path,
    *,
    loads=None,
    carriers=None,
    rates=None,
    emails=None,
    calls=None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)

    load_rows = DEFAULT_LOADS if loads is None else loads
    with (root / "loads.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(DEFAULT_LOADS[0].keys()))
        writer.writeheader()
        writer.writerows(load_rows)

    carrier_rows = DEFAULT_CARRIERS if carriers is None else carriers
    (root / "carrier_profiles.json").write_text(json.dumps(carrier_rows, indent=1))

    rate_rows = DEFAULT_RATES if rates is None else rates
    with (root / "rate_history.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(DEFAULT_RATES[0].keys()))
        writer.writeheader()
        writer.writerows(rate_rows)

    email_rows = DEFAULT_EMAILS if emails is None else emails
    (root / "carrier_emails.json").write_text(json.dumps(email_rows, indent=1))

    call_dir = root / "call_recordings"
    call_dir.mkdir(exist_ok=True)
    for index, name in enumerate(DEFAULT_CALLS if calls is None else calls):
        write_wav(call_dir / name, tone=index + 1)

    return root
