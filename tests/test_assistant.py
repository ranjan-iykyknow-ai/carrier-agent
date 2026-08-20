"""Assistant execution loop (spec 3H): bounded, tool-grounded, citation-validated."""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from apps.aiops.models import AIOperation
from apps.workspace import assistant
from apps.workspace.models import (
    AssistantCitation,
    AssistantMessage,
    AssistantRun,
    ToolExecution,
)
from tests.factories import (
    make_equipment,
    make_lane,
    make_load,
    make_market_rate,
    make_snapshot,
)

pytestmark = pytest.mark.django_db

ACTOR = "broker@goodlanelogistics.com"


def tool_call_response(name, arguments, call_id="call_1"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id=call_id,
                            function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
                        )
                    ],
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=200, completion_tokens=30),
    )


def answer_response(answer, citations):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps({"answer": answer, "citations": citations}),
                    tool_calls=None,
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=300, completion_tokens=60),
    )


class ScriptedOpenAI:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

        def create(**kwargs):
            self.requests.append(kwargs)
            if not self.script:
                raise AssertionError("script exhausted")
            return self.script.pop(0)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


@pytest.fixture
def world():
    snapshot = make_snapshot(is_active=True)
    equipment = make_equipment()
    lane = make_lane("PA", "NY")
    load = make_load(
        snapshot=snapshot,
        external_load_id="29372450",
        lane=lane,
        equipment_type=equipment,
        distance_miles=97,
        offered_rate_usd=Decimal("420"),
        pickup_date=date(2026, 5, 23),
    )
    make_market_rate(
        snapshot=snapshot,
        lane=lane,
        equipment_type=equipment,
        week_start=date(2026, 5, 11),
        minimum_rate_per_mile="2.59",
        average_rate_per_mile="2.94",
        maximum_rate_per_mile="3.47",
    )
    return snapshot, load


class TestRunTurn:
    def test_happy_path_grounds_answer_with_citations(self, world):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        client = ScriptedOpenAI(
            [
                tool_call_response("get_load", {"external_load_id": "29372450"}),
                answer_response(
                    "The offered rate of $420 is above the market maximum for this lane.",
                    [{"source_id": "load:29372450", "label": "Load 29372450"}],
                ),
            ]
        )

        run = assistant.run_turn(conversation, "Is this load priced well?", client=client)

        assert run.status == AssistantRun.Status.COMPLETED
        messages = list(conversation.messages.order_by("sequence"))
        assert [m.role for m in messages] == ["user", "assistant"]
        assert "above the market maximum" in messages[1].content
        execution = ToolExecution.objects.get(assistant_run=run)
        assert execution.tool_name == "get_load"
        assert execution.status == ToolExecution.Status.COMPLETED
        citation = AssistantCitation.objects.get(assistant_message=messages[1])
        assert citation.stable_source_id == "load:29372450"
        assert citation.source_type == "load"
        operation = run.ai_operation
        assert operation.operation_type == AIOperation.OperationType.ASSISTANT_TURN
        assert operation.provider_call_count == 2
        assert operation.estimated_cost is not None

    def test_invalid_citation_is_nudged_then_corrected(self, world):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        client = ScriptedOpenAI(
            [
                tool_call_response("get_load", {"external_load_id": "29372450"}),
                answer_response("Claim.", [{"source_id": "load:99999999", "label": "x"}]),
                answer_response(
                    "The load is above market.",
                    [{"source_id": "load:29372450", "label": "Load 29372450"}],
                ),
            ]
        )

        run = assistant.run_turn(conversation, "Priced well?", client=client)

        assert run.status == AssistantRun.Status.COMPLETED
        assert len(client.requests) == 3

    def test_unsupported_answer_never_reaches_the_broker(self, world):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        bad = answer_response("Fabricated.", [{"source_id": "load:99999999", "label": "x"}])
        client = ScriptedOpenAI(
            [tool_call_response("get_load", {"external_load_id": "29372450"})]
            + [bad] * assistant.MAX_ITERATIONS
        )

        run = assistant.run_turn(conversation, "Priced well?", client=client)

        assert run.status == AssistantRun.Status.FAILED
        assert run.error_code == "unsupported_citation"
        answer = conversation.messages.get(role="assistant")
        assert answer.status == AssistantMessage.Status.FAILED
        assert "Fabricated" not in answer.content

    def test_answer_with_successful_tools_requires_citations(self, world):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        uncited = answer_response("The load is above market, trust me.", [])
        client = ScriptedOpenAI(
            [tool_call_response("get_load", {"external_load_id": "29372450"})]
            + [uncited] * assistant.MAX_ITERATIONS
        )

        run = assistant.run_turn(conversation, "Priced well?", client=client)

        assert run.status == AssistantRun.Status.FAILED

    def test_tool_failure_is_explicit_and_returned_to_model(self, world):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        client = ScriptedOpenAI(
            [
                tool_call_response("get_load", {"external_load_id": "00000000"}),
                answer_response(
                    "I could not retrieve that load; it is outside this conversation's scope.",
                    [],
                ),
            ]
        )

        run = assistant.run_turn(conversation, "What about load 00000000?", client=client)

        assert run.status == AssistantRun.Status.COMPLETED
        execution = ToolExecution.objects.get(assistant_run=run)
        assert execution.status == ToolExecution.Status.FAILED
        assert execution.error_code in ("scope_denied", "not_found")

    def test_iteration_budget_is_enforced(self, world):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        endless = [
            tool_call_response("get_load", {"external_load_id": "29372450"}, f"call_{i}")
            for i in range(assistant.MAX_ITERATIONS + 3)
        ]
        client = ScriptedOpenAI(endless)

        run = assistant.run_turn(conversation, "Loop forever.", client=client)

        assert run.status == AssistantRun.Status.FAILED
        assert run.error_code == "budget_exhausted"
        assert len(client.requests) == assistant.MAX_ITERATIONS

    def test_time_budget_marks_run_timed_out(self, world, monkeypatch):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        clock = iter([0, 0, 100])  # start, first check ok, second check over budget
        monkeypatch.setattr(assistant, "_now", lambda: next(clock))
        client = ScriptedOpenAI([tool_call_response("get_load", {"external_load_id": "29372450"})])

        run = assistant.run_turn(conversation, "Slow question.", client=client)

        assert run.status == AssistantRun.Status.TIMED_OUT

    def test_prior_turn_results_never_satisfy_citations(self, world):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        first = ScriptedOpenAI(
            [
                tool_call_response("get_load", {"external_load_id": "29372450"}),
                answer_response(
                    "Above market.",
                    [{"source_id": "load:29372450", "label": "Load 29372450"}],
                ),
            ]
        )
        assistant.run_turn(conversation, "Priced well?", client=first)

        stale = answer_response(
            "Still above market.", [{"source_id": "load:29372450", "label": "Load"}]
        )
        second = ScriptedOpenAI([stale] * assistant.MAX_ITERATIONS)

        run = assistant.run_turn(conversation, "And now?", client=second)

        # No tool ran this turn, so the stale citation cannot validate.
        assert run.status == AssistantRun.Status.FAILED


