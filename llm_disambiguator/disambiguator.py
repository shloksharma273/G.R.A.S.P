"""Station 3's pipeline (PRD Section 6): pre-pass, LLM, confidence gate.

Three stages, each shrinking the next one's load:

1. Edges with no usable text are parked `empty_description` without a call (FR-2),
   and the lexical pre-pass settles unambiguously-worded ones (FR-3).
2. What is left is deduplicated by content hash, checked against the cache, and
   the misses are batched to the model (FR-4, FR-5).
3. Every verdict passes the confidence gate: at or above threshold it is stamped,
   below it parks `low_confidence`, and `unclear` parks regardless (FR-6).

The station is reproducible rather than pure: it calls a network service, but
temperature 0 plus the cache mean a second run over the same input issues no
calls and produces identical output.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from kg_read_harness.bundle import Bundle
from rule_preclassifier.detect import canonical_type
from rule_preclassifier.model import ORIENTATION_DEFERRED, Orientation, ParkedBundle
from rule_preclassifier.table import (
    EMPTY_DESCRIPTION,
    LOW_CONFIDENCE,
    SERVICE_ERROR,
    UNCLEAR,
)

from .cache import VerdictCache, content_key
from .config import LLMConfig
from .lexical import classify_lexically
from .model import (
    LABEL_UNCLEAR,
    METHOD_LLM,
    ORIENTATION_IMPLIED_BY_LABEL,
    STATION,
    CallStats,
    DisambiguationResult,
    ResolvedEdge,
    Verdict,
)
from .prompt import (
    REPROMPT_SUFFIX,
    SYSTEM_PROMPT,
    MalformedResponse,
    PromptItem,
    build_user_message,
    parse_response,
)
from .provider import Provider, RequestCapReached, ServiceError


def as_bundle(item: Any) -> Bundle:
    """Accept a raw Bundle or Station 2's DeferredBundle wrapper interchangeably."""
    return item.bundle if hasattr(item, "bundle") else item


def endpoints(bundle: Bundle) -> tuple[str, str, bool]:
    """`(primitive_name, state_name, reversed_from_input)` for this edge.

    Station 2 guarantees the pair is PRIMITIVE-STATE but not which end AutoGraph
    wrote as source, so the roles are resolved from the types rather than assumed.
    """
    if canonical_type(bundle.source.type) == "PRIMITIVE":
        return bundle.source.name, bundle.target.name, False
    if canonical_type(bundle.target.type) == "PRIMITIVE":
        return bundle.target.name, bundle.source.name, True
    # Shouldn't happen on Station 2 output; fall back to as-written rather than
    # raising, so one odd bundle cannot abort a batch.
    return bundle.source.name, bundle.target.name, False


def disambiguate(
    deferred: Iterable[Any],
    config: LLMConfig,
    provider: Provider | None = None,
    cache: VerdictCache | None = None,
) -> DisambiguationResult:
    """Resolve every deferred PRIMITIVE-STATE bundle (FR-1 ... FR-8)."""
    bundles = [as_bundle(item) for item in deferred]
    provider = provider if provider is not None else Provider(config)
    cache = cache if cache is not None else VerdictCache(config.cache_path, config.cache_enabled)

    result = DisambiguationResult(total_input=len(bundles), model=config.model)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- stage 1: skip empty text, then the lexical pre-pass ----------------
    pending: dict[str, list[Bundle]] = defaultdict(list)
    prompt_items: dict[str, PromptItem] = {}
    verdicts: dict[str, Verdict] = {}
    from_cache: set[str] = set()

    for bundle in bundles:
        primitive, state, _ = endpoints(bundle)
        description = bundle.description.strip()

        if not description:
            result.parked.append(
                ParkedBundle(
                    bundle=bundle,
                    reason_code=EMPTY_DESCRIPTION,
                    detail="no description to judge; no LLM call was made",
                    station=STATION,
                )
            )
            continue

        key = content_key(config.model, primitive, state, description)
        pending[key].append(bundle)

        if key in verdicts or key in prompt_items:
            continue  # already resolved or already queued (Section 11 dedup)

        if config.lexical_prepass:
            lexical = classify_lexically(description)
            if lexical is not None:
                verdicts[key] = lexical
                continue

        cached = cache.get(key)
        if cached is not None:
            result.calls.cache_hits += 1
            verdicts[key] = cached
            from_cache.add(key)
            continue

        result.calls.cache_misses += 1
        prompt_items[key] = PromptItem(
            id=key[:12],  # short, stable, and unique within a run
            primitive=primitive,
            state=state,
            description=" ".join(description.split()),
        )

    # --- stage 2: batch the remainder to the model --------------------------
    by_short_id = {item.id: key for key, item in prompt_items.items()}
    failures: dict[str, str] = {}

    for batch in _batches(list(prompt_items.values()), config.batch_size):
        try:
            answers = _classify_batch(batch, provider, config, result.calls)
        except ServiceError as error:
            for item in batch:
                failures[by_short_id[item.id]] = str(error)
            if isinstance(error, RequestCapReached):
                # The cap is a spend guard, not a transient fault: stop calling
                # and park the rest rather than burning the remaining budget.
                for remaining in _remaining_after(list(prompt_items.values()), batch):
                    failures.setdefault(by_short_id[remaining.id], str(error))
                break
            continue

        for item in batch:
            key = by_short_id[item.id]
            answer = answers.get(item.id)
            if answer is None:
                failures[key] = "the model returned no verdict for this item"
                continue
            verdicts[key] = answer
            cache.put(key, answer)

    cache.save()
    result.calls.requests = provider.requests_made
    result.calls.retries = provider.retries_made

    # --- stage 3: the confidence gate ---------------------------------------
    for key, group in pending.items():
        verdict = verdicts.get(key)
        for bundle in group:
            if verdict is None:
                result.parked.append(
                    ParkedBundle(
                        bundle=bundle,
                        reason_code=SERVICE_ERROR,
                        detail=failures.get(key, "no verdict was produced"),
                        station=STATION,
                    )
                )
            else:
                _gate(bundle, verdict, config, result, timestamp, key in from_cache)

    result.assert_conservation()
    return result


