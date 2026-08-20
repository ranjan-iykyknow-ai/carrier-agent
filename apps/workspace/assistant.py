"""Assistant execution (spec 3H).

A bounded synchronous loop: at most MAX_ITERATIONS model calls inside a
TIME_BUDGET_SECONDS budget, read-only application tools, and citation
validation scoped to THIS run's successful tool executions — prior turns'
results never satisfy validation, so corrections between turns cannot be
papered over. An unsupported answer fails visibly instead of being shown.
"""

import json
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.aiops import observability
from apps.aiops.models import AIOperation, AIProviderCall
from apps.aiops.prompts import assistant_prompt
from apps.aiops.providers import (
    PRICING_VERSION,
    _openai_cost,
    _pricing_for,
    resolve_prompt_chain,
)
from apps.freight.models import DatasetSnapshot
from apps.workspace.models import (
    AssistantCitation,
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    ToolExecution,
)
from apps.workspace.tools import ToolFailure, ToolScope, execute_tool, tool_schemas

MAX_ITERATIONS = 6
TIME_BUDGET_SECONDS = 45
HISTORY_MESSAGES = 12

_now = time.monotonic  # test seam

CITATION_TYPES = {
    "load": "load",
    "inquiry": "inquiry",
    "carrier": "carrier",
    "quote": "quote",
    "assessment": "assessment",
    "market": "market_rate",
    "email": "email",
    "call": "call",
}


def _normalize_citations(raw):
    """The model may cite as objects or bare id strings; accept both shapes."""
    normalized = []
    for item in raw or []:
        if isinstance(item, str) and item.strip():
            normalized.append({"source_id": item.strip(), "label": item.strip()})
        elif isinstance(item, dict) and item.get("source_id"):
            normalized.append(
                {
                    "source_id": str(item["source_id"]),
                    "label": str(item.get("label") or item["source_id"]),
                }
            )
    return normalized


def default_client():
    from openai import OpenAI

    return OpenAI(timeout=settings.PROVIDER_TIMEOUT_SECONDS, max_retries=0)


def find_or_create_conversation(actor_label: str, *, load=None) -> AssistantConversation:
    scope = AssistantConversation.Scope.LOAD if load else AssistantConversation.Scope.GLOBAL
    conversation = AssistantConversation.objects.filter(
        actor_label=actor_label,
        scope=scope,
        load=load,
        status=AssistantConversation.Status.ACTIVE,
    ).first()
    if conversation is None:
        conversation = AssistantConversation.objects.create(
            actor_label=actor_label,
            scope=scope,
            load=load,
            title=f"Load {load.external_load_id}" if load else "Operations assistant",
        )
    return conversation


def _next_sequence(conversation) -> int:
    last = conversation.messages.order_by("-sequence").first()
    return (last.sequence + 1) if last else 1


def _history(conversation):
    rows = []
    for message in conversation.messages.filter(status=AssistantMessage.Status.COMPLETED).order_by(
        "sequence"
    )[:HISTORY_MESSAGES]:
        rows.append({"role": message.role, "content": message.content})
    return rows


