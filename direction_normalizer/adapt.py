"""The only place that knows Station 2's and Station 3's record shapes (FR-1).

Both upstream stations stamp edges, but with different payloads — Station 2's
confidence is the categorical "high", Station 3's is a number — so everything
downstream of here works in `InputEdge` and never branches on which station a
record came from.

JSON forms are accepted too, so the stations compose on the command line as well
as in-process.
"""

from __future__ import annotations

from typing import Any, Iterable

from kg_read_harness.bundle import Bundle, Entity

from .model import InputEdge


def adapt(item: Any) -> InputEdge:
    """One stamped edge, from whichever upstream shape it arrives in."""
    if isinstance(item, InputEdge):
        return item
    if isinstance(item, dict):
        return _from_dict(item)
    return _from_record(item)


def adapt_all(items: Iterable[Any]) -> list[InputEdge]:
    return [adapt(item) for item in items]


def _from_record(record: Any) -> InputEdge:
    """A Station 2 `StampedEdge` or a Station 3 `ResolvedEdge`."""
    orientation = getattr(record, "orientation", None)
    return InputEdge(
        bundle=record.bundle,
        edge_type=record.edge_type,
        head=getattr(orientation, "head", None),
        tail=getattr(orientation, "tail", None),
        upstream_method=getattr(record, "method", "unknown"),
        upstream_station=_station_of(record),
        confidence=_confidence(getattr(record, "confidence", None)),
    )


def _from_dict(payload: dict[str, Any]) -> InputEdge:
    """A row from either station's `--format json` output."""
    try:
        bundle = Bundle(
            relation_key=str(payload["relation_key"]),
            source=Entity(str(payload["source"]["name"]), str(payload["source"]["type"])),
            target=Entity(str(payload["target"]["name"]), str(payload["target"]["type"])),
            description=str(payload.get("description", "")),
        )
    except (KeyError, TypeError) as error:
        raise ValueError(f"stamped edge is missing {error}") from None

    orientation = payload.get("orientation") or {}
    return InputEdge(
        bundle=bundle,
        edge_type=str(payload["edge_type"]),
        head=orientation.get("head"),
        tail=orientation.get("tail"),
        upstream_method=str(payload.get("method", "unknown")),
        upstream_station=str(payload.get("station", "unknown")),
        confidence=_confidence(payload.get("confidence")),
    )


def _confidence(raw: Any) -> float | None:
    """Station 3 reports a number; Station 2's categorical "high" is not one."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def _station_of(record: Any) -> str:
    module = type(record).__module__
    if "llm_disambiguator" in module:
        return "station3:llm_disambiguator"
    if "rule_preclassifier" in module:
        return "station2:rule_preclassifier"
    return "unknown"


def stamped_edges_from_json(payload: Any) -> list[InputEdge]:
    """Every stamped edge in a Station 2 and/or Station 3 JSON result.

    Accepts either station's object, a bare array of stamped rows, or a list of
    both stations' objects — which is how the two are joined on the command line.
    """
    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "summary" in payload[0]:
        edges: list[InputEdge] = []
        for part in payload:
            edges.extend(stamped_edges_from_json(part))
        return edges
    if isinstance(payload, list):
        return [adapt(row) for row in payload]
    if isinstance(payload, dict) and isinstance(payload.get("stamped"), list):
        return [adapt(row) for row in payload["stamped"]]
    raise ValueError(
        "expected a station result with a 'stamped' array, or an array of stamped edges"
    )
