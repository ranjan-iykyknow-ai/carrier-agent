"""Offline evaluation runner (spec 3I).

`make eval` is an explicit, potentially paid workflow — it never runs in CI,
deploy, startup, or seeding. Reuse mode (--reuse) scores the current stored
extractions for free; fresh mode re-extracts every gold case with the resolved
production prompt so prompt/model candidates can be compared against the
accepted baseline. Every run pins dataset, git commit, schema, prompt, model,
policy, and pricing identity, and writes a reproducible markdown report.
"""

import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.aiops.evaluators import (
    aggregate_metrics,
    evaluate_extraction,
    evaluate_resolution,
)
from apps.aiops.models import EvaluationCaseResult, EvaluationRun, EvaluationScore
from apps.aiops.providers import PRICING_VERSION, OpenAIExtractor
from apps.candidates.policy import POLICY_VERSION
from apps.comms.models import CommunicationEvent, Transcript
from apps.freight.models import Carrier, DatasetSnapshot, Load
from apps.inquiries.extraction_schema import (
    SCHEMA_VERSION,
    ExtractionValidationError,
    parse_extraction,
)
from apps.inquiries.models import ExtractionRun, Inquiry

IMPROVEMENTS = {
    "identifier_extraction": "Tighten identifier examples in the prompt or normalization.",
    "rate_role_confusion": "Add contrastive rate-role examples to the prompt.",
    "rate_extraction": "Check rate mention coverage in the prompt rules.",
    "equipment_normalization": "Extend equipment synonyms (aliases or prompt).",
    "availability": "Sharpen explicit-vs-conditional availability definitions.",
    "intent_classification": "Add intent definitions/examples to the prompt.",
    "evidence_grounding": "Strengthen the exact-excerpt requirement in the prompt.",
    "entity_resolution": "Inspect matcher signals; the identifier may have been missed upstream.",
}


def _git_commit() -> str:
    head = Path(settings.BASE_DIR) / ".git" / "HEAD"
    try:
        content = head.read_text().strip()
        if content.startswith("ref:"):
            ref = Path(settings.BASE_DIR) / ".git" / content.split(" ", 1)[1]
            return ref.read_text().strip()[:40]
        return content[:40]
    except OSError:
        return "unknown"