def _gate(
    bundle: Bundle,
    verdict: Verdict,
    config: LLMConfig,
    result: DisambiguationResult,
    timestamp: str,
    cached: bool = False,
) -> None:
    """Stamp at or above threshold; park otherwise (FR-6)."""
    if verdict.label == LABEL_UNCLEAR:
        result.parked.append(
            ParkedBundle(
                bundle=bundle,
                reason_code=UNCLEAR,
                detail=verdict.rationale or "the model abstained: the text supports neither reading",
                station=STATION,
            )
        )
        return

    if verdict.confidence < config.confidence_threshold:
        result.parked.append(
            ParkedBundle(
                bundle=bundle,
                reason_code=LOW_CONFIDENCE,
                detail=(
                    f"verdict {verdict.label!r} at confidence {verdict.confidence:.2f} "
                    f"is below the threshold of {config.confidence_threshold:.2f}"
                    + (f"; {verdict.rationale}" if verdict.rationale else "")
                ),
                station=STATION,
            )
        )
        return

    result.stamped.append(
        ResolvedEdge(
            bundle=bundle,
            edge_type=verdict.label,
            confidence=verdict.confidence,
            rationale=verdict.rationale,
            method=verdict.method,
            orientation=_orientation(bundle),
            model=verdict.model if verdict.method == METHOD_LLM else None,
            run_timestamp=timestamp,
            from_cache=cached,
        )
    )


def _orientation(bundle: Bundle) -> Orientation:
    """For requires and produces alike the arrow runs primitive -> state.

    The label fixes the edge's meaning (Section 5), so unlike Station 2's
    `precedes` there is nothing left for Station 4 to decide here beyond
    confirming it.
    """
    primitive, state, reversed_from_input = endpoints(bundle)
    if "PRIMITIVE" not in (
        canonical_type(bundle.source.type),
        canonical_type(bundle.target.type),
    ):
        # Neither end is a primitive, so which end is the action is a guess.
        # Say so rather than asserting an arrow Station 4 would trust.
        return Orientation(head=None, tail=None, decided_by=ORIENTATION_DEFERRED)
    return Orientation(
        head=primitive,
        tail=state,
        decided_by=ORIENTATION_IMPLIED_BY_LABEL,
        reversed_from_input=reversed_from_input,
    )


def _classify_batch(
    batch: list[PromptItem],
    provider: Provider,
    config: LLMConfig,
    stats: CallStats,
) -> dict[str, Verdict]:
    """One batch, with a single reprompt on malformed output (FR-4)."""
    stats.items_sent += len(batch)
    user = build_user_message(batch)

    try:
        return parse_response(provider.complete(SYSTEM_PROMPT, user), config.model)
    except MalformedResponse:
        stats.reprompts += 1

    try:
        return parse_response(
            provider.complete(SYSTEM_PROMPT + REPROMPT_SUFFIX, user), config.model
        )
    except MalformedResponse as error:
        raise ServiceError(f"model output was malformed after one reprompt: {error}") from None


def _batches(items: list[PromptItem], size: int) -> Iterable[list[PromptItem]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _remaining_after(items: list[PromptItem], batch: list[PromptItem]) -> list[PromptItem]:
    """Items that come after `batch` in the queue - batches are contiguous slices."""
    sent = {item.id for item in batch}
    seen_batch = False
    remaining: list[PromptItem] = []
    for item in items:
        if item.id in sent:
            seen_batch = True
            continue
        if seen_batch:
            remaining.append(item)
    return remaining
