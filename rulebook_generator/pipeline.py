"""The generator pipeline (PRD Section 6).

    video link -> transcript -> structured extraction -> markdown -> validation gate

Upstream of Layer 1 and additive by construction: the only thing it produces is a
rulebook in the format the pipeline already consumes, so Stations 1-5 and Layer 2
are indifferent to whether a rulebook was written by a person or generated here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llm_disambiguator.provider import Provider, ServiceError

from .cache import PayloadCache
from .config import GeneratorConfig
from .extract import ExtractionFailed, NotProcedural, extract
from .render import render
from .schema import ACCEPT, FLAG, REJECT, Rulebook
from .transcript import Transcript, truncate
from .validate import ValidationReport, check_grounding, validate


@dataclass
class GenerationResult:
    """Everything one run produced (Section 7)."""

    transcript: Transcript | None = None
    rulebook: Rulebook | None = None
    markdown: str = ""
    report: ValidationReport | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    verdict: str = REJECT
    reason: str = ""
    truncated: bool = False
    multiple_tasks: bool = False
    from_cache: bool = False
    reprompted: bool = False
    written_to: str = ""
    ingested: bool = False
    ingest_detail: str = ""

    @property
    def accepted(self) -> bool:
        return self.verdict == ACCEPT

    @property
    def writable(self) -> bool:
        return self.rulebook is not None and bool(self.markdown)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "video": {
                "id": self.transcript.video_id if self.transcript else None,
                "url": self.transcript.url if self.transcript else None,
                "words": self.transcript.words if self.transcript else 0,
                "digest": self.transcript.digest if self.transcript else None,
                "truncated": self.truncated,
                "multiple_tasks": self.multiple_tasks,
            },
            "extraction": {
                "from_cache": self.from_cache,
                "reprompted": self.reprompted,
                "intermediate": self.rulebook.to_dict() if self.rulebook else None,
            },
            "validation": self.report.to_dict() if self.report else None,
            "written_to": self.written_to or None,
            "ingested": self.ingested,
            "ingest_detail": self.ingest_detail,
        }


def generate(
    transcript: Transcript,
    config: GeneratorConfig,
    provider: Provider | None = None,
    cache: PayloadCache | None = None,
) -> GenerationResult:
    """Transcript in, validated rulebook out (FR-3 ... FR-7)."""
    result = GenerationResult()

    bounded, was_truncated = truncate(transcript)
    result.transcript = bounded
    result.truncated = was_truncated

    provider = provider if provider is not None else Provider(config.llm)
    cache = cache if cache is not None else PayloadCache(config.cache_path, config.cache_enabled)

    # --- Stage 2: structured extraction ------------------------------------
    try:
        extraction = extract(bounded, provider, config.llm.model, cache=cache)
    except NotProcedural as error:
        result.verdict = REJECT
        result.reason = f"not a procedural task: {error}"
        return result
    except ExtractionFailed as error:
        result.verdict = REJECT
        result.reason = f"extraction failed: {error}"
        return result
    except ServiceError as error:
        result.verdict = REJECT
        result.reason = f"the model was unavailable: {error}"
        return result
    finally:
        cache.save()

    result.raw = extraction.raw
    result.from_cache = extraction.from_cache
    result.reprompted = extraction.reprompted
    result.multiple_tasks = extraction.multiple_tasks

    if extraction.not_procedural or extraction.rulebook is None:
        result.verdict = REJECT
        result.reason = f"not a procedural task: {extraction.reason}"
        return result

    rulebook = extraction.rulebook
    rulebook.source_url = bounded.url or bounded.video_id
    result.rulebook = rulebook

    # --- Stage 3: deterministic rendering ----------------------------------
    result.markdown = render(rulebook)

    # --- Stage 4: the validation gate --------------------------------------
    report = validate(rulebook, markdown=result.markdown)
    if extraction.multiple_tasks:
        # Section 10: several recipes in one video is out of scope. The first
        # coherent task was taken; a human should confirm that was the right one.
        report.add(
            "multiple_tasks",
            "the video covers more than one task; only the first was taken and the "
            "rest ignored. Confirm the right one was chosen.",
        )
    if config.check_grounding:
        for issue in check_grounding(rulebook, bounded.text):
            report.issues.append(issue)
        report.settle()
    result.report = report
    result.verdict = report.verdict

    if result.verdict == ACCEPT:
        result.reason = "passed every validation check"
    elif result.verdict == FLAG:
        result.reason = (
            f"{len(report.issues)} issue(s) need a human eye before this is used"
        )
    else:
        fatal = [i for i in report.issues if i.fatal]
        result.reason = "; ".join(i.detail for i in fatal) or "failed validation"

    return result
