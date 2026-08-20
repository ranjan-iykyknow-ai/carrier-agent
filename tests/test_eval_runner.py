"""Offline eval runner (spec 3I): pinned, reproducible, CI-safe in reuse mode."""

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.aiops.models import EvaluationCaseResult, EvaluationRun, EvaluationScore
from tests.factories import (
    make_carrier,
    make_communication_event,
    make_email_content,
    make_equipment,
    make_extraction_run,
    make_ingestion_job,
    make_inquiry,
    make_load,
    make_snapshot,
)

pytestmark = pytest.mark.django_db

GOOD_ITEM = {
    "carrier_name": "Blue Ridge Transport",
    "carrier_name_evidence": {"source_part": "body", "excerpt": "Blue Ridge Transport"},
    "mc_number": "712843",
    "mc_number_evidence": {"source_part": "body", "excerpt": "MC 712843"},
    "dot_number": None,
    "contact_email": None,
    "contact_phone": None,
    "load_reference": "29372450",
    "load_reference_evidence": {"source_part": "body", "excerpt": "29372450"},
    "equipment": "box truck",
    "equipment_evidence": {"source_part": "body", "excerpt": "box truck"},
    "availability": "confirmed",
    "availability_evidence": {"source_part": "body", "excerpt": "We can do"},
    "intents": ["availability"],
    "rates": [
        {
            "amount": "395",
            "currency": "USD",
            "role": "carrier_quote",
            "basis": "all_in",
            "evidence": {"source_part": "body", "excerpt": "We'd need $395"},
        }
    ],
    "questions": [],
    "conditions": None,
    "conditions_evidence": None,
    "summary": "Availability confirmed.",
}

GOLD_LABELS = {
    "primary_intent": "availability",
    "availability": "confirmed",
    "equipment_code": "box_truck",
    "load_reference_digits": "29372450",
    "mc_number_digits": "712843",
    "mc_stated_but_garbled": False,
    "current_carrier_position": {"amount": "395", "role": "carrier_quote", "basis": "all_in"},
    "broker_rate_mentioned": None,
    "multi_inquiry": False,
}


def build_world(*, item=None, sid="CE9001"):
    snapshot = make_snapshot(is_active=True)
    make_equipment()
    make_carrier(snapshot=snapshot, mc_number_normalized="712843", mc_number_raw="712843")
    make_load(snapshot=snapshot, external_load_id="29372450")
    event = make_communication_event(
        snapshot=snapshot, external_source_id=sid, stable_evidence_id=f"email:{sid}"
    )
    make_email_content(event=event)
    make_ingestion_job(event=event, status="completed")
    make_extraction_run(
        event=event,
        validated_output={"inquiries": [item or GOOD_ITEM]},
        validation_status="valid",
        is_current=True,
    )
    make_inquiry(
        event=event,
        carrier_resolution_status="verified",
        load_resolution_status="verified",
    )
    return snapshot, event


def write_gold(directory: Path, sid="CE9001", **label_overrides):
    labels = dict(GOLD_LABELS, **label_overrides)
    case_dir = directory / "emails"
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / f"{sid}.json").write_text(
        json.dumps(
            {
                "case_id": f"email-{sid}",
                "external_source_id": sid,
                "channel": "email",
                "labels": labels,
                "notes": "",
            }
        )
    )


class TestEvalRunner:
    def test_reuse_mode_scores_persist_and_report(self, tmp_path):
        build_world()
        write_gold(tmp_path / "gold")
        reports = tmp_path / "reports"
        out = StringIO()

        call_command(
            "eval",
            "--reuse",
            "--gold-dir",
            str(tmp_path / "gold"),
            "--reports-dir",
            str(reports),
            "--name",
            "ci-check",
            stdout=out,
        )

        run = EvaluationRun.objects.get()
        assert run.status == EvaluationRun.Status.COMPLETED
        assert run.cases_total == 1
        assert run.cases_passed == 1
        assert run.aggregate_metrics["score"] == 1.0
        assert run.dataset_version  # pinned from the active snapshot
        assert run.prompt_versions  # resolved prompt identity recorded
        assert run.configuration["mode"] == "reuse"
        case = run.cases.get()
        assert case.status == EvaluationCaseResult.Status.PASSED
        assert case.evaluation_mode is None  # email cases carry no transcript mode
        assert EvaluationScore.objects.filter(case_result=case, metric_name="intent").exists()
        report = Path(run.report_path)
        assert report.exists()
        text = report.read_text()
        assert "ci-check" in text
        assert "intent" in text

    def test_failed_case_records_categories_and_analysis(self, tmp_path):
        bad_item = dict(GOOD_ITEM, mc_number=None, mc_number_evidence=None)
        build_world(item=bad_item)
        write_gold(tmp_path / "gold")

        call_command(
            "eval",
            "--reuse",
            "--gold-dir",
            str(tmp_path / "gold"),
            "--reports-dir",
            str(tmp_path / "reports"),
            "--name",
            "ci-fail",
            stdout=StringIO(),
        )

        run = EvaluationRun.objects.get()
        assert run.cases_failed == 1
        case = run.cases.get()
        assert case.status == EvaluationCaseResult.Status.FAILED
        assert "identifier_extraction" in case.failure_categories
        assert "mc_number" in case.failure_analysis

    def test_missing_extraction_is_an_errored_case(self, tmp_path):
        snapshot = make_snapshot(is_active=True)
        event = make_communication_event(
            snapshot=snapshot, external_source_id="CE9002", stable_evidence_id="email:CE9002"
        )
        make_email_content(event=event)
        write_gold(tmp_path / "gold", sid="CE9002")

        call_command(
            "eval",
            "--reuse",
            "--gold-dir",
            str(tmp_path / "gold"),
            "--reports-dir",
            str(tmp_path / "reports"),
            "--name",
            "ci-err",
            stdout=StringIO(),
        )

        run = EvaluationRun.objects.get()
        assert run.cases_errored == 1
        assert run.status == EvaluationRun.Status.COMPLETED

    def test_fresh_mode_strips_nul_bytes_from_model_output(self, tmp_path, monkeypatch):
        build_world()
        write_gold(tmp_path / "gold")
        from tests.factories import make_ai_operation

        noisy = dict(GOOD_ITEM, summary="Availability\x00 confirmed.")

        def fake_extract(self, prompt, document, **kwargs):
            return {"inquiries": [noisy]}, make_ai_operation(operation_type="extraction")

        monkeypatch.setattr("apps.aiops.providers.OpenAIExtractor.extract", fake_extract)

        call_command(
            "eval",
            "--gold-dir",
            str(tmp_path / "gold"),
            "--reports-dir",
            str(tmp_path / "reports"),
            "--name",
            "ci-nul",
            stdout=StringIO(),
        )

        run = EvaluationRun.objects.get()
        assert run.cases_errored == 0
        case = run.cases.get()
        assert "\\u0000" not in json.dumps(case.actual_output)

    def test_reuse_mode_never_calls_a_provider(self, tmp_path, monkeypatch):
        build_world()
        write_gold(tmp_path / "gold")

        def explode(*args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("provider called in reuse mode")

        monkeypatch.setattr("apps.aiops.providers.OpenAIExtractor.extract", explode)

        call_command(
            "eval",
            "--reuse",
            "--gold-dir",
            str(tmp_path / "gold"),
            "--reports-dir",
            str(tmp_path / "reports"),
            "--name",
            "ci-noprovider",
            stdout=StringIO(),
        )

        assert EvaluationRun.objects.get().status == EvaluationRun.Status.COMPLETED
