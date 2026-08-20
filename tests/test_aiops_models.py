"""Constraint behavior for the Step 2F P0 models (AI operations, provider calls, evaluation)."""

import pytest
from django.db import IntegrityError, transaction

from tests.factories import (
    make_ai_operation,
    make_evaluation_case,
    make_evaluation_run,
    make_provider_call,
)

pytestmark = pytest.mark.django_db


def _rejects(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


class TestAIOperation:
    def test_unknown_usage_and_cost_stay_null_not_zero(self):
        operation = make_ai_operation()
        assert operation.total_tokens is None
        assert operation.estimated_cost is None
        assert operation.latency_ms is None


class TestAIProviderCall:
    def test_sequence_unique_within_operation(self):
        call = make_provider_call(sequence=1)
        _rejects(lambda: make_provider_call(operation=call.operation, sequence=1))

    def test_each_retry_is_its_own_row(self):
        call = make_provider_call(sequence=1, attempt_number=1)
        make_provider_call(operation=call.operation, sequence=2, attempt_number=2)


class TestEvaluation:
    def test_case_id_unique_within_run(self):
        case = make_evaluation_case(case_id="email-CE0058")
        _rejects(lambda: make_evaluation_case(run=case.run, case_id="email-CE0058"))

    def test_same_case_id_allowed_across_runs(self):
        make_evaluation_case(case_id="email-CE0058")
        make_evaluation_case(case_id="email-CE0058")

    def test_score_requires_numeric_or_categorical_value(self):
        from apps.aiops.models import EvaluationScore

        case = make_evaluation_case()
        _rejects(
            lambda: EvaluationScore.objects.create(
                case_result=case,
                metric_name="load_reference_accuracy",
                evaluator_type="deterministic",
                numeric_score=None,
                categorical_score=None,
            )
        )

    def test_run_name_recorded(self):
        run = make_evaluation_run(name="baseline-gpt-5.6-luna")
        assert run.name == "baseline-gpt-5.6-luna"