class Command(BaseCommand):
    help = "Run the offline gold-set evaluation (potentially paid; never in CI)."

    def add_arguments(self, parser):
        parser.add_argument("--gold-dir", default="evaluations/gold")
        parser.add_argument("--reports-dir", default="evaluations/reports")
        parser.add_argument("--name", default=None)
        parser.add_argument(
            "--reuse",
            action="store_true",
            help="Score the stored current extractions; no provider calls, no cost.",
        )
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *, gold_dir, reports_dir, name, reuse, limit, **options):
        snapshot = DatasetSnapshot.objects.filter(is_active=True).first()
        if snapshot is None:
            raise CommandError("No active dataset snapshot; run `make seed` first.")
        cases = sorted(Path(gold_dir).glob("**/*.json"))
        if limit:
            cases = cases[:limit]
        if not cases:
            raise CommandError(f"No gold cases found under {gold_dir}.")

        mode = "reuse" if reuse else "fresh"
        extractor = OpenAIExtractor(model=settings.OPENAI_MODEL_EXTRACTION)
        prompts = {
            "extraction-email": extractor.resolve_prompt("email"),
            "extraction-call": extractor.resolve_prompt("call"),
        }
        run = EvaluationRun.objects.create(
            name=name or f"eval-{timezone.now():%Y%m%d-%H%M%S}",
            dataset_version=snapshot.version,
            dataset_checksum=snapshot.manifest_checksum,
            git_commit_sha=_git_commit(),
            prompt_versions={
                key: {"version": info.version, "source": info.source}
                for key, info in prompts.items()
            },
            model_versions={"extraction": settings.OPENAI_MODEL_EXTRACTION},
            policy_version=POLICY_VERSION,
            configuration={
                "mode": mode,
                "schema_version": SCHEMA_VERSION,
                "pricing_version": PRICING_VERSION,
                "gold_dir": str(gold_dir),
                "cases": len(cases),
            },
            status=EvaluationRun.Status.RUNNING,
            started_at=timezone.now(),
        )

        all_scores, passed, failed, errored = [], 0, 0, 0
        total_cost = Decimal(0)
        case_rows = []
        for path in cases:
            gold = json.loads(path.read_text())
            try:
                result = self._run_case(run, gold, path, snapshot, extractor, prompts, reuse)
            except Exception as error:
                errored += 1
                case_rows.append(
                    EvaluationCaseResult.objects.create(
                        run=run,
                        case_id=gold.get("case_id", path.stem),
                        stable_source_id=gold.get("external_source_id", path.stem),
                        case_type=gold.get("channel", "email"),
                        status=EvaluationCaseResult.Status.ERRORED,
                        expected_label_ref=str(path),
                        failure_analysis=f"{type(error).__name__}: {error}",
                    )
                )
                continue
            case, scores, cost = result
            case_rows.append(case)
            all_scores.append(scores)
            total_cost += cost or 0
            if case.status == EvaluationCaseResult.Status.PASSED:
                passed += 1
            else:
                failed += 1

        summary = aggregate_metrics(all_scores)
        run.status = EvaluationRun.Status.COMPLETED
        run.completed_at = timezone.now()
        run.cases_total = len(cases)
        run.cases_passed = passed
        run.cases_failed = failed
        run.cases_errored = errored
        run.aggregate_metrics = summary
        run.evaluation_cost = total_cost if not reuse else None
        report = self._write_report(run, case_rows, summary, Path(reports_dir))
        run.report_path = str(report)
        run.save()

        self.stdout.write(
            f"{run.name}: {passed} passed / {failed} failed / {errored} errored "
            f"— score {summary['score']} — report {report}"
        )

    # ------------------------------------------------------------------

    def _run_case(self, run, gold, path, snapshot, extractor, prompts, reuse):
        sid = gold["external_source_id"]
        channel = gold["channel"]
        labels = gold["labels"]
        event = CommunicationEvent.objects.get(
            dataset_snapshot=snapshot, external_source_id=sid, channel=channel
        )
        transcript = None
        if channel == "call":
            transcript = Transcript.objects.filter(
                call_recording__communication_event=event, is_current=True
            ).first()
            if transcript is None:
                raise RuntimeError("no reviewed transcript for this call case")

        cost = Decimal(0)
        operation = None
        if reuse:
            stored = ExtractionRun.objects.filter(
                communication_event=event, is_current=True, validation_status="valid"
            ).first()
            if stored is None:
                raise RuntimeError("no current valid extraction to reuse")
            proposal = parse_extraction(stored.validated_output)
        else:
            from apps.comms.pipeline import _build_document

            prompt = prompts["extraction-call" if channel == "call" else "extraction-email"]
            document = _build_document(event, transcript)
            raw, operation = extractor.extract(
                prompt,
                document,
                usage_category="evaluation",
                trace_seed=f"eval:{run.id}:{gold['case_id']}",
            )
            try:
                proposal = parse_extraction(raw)
            except ExtractionValidationError as error:
                raise RuntimeError(f"extraction failed schema validation: {error}") from error
            cost = operation.estimated_cost or Decimal(0)

        from apps.comms.pipeline import _strip_nul

        item = _strip_nul(proposal.inquiries[0].model_dump(mode="json"))
        scores = evaluate_extraction(item, labels)
        if reuse:
            inquiry = (
                Inquiry.objects.filter(communication_event=event)
                .order_by("sequence_number")
                .first()
            )
            if inquiry is not None:
                scores += evaluate_resolution(
                    carrier_status=inquiry.carrier_resolution_status,
                    load_status=inquiry.load_resolution_status,
                    labels=labels,
                    mc_in_directory=bool(labels["mc_number_digits"])
                    and Carrier.objects.filter(
                        dataset_snapshot=snapshot,
                        mc_number_normalized=labels["mc_number_digits"],
                    ).exists(),
                    load_in_directory=bool(labels["load_reference_digits"])
                    and Load.objects.filter(
                        dataset_snapshot=snapshot,
                        external_load_id=labels["load_reference_digits"],
                    ).exists(),
                )

        failed_scores = [s for s in scores if s.passed is False]
        trace_id = None
        if operation is not None:
            call = operation.provider_calls.exclude(langfuse_trace_id=None).first()
            trace_id = call.langfuse_trace_id if call else None
        case = EvaluationCaseResult.objects.create(
            run=run,
            case_id=gold["case_id"],
            stable_source_id=sid,
            case_type=channel,
            evaluation_mode=(
                EvaluationCaseResult.Mode.REVIEWED_TRANSCRIPT if channel == "call" else None
            ),
            status=EvaluationCaseResult.Status.FAILED
            if failed_scores
            else EvaluationCaseResult.Status.PASSED,
            expected_label_ref=str(path),
            actual_output={"item": item},
            ai_operation=operation,
            langfuse_trace_id=trace_id,
            estimated_cost=cost if not reuse else None,
            failure_categories=sorted({s.category for s in failed_scores}),
            failure_analysis="; ".join(
                f"{s.metric}: expected {s.expected}, got {s.actual}" for s in failed_scores
            ),
            suggested_improvement=" ".join(
                dict.fromkeys(IMPROVEMENTS.get(s.category, "") for s in failed_scores)
            ).strip(),
        )
        for score in scores:
            EvaluationScore.objects.create(
                case_result=case,
                metric_name=score.metric,
                evaluator_type=EvaluationScore.EvaluatorType.DETERMINISTIC,
                numeric_score=(
                    Decimal(str(score.value))
                    if score.value is not None
                    else (Decimal(1) if score.passed else Decimal(0))
                    if score.passed is not None
                    else None
                ),
                categorical_score="not_applicable" if score.passed is None else None,
                passed=score.passed,
                explanation=f"expected {score.expected}, got {score.actual}",
            )
        return case, scores, cost

    def _write_report(self, run, cases, summary, reports_dir: Path) -> Path:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / f"{run.name}.md"
        lines = [
            f"# Evaluation report — {run.name}",
            "",
            f"- Mode: **{run.configuration['mode']}** · cases {run.cases_total or len(cases)}",
            f"- Dataset: {run.dataset_version} ({run.dataset_checksum[:12]}…)",
            f"- Git: {run.git_commit_sha[:12]} · schema {run.configuration['schema_version']}"
            f" · policy {run.policy_version} · pricing {run.configuration['pricing_version']}",
            f"- Prompts: {json.dumps(run.prompt_versions)}",
            f"- Model: {run.model_versions.get('extraction')}",
            "- Cost: "
            + (f"${run.evaluation_cost}" if run.evaluation_cost else "none (reuse mode)"),
            "",
            f"## Overall score: {summary['score']}",
            "",
            "| Metric | Passed | Failed | N/A | Accuracy |",
            "|---|---:|---:|---:|---:|",
        ]
        for metric, row in sorted(summary["metrics"].items()):
            lines.append(
                f"| {metric} | {row['passed']} | {row['failed']} |"
                f" {row['not_applicable']} | {row['accuracy']} |"
            )
        failing = [c for c in cases if c.status != EvaluationCaseResult.Status.PASSED]
        if failing:
            lines += ["", "## Cases needing attention", ""]
            for case in failing:
                lines += [
                    f"### {case.case_id} — {case.status}",
                    f"- Categories: {', '.join(case.failure_categories) or '—'}",
                    f"- What happened: {case.failure_analysis or '—'}",
                    f"- Improve: {case.suggested_improvement or '—'}",
                    "",
                ]
        path.write_text("\n".join(lines) + "\n")
        return path