class TestAssistantViews:
    @pytest.fixture
    def broker(self, client):
        user = User.objects.create_user(ACTOR, password="x")
        client.force_login(user)
        return user

    def test_send_message_from_load_scope(self, client, broker, world, monkeypatch):
        snapshot, load = world
        scripted = ScriptedOpenAI(
            [
                tool_call_response("get_load", {"external_load_id": "29372450"}),
                answer_response(
                    "Above market.",
                    [{"source_id": "load:29372450", "label": "Load 29372450"}],
                ),
            ]
        )
        monkeypatch.setattr(assistant, "default_client", lambda: scripted)

        response = client.post(
            reverse("assistant_send"),
            {"message": "Priced well?", "load": "29372450"},
        )

        assert response.status_code == 302
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        assert conversation.messages.filter(role="assistant").exists()

    def test_requires_login(self, client, world):
        response = client.post(reverse("assistant_send"), {"message": "hi"})
        assert response.status_code == 302
        assert reverse("login") in response.url


class TestAnswerShapeRobustness:
    def test_string_citations_are_accepted(self, world):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        client = ScriptedOpenAI(
            [
                tool_call_response("get_load", {"external_load_id": "29372450"}),
                answer_response("Above market.", ["load:29372450"]),
            ]
        )

        run = assistant.run_turn(conversation, "Priced well?", client=client)

        assert run.status == AssistantRun.Status.COMPLETED
        citation = AssistantCitation.objects.get(assistant_message=run.assistant_message)
        assert citation.stable_source_id == "load:29372450"

    def test_unexpected_error_marks_run_failed_not_500(self, world, monkeypatch):
        snapshot, load = world
        conversation = assistant.find_or_create_conversation(ACTOR, load=load)
        client = ScriptedOpenAI(
            [
                tool_call_response("get_load", {"external_load_id": "29372450"}),
                answer_response("Above market.", [{"source_id": "load:29372450"}]),
            ]
        )
        monkeypatch.setattr(
            assistant,
            "_normalize_citations",
            lambda raw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        run = assistant.run_turn(conversation, "Priced well?", client=client)

        assert run.status == AssistantRun.Status.FAILED
        assert run.error_code == "internal_error"
