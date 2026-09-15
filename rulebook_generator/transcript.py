"""Stage 1 — transcript acquisition (PRD Section 6, FR-2).

Fetch a video's captions, strip the timing and the noise, and hand back clean
prose. Section 3 is explicit that this version relies on existing captions: no
ASR, so a video without them fails clearly rather than producing a rulebook from
nothing.

`youtube-transcript-api` is an **optional** dependency. The project has kept its
required dependency list at one on purpose, and everything except live YouTube
fetching works without it — `--transcript FILE` covers testing, offline use, and
any source that is not YouTube.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from kg_read_harness.errors import HarnessError

#: YouTube ids are 11 characters of the URL-safe alphabet.
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

_URL_PATTERNS = (
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})"),
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
)

#: Caption artefacts that are not speech: [Music], (applause), >> speaker marks.
_NOISE = re.compile(r"\[[^\]]{0,40}\]|\([^)]{0,40}\)|^>>+\s*", re.MULTILINE)

#: Auto-generated captions repeat the tail of the previous cue constantly.
_MAX_REPEAT_WINDOW = 12


class NoCaptions(HarnessError):
    """The video has no usable captions (Section 10)."""

    exit_code = 4
    label = "no captions"


class BadVideoUrl(HarnessError):
    exit_code = 2
    label = "bad video url"


@dataclass(frozen=True)
class Transcript:
    text: str
    video_id: str
    source: str
    url: str = ""

    @property
    def digest(self) -> str:
        """Content hash — the cache key that makes a re-run free (FR-6)."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    @property
    def words(self) -> int:
        return len(self.text.split())


def video_id(url: str) -> str:
    """The video id from a watch URL, a short URL, a shorts URL, or a bare id."""
    candidate = (url or "").strip()
    if _VIDEO_ID.match(candidate):
        return candidate
    for pattern in _URL_PATTERNS:
        found = pattern.search(candidate)
        if found:
            return found.group(1)
    raise BadVideoUrl(
        f"could not find a YouTube video id in {url!r}.",
        "pass a full watch/shorts URL, or the 11-character video id, or use "
        "--transcript FILE to supply captions directly.",
    )


def clean(raw: str, caption_artifacts: bool = True) -> str:
    """Caption text to prose: drop the noise, undo the overlap, join the cues.

    Auto-generated captions arrive as overlapping windows — each cue repeats the
    tail of the one before — so joining them naively triples the text and teaches
    the model that everything was said three times.

    `caption_artifacts=False` is for written documentation rather than speech.
    Both repairs above are *wrong* on a manual. The noise pattern strips
    parenthesised spans because captions use them for `(applause)`; a manual uses
    them for `(HDOP below 2.0)`, and that parenthesis is the precondition. The
    de-overlap deletes a repeated phrase, which in a procedure list is a real
    repeated step rather than a duplicated cue. Line structure is kept too: a
    manual's bullets are its step boundaries, and flattening them into one prose
    blob throws away the clearest signal in the document.
    """
    text = (raw or "").replace(" ", " ")
    if not caption_artifacts:
        stripped = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        kept: list[str] = []
        for line in stripped:
            if line or (kept and kept[-1]):
                kept.append(line)
        return "\n".join(kept).strip()

    text = _NOISE.sub(" ", text)

    words: list[str] = []
    for line in text.splitlines():
        incoming = line.split()
        if not incoming:
            continue
        overlap = 0
        window = min(_MAX_REPEAT_WINDOW, len(words), len(incoming))
        for size in range(window, 0, -1):
            if [w.lower() for w in words[-size:]] == [w.lower() for w in incoming[:size]]:
                overlap = size
                break
        words.extend(incoming[overlap:])

    return re.sub(r"\s+", " ", " ".join(words)).strip()


def from_file(path: str | Path, url: str = "", manual: bool = False) -> Transcript:
    """Captions from a local file — plain text, or one cue per line."""
    raw = Path(path).read_text(encoding="utf-8")
    text = clean(raw, caption_artifacts=not manual)
    if not text:
        raise NoCaptions(
            f"{path} contains no usable caption text.",
            "supply a file with the spoken words, one cue per line or as prose.",
        )
    return Transcript(
        text=text,
        video_id=url or str(path),
        source="manual" if manual else "file",
        url=url,
    )


def from_youtube(url: str, languages: tuple[str, ...] = ("en", "en-US", "en-GB")) -> Transcript:
    """Captions for a YouTube video (FR-2)."""
    identifier = video_id(url)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise HarnessError(
            "youtube-transcript-api is not installed, so captions cannot be fetched.",
            "pip install youtube-transcript-api  (it is an optional dependency; "
            "--transcript FILE works without it).",
        ) from None

    try:
        fetched = YouTubeTranscriptApi().fetch(identifier, languages=list(languages))
    except Exception as error:
        raise NoCaptions(
            f"no usable captions for {identifier} ({error.__class__.__name__}).",
            "this version relies on existing captions - ASR is a future extension. "
            "Try a captioned source, or pass --transcript FILE.",
        ) from None

    lines = [getattr(cue, "text", "") or "" for cue in fetched]
    text = clean("\n".join(lines))
    if not text:
        raise NoCaptions(
            f"the captions for {identifier} contain no speech.",
            "the track may be music-only; try a captioned source.",
        )
    return Transcript(
        text=text,
        video_id=identifier,
        source="youtube",
        url=f"https://www.youtube.com/watch?v={identifier}",
    )


#: Above this, the transcript is truncated before extraction and the report says
#: so (Section 10, "very long transcript"). Generous: a long recipe video is
#: about 3,000 words, and the models in use take far more than this comfortably.
MAX_WORDS = 12_000


def truncate(transcript: Transcript, max_words: int = MAX_WORDS) -> tuple[Transcript, bool]:
    """Bound the transcript, reporting whether anything was dropped."""
    words = transcript.text.split()
    if len(words) <= max_words:
        return transcript, False
    shortened = Transcript(
        text=" ".join(words[:max_words]),
        video_id=transcript.video_id,
        source=transcript.source,
        url=transcript.url,
    )
    return shortened, True