def run_turn(conversation, user_text: str, *, client=None) -> AssistantRun:
    """Bounded synchronous turn; an unexpected error still ends in a durable
    failed run rather than an unhandled 500."""
    client = client or default_client()
    started = _now()
    snapshot = (
        conversation.load.dataset_snapshot
        if conversation.load
        else DatasetSnapshot.objects.filter(is_active=True).first()
    )
    scope = ToolScope(snapshot=snapshot, load=conversation.load)

    with transaction.atomic():
        user_message = AssistantMessage.objects.create(
            conversation=conversation,
            sequence=_next_sequence(conversation),
            role=AssistantMessage.Role.USER,
            content=user_text,
        )
        run = AssistantRun.objects.create(
            conversation=conversation,
            user_message=user_message,
            scope_snapshot={
                "scope": conversation.scope,
                "load": conversation.load.external_load_id if conversation.load else None,
                "snapshot": str(snapshot.id) if snapshot else None,
            },
            started_at=timezone.now(),
        )

    prompt = resolve_prompt_chain(assistant_prompt())
    model = settings.OPENAI_MODEL_ASSISTANT
    operation = AIOperation.objects.create(
        operation_type=AIOperation.OperationType.ASSISTANT_TURN,
        usage_category=AIOperation.UsageCategory.ASSISTANT,
        started_at=timezone.now(),
    )
    run.prompt_name = prompt.name
    run.prompt_version = prompt.version
    run.model = model
    run.ai_operation = operation

    scope_note = (
        f"Scope: load {conversation.load.external_load_id} only."
        if conversation.load
        else "Scope: global — cross-load and lane questions in the active snapshot."
    )
    messages = [
        {"role": "system", "content": f"{prompt.text}\n{scope_note}"},
        *_history(conversation),
        {"role": "user", "content": user_text},
    ]

    collected_ids: set[str] = set()
    tokens = {"prompt": 0, "completion": 0}
    trace_seed = f"assistant:{run.id}"

    def finish(status, *, error_code=None, answer=None, citations=()):
        answer_message = AssistantMessage.objects.create(
            conversation=conversation,
            sequence=_next_sequence(conversation),
            role=AssistantMessage.Role.ASSISTANT,
            content=answer or "",
            status=AssistantMessage.Status.COMPLETED
            if status == AssistantRun.Status.COMPLETED
            else AssistantMessage.Status.FAILED,
        )
        for index, citation in enumerate(citations, start=1):
            AssistantCitation.objects.create(
                assistant_message=answer_message,
                sequence=index,
                source_type=CITATION_TYPES.get(citation["source_id"].split(":", 1)[0], "inquiry"),
                stable_source_id=citation["source_id"],
                display_label=citation.get("label", citation["source_id"])[:300],
            )
        run.assistant_message = answer_message
        run.status = status
        run.error_code = error_code
        run.completed_at = timezone.now()
        run.langfuse_trace_id = observability.trace_id_for(trace_seed)
        run.save()
        cost = _openai_cost(model, tokens["prompt"], tokens["completion"])
        operation.status = (
            AIOperation.Status.COMPLETED
            if status == AssistantRun.Status.COMPLETED
            else AIOperation.Status.FAILED
        )
        operation.prompt_tokens = tokens["prompt"]
        operation.completion_tokens = tokens["completion"]
        operation.total_tokens = tokens["prompt"] + tokens["completion"]
        operation.estimated_cost = cost
        operation.provider_call_count = operation.provider_calls.count()
        operation.last_error_code = error_code
        operation.completed_at = timezone.now()
        if operation.started_at:
            operation.latency_ms = int(
                (operation.completed_at - operation.started_at).total_seconds() * 1000
            )
        operation.save()
        from apps.aiops import runtime_evals

        runtime_evals.score_assistant_run(run)
        observability.flush_safely()
        return run

    try:
        return _loop(
            client,
            model,
            prompt,
            scope,
            messages,
            collected_ids,
            tokens,
            trace_seed,
            started,
            run,
            operation,
            finish,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception("assistant turn failed unexpectedly")
        return finish(AssistantRun.Status.FAILED, error_code="internal_error")


def _loop(
    client,
    model,
    prompt,
    scope,
    messages,
    collected_ids,
    tokens,
    trace_seed,
    started,
    run,
    operation,
    finish,
):
    tool_sequence = 0
    for iteration in range(1, MAX_ITERATIONS + 1):
        if _now() - started > TIME_BUDGET_SECONDS:
            return finish(AssistantRun.Status.TIMED_OUT, error_code="time_budget")

        recorder = observability.start_generation(
            trace_seed=trace_seed,
            name=f"assistant:iteration-{iteration}",
            model=model,
            input=messages[-1],
            metadata={"iteration": iteration, "prompt_version": prompt.version},
        )
        call = AIProviderCall.objects.create(
            operation=operation,
            sequence=iteration,
            provider=AIProviderCall.Provider.OPENAI,
            operation_name="assistant_turn",
            model=model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_source=prompt.source,
            langfuse_trace_id=recorder.trace_id,
            langfuse_observation_id=recorder.observation_id,
            started_at=timezone.now(),
            pricing_version=PRICING_VERSION,
            pricing_snapshot=_pricing_for(model),
        )
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_schemas(),
                reasoning_effort=settings.OPENAI_REASONING_EFFORT_ASSISTANT,
            )
        except Exception:
            recorder.finish(error="provider_error")
            _close_call(call, "failed", "provider_error")
            return finish(AssistantRun.Status.FAILED, error_code="provider_error")

        usage = getattr(response, "usage", None)
        tokens["prompt"] += getattr(usage, "prompt_tokens", 0) or 0
        tokens["completion"] += getattr(usage, "completion_tokens", 0) or 0
        message = response.choices[0].message
        recorder.finish(
            output=message.content or "[tool calls]",
            usage={
                "input": getattr(usage, "prompt_tokens", 0) or 0,
                "output": getattr(usage, "completion_tokens", 0) or 0,
            },
        )
        _close_call(call, "completed", None)

        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                tool_sequence += 1
                name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = None
                execution = ToolExecution(
                    assistant_run=run,
                    sequence=tool_sequence,
                    tool_name=name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                    started_at=timezone.now(),
                )
                try:
                    if not isinstance(arguments, dict):
                        raise ToolFailure("invalid_arguments", "arguments were not valid JSON")
                    result = execute_tool(name, arguments, scope)
                except ToolFailure as failure:
                    execution.status = ToolExecution.Status.FAILED
                    execution.error_code = failure.code
                    execution.error_summary = failure.summary
                    execution.completed_at = timezone.now()
                    execution.save()
                    payload = {"error": failure.code, "detail": failure.summary}
                else:
                    execution.status = ToolExecution.Status.COMPLETED
                    execution.result_summary = result.summary
                    execution.result_record_ids = result.record_ids
                    execution.completed_at = timezone.now()
                    execution.save()
                    collected_ids.update(result.record_ids)
                    payload = {"facts": result.facts, "record_ids": result.record_ids}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(payload, default=str),
                    }
                )
            continue

        # Final answer: validate citations against THIS run's tool results.
        try:
            proposal = json.loads(message.content or "")
            answer = proposal["answer"]
            citations = _normalize_citations(proposal.get("citations"))
        except (json.JSONDecodeError, TypeError, KeyError):
            answer, citations = message.content or "", []
        invalid = [c for c in citations if c.get("source_id") not in collected_ids]
        missing = bool(collected_ids) and not citations
        if invalid or missing:
            if iteration < MAX_ITERATIONS:
                messages.append({"role": "assistant", "content": message.content})
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Citation validation failed: cite only record ids returned "
                            "by tools called in THIS turn, and cite every significant "
                            "operational claim. Re-fetch with tools if needed."
                        ),
                    }
                )
                continue
            return finish(AssistantRun.Status.FAILED, error_code="unsupported_citation")
        return finish(AssistantRun.Status.COMPLETED, answer=answer, citations=citations)

    return finish(AssistantRun.Status.FAILED, error_code="budget_exhausted")


def _close_call(call, status, error_code):
    call.status = status
    call.error_code = error_code
    call.completed_at = timezone.now()
    if call.started_at:
        call.latency_ms = int((call.completed_at - call.started_at).total_seconds() * 1000)
    call.save()
