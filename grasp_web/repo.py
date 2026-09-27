"""Browsing a robotics documentation repository for procedures.

The generator's other two inputs — a video link and a pasted transcript — arrive
as one blob of text. A documentation repo does not: PX4's user guide alone is
several hundred markdown files, of which a given procedure occupies three or four.
So this module does the part the other inputs do not need, which is **choosing**:
list what the repo holds, rank it by how procedure-shaped it looks, and hand the
chosen files to the generator as one document.

It only ever talks to GitHub's API and raw hosts. That is a deliberate limit
rather than an incidental one: this server is handed a URL by whoever opens the
page, and a fetcher that accepts any URL is an SSRF hole pointed at whatever the
host can reach. Two allowed hosts, both public, is the whole of it.

Ranking is a heuristic and is presented as one. It moves the likely procedures to
the top of a list a human then picks from; it never selects on its own, because
"which pages describe the procedure you mean" is a question the person asking has
a much better answer to than a keyword score does.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from kg_read_harness.errors import HarnessError

API_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"
TIMEOUT_SECONDS = 30.0

#: Markdown is what documentation repos are written in. `.mdx` covers the
#: Docusaurus/VitePress sites most robotics projects publish from, and `.rst` the
#: Sphinx ones - which is what most ROS packages use.
DOC_SUFFIXES = (".md", ".mdx", ".rst")

#: Code mode additionally reads the files that *declare the interfaces*: the
#: sources that advertise a service or a publisher, the interface definitions
#: themselves, and the launch and config files that name the controllers.
CODE_SUFFIXES = (
    ".py", ".cpp", ".hpp", ".h", ".c", ".cc",
    ".srv", ".action", ".msg", ".idl",
    ".yaml", ".yml", ".xml", ".launch",
)

#: The two things this page can read out of a repository.
MODE_DOCS = "docs"
MODE_CODE = "code"
MODES = (MODE_DOCS, MODE_CODE)

#: Paths that are never a procedure: machinery, templates, and the changelog.
SKIP_PATTERNS = (
    re.compile(r"(^|/)(node_modules|\.github|\.vitepress|\.vuepress|_snippets)/"),
    re.compile(r"(^|/)(CHANGELOG|LICENSE|CONTRIBUTING|CODE_OF_CONDUCT|SUMMARY)\.mdx?$", re.I),
    re.compile(r"(^|/)(README)\.mdx?$", re.I),
)

#: A translated copy of a page the repo already has in English. PX4's guide ships
#: a dozen of these and they would otherwise swamp the list.
TRANSLATION = re.compile(
    r"(^|/)(zh|ko|ru|ja|de|fr|es|it|pt|tr|uk|nl|pl)(-[A-Za-z]{2})?/",
)

#: Words that mark a page as describing something *done*, in the order a robotics
#: manual tends to use them. Purely a ranking hint — see the module docstring.
#: Paths that tend to *declare* an interface rather than merely use one. A ROS
#: package puts its service and action definitions under these directories, and
#: names the node that advertises them after what it drives. A ranking hint, like
#: PROCEDURE_WORDS - it reorders the list, it never ticks a box.
INTERFACE_WORDS = (
    "srv", "action", "msg", "service", "services", "topic", "topics",
    "controller", "controllers", "driver", "hardware_interface", "dashboard",
    "node", "launch", "api", "client", "server", "bringup", "moveit",
    "gripper", "trajectory", "command", "commands", "io", "gpio",
)

PROCEDURE_WORDS = (
    "arm", "disarm", "takeoff", "take_off", "land", "landing", "return", "rtl",
    "launch", "mission", "flight_mode", "flight_modes", "procedure", "checklist",
    "preflight", "pre_flight", "startup", "shutdown", "calibration", "calibrate",
    "setup", "install", "assembly", "configure", "configuration", "operation",
    "safety", "failsafe", "emergency", "gripper", "pick", "place", "navigation",
    "teleop", "bringup", "tutorial", "getting_started", "quickstart", "first",
)

#: Caps. A tree listing is bounded so a huge monorepo cannot fill the page, and a
#: document is bounded because the generator truncates at 12,000 words anyway.
MAX_LISTED = 600
MAX_SELECTED = 12
MAX_DOC_BYTES = 600 * 1024
TREE_CACHE_SECONDS = 600.0


class RepoError(HarnessError):
    """The repository could not be read (a bad URL, a private repo, a rate limit)."""

    exit_code = 2
    label = "repo"


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str
    ref: str = ""
    path: str = ""

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        base = f"https://github.com/{self.slug}"
        if self.ref and self.path:
            return f"{base}/tree/{self.ref}/{self.path}"
        return base

    def raw_url(self, path: str, ref: str) -> str:
        quoted = urllib.parse.quote(path)
        return f"https://{RAW_HOST}/{self.owner}/{self.repo}/{ref}/{quoted}"


#: One pattern for every spelling: a full URL, a tree/blob URL, a bare
#: `owner/repo`, and a bare slug that carries a branch and subdirectory. The
#: host prefix is optional but, when present, must be github.com - a URL on any
#: other host fails to match and is refused below rather than fetched.
_REPO = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?github\.com/)?"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/(?:tree|blob)/(?P<ref>[^/]+)(?:/(?P<path>.*))?)?/?$"
)


def parse_repo(url: str) -> RepoRef:
    """`RepoRef` from a GitHub URL, a tree/blob URL, or a bare `owner/repo`."""
    candidate = (url or "").strip()
    if not candidate:
        raise RepoError(
            "no repository given.",
            "paste a GitHub URL such as https://github.com/PX4/PX4-user_guide, "
            "or just PX4/PX4-user_guide.",
        )

    found = _REPO.match(candidate)
    if found:
        parts = found.groupdict()
        return RepoRef(
            owner=parts["owner"],
            repo=parts["repo"],
            ref=parts.get("ref") or "",
            path=(parts.get("path") or "").strip("/"),
        )

    raise RepoError(
        f"{candidate!r} is not a GitHub repository.",
        "this reads public GitHub repositories only: paste a github.com URL, or "
        "owner/repo. Other hosts are not fetched on purpose.",
    )


def _get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    """One GET against GitHub, with the host pinned.

    The host check is the SSRF guard. Every URL here is built by this module, so
    it can only fail if someone changes the code above it — which is exactly when
    a guard is worth having.
    """
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in (API_HOST, RAW_HOST):
        raise RepoError(f"refusing to fetch from {host or url!r}.", "only GitHub is read.")

    headers = {"Accept": accept, "User-Agent": "grasp-web"}
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise _http_error(error, url) from None
    except Exception as error:
        raise RepoError(
            f"could not reach GitHub ({error.__class__.__name__}: {error}).",
            "check the network, then try again.",
        ) from None


def _http_error(error: urllib.error.HTTPError, url: str) -> RepoError:
    if error.code == 404:
        return RepoError(
            "that repository, branch or path does not exist, or is private.",
            "this reads public repositories only. Check the spelling and the branch.",
        )
    if error.code in (403, 429):
        remaining = error.headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            return RepoError(
                "GitHub's rate limit for unauthenticated requests is used up.",
                "set GITHUB_TOKEN to raise the limit from 60 to 5,000 requests an "
                "hour, or wait for the window to reset.",
            )
        return RepoError(f"GitHub refused the request (HTTP {error.code}).", "")
    return RepoError(f"GitHub returned HTTP {error.code} for {url}.", "")


def _json(url: str) -> Any:
    body = _get(url)
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise RepoError(f"GitHub returned something that is not JSON ({error}).", "") from None


def default_branch(ref: RepoRef) -> str:
    payload = _json(f"https://{API_HOST}/repos/{ref.owner}/{ref.repo}")
    branch = str(payload.get("default_branch") or "").strip()
    if not branch:
        raise RepoError(f"{ref.slug} reports no default branch.", "")
    return branch


def suffixes_for(mode: str) -> tuple[str, ...]:
    return DOC_SUFFIXES + CODE_SUFFIXES if mode == MODE_CODE else DOC_SUFFIXES


def is_doc(path: str, mode: str = MODE_DOCS) -> bool:
    """Whether this file is worth offering, for the kind of reading being done."""
    if not path.lower().endswith(suffixes_for(mode)):
        return False
    if TRANSLATION.search(path):
        return False
    if mode == MODE_CODE and re.search(r"(^|/)(test|tests)/", path):
        # A test names the interfaces it exercises, which reads exactly like a
        # declaration and is not one. Offering them buries the real sources.
        return False
    return not any(pattern.search(path) for pattern in SKIP_PATTERNS)


def score(path: str, mode: str = MODE_DOCS) -> int:
    """How relevant a path looks, for the kind of reading being done.

    In docs mode that means procedure-shaped. In code mode it means likely to
    *declare an interface* - a `.srv` file, a controller, the node that advertises
    the services. Both are hints for ordering, nothing more.
    """
    haystack = re.sub(r"[^a-z0-9]+", "_", path.lower())
    total = 0
    if mode == MODE_CODE:
        for word in INTERFACE_WORDS:
            if f"_{word}_" in f"_{haystack}_":
                total += 3
        # An interface definition is the most direct statement there is.
        if path.lower().endswith((".srv", ".action", ".msg")):
            total += 6
    for word in PROCEDURE_WORDS:
        if word in haystack:
            # A hit in the filename means more than one in a parent directory:
            # `flight_modes/land.md` is a procedure, `land/index.md` is a section.
            total += 3 if word in haystack.rsplit("_", 1)[-1] else 2
            total += 2 if haystack.endswith(word) else 0
    return total


@dataclass
class _Cached:
    when: float
    ref: str
    files: list[dict[str, Any]]
    truncated: bool


class RepoBrowser:
    """Lists and fetches documentation from a public GitHub repository.

    The tree is cached briefly: picking files is an iterative business (list,
    search, change the filter, look again) and re-fetching a 10,000-entry tree on
    each keystroke would burn the unauthenticated rate limit in a minute.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str], _Cached] = {}
        self._lock = threading.Lock()

    # --- listing ------------------------------------------------------------

    def tree(self, url: str, mode: str = MODE_DOCS) -> dict[str, Any]:
        """Every readable file in the repo, best candidates first.

        `mode` decides what "readable" means: prose in docs mode, prose plus the
        sources and interface definitions in code mode. It is part of the cache
        key, because the two modes list different files for the same repo.
        """
        mode = mode if mode in MODES else MODE_DOCS
        ref = parse_repo(url)
        key = (ref.owner.lower(), ref.repo.lower(), ref.path.lower(), mode)

        with self._lock:
            hit = self._cache.get(key)
            if hit and (time.monotonic() - hit.when) < TREE_CACHE_SECONDS:
                return self._payload(ref, hit, cached=True, mode=mode)

        branch = ref.ref or default_branch(ref)
        payload = _json(
            f"https://{API_HOST}/repos/{ref.owner}/{ref.repo}/git/trees/"
            f"{urllib.parse.quote(branch)}?recursive=1"
        )
        entries = payload.get("tree")
        if not isinstance(entries, list):
            raise RepoError(f"{ref.slug} returned no file tree for {branch!r}.", "")

        prefix = f"{ref.path}/" if ref.path else ""
        files = []
        for entry in entries:
            if entry.get("type") != "blob":
                continue
            path = str(entry.get("path") or "")
            if prefix and not path.startswith(prefix):
                continue
            if not is_doc(path, mode):
                continue
            files.append(
                {
                    "path": path,
                    "name": path.rsplit("/", 1)[-1],
                    "dir": path.rsplit("/", 1)[0] if "/" in path else "",
                    "size": int(entry.get("size") or 0),
                    "score": score(path, mode),
                }
            )

        if not files:
            raise RepoError(
                f"no {'source or documentation' if mode == MODE_CODE else 'markdown documentation'} found in {ref.slug}"
                + (f" under {ref.path}/" if ref.path else "")
                + ".",
                "point at a docs repository, or at the subdirectory holding the "
                "guide - for example PX4/PX4-user_guide, or .../tree/main/en.",
            )

        files.sort(key=lambda f: (-f["score"], f["path"]))
        cached = _Cached(
            when=time.monotonic(),
            ref=branch,
            files=files[:MAX_LISTED],
            truncated=bool(payload.get("truncated")) or len(files) > MAX_LISTED,
        )
        with self._lock:
            self._cache[key] = cached
        return self._payload(ref, cached, cached=False, mode=mode)

    @staticmethod
    def _payload(ref: RepoRef, entry: _Cached, cached: bool, mode: str = MODE_DOCS) -> dict[str, Any]:
        return {
            "repo": ref.slug,
            "url": ref.url,
            "ref": entry.ref,
            "path": ref.path,
            "mode": mode,
            "files": entry.files,
            "truncated": entry.truncated,
            "from_cache": cached,
            "max_selected": MAX_SELECTED,
        }

    def parse_check(self, url: str, paths: list[str]) -> str:
        """Validate a generate request before a job is made for it.

        The slow part runs inside the job, but a typo in the URL or an empty
        selection is knowable now - and a caller that hears it from the POST can
        act on it, where one that hears it from a job a second later has already
        drawn a spinner.
        """
        reference = parse_repo(url)
        chosen = [p for p in dict.fromkeys(paths or []) if p]
        if not chosen:
            raise RepoError("no pages chosen.", "tick at least one page to read.")
        if len(chosen) > MAX_SELECTED:
            raise RepoError(
                f"{len(chosen)} pages chosen; the limit is {MAX_SELECTED}.",
                "a rulebook describes one procedure. Narrow the selection to the "
                "pages that describe it.",
            )
        return reference.slug

    # --- fetching -----------------------------------------------------------

    def document(
        self, url: str, paths: list[str], ref: str = "", mode: str = MODE_DOCS
    ) -> dict[str, Any]:
        """The chosen files, concatenated into one document for the generator.

        Each file keeps its path as a heading. That is not decoration: it is the
        only way the rulebook's own source is traceable back to the page it came
        from once the files are one blob of text.
        """
        reference = parse_repo(url)
        chosen = [p for p in dict.fromkeys(paths or []) if p]
        if not chosen:
            raise RepoError("no files chosen.", "tick at least one page to read.")
        if len(chosen) > MAX_SELECTED:
            raise RepoError(
                f"{len(chosen)} files chosen; the limit is {MAX_SELECTED}.",
                "a rulebook describes one procedure. Narrow the selection to the "
                "pages that describe it.",
            )

        branch = ref or reference.ref or default_branch(reference)
        parts: list[str] = []
        used: list[str] = []
        total = 0

        for path in chosen:
            if not is_doc(path, mode if mode in MODES else MODE_DOCS):
                raise RepoError(f"{path} is not a readable file in {mode} mode.", "")
            body = _get(reference.raw_url(path, branch), accept="text/plain").decode(
                "utf-8", "replace"
            )
            total += len(body.encode("utf-8"))
            if total > MAX_DOC_BYTES and used:
                break
            parts.append(f"# {path}\n\n{body.strip()}")
            used.append(path)

        return {
            "repo": reference.slug,
            "ref": branch,
            "mode": mode,
            "paths": used,
            "skipped": [p for p in chosen if p not in used],
            "source_url": f"https://github.com/{reference.slug}/tree/{branch}",
            "text": "\n\n".join(parts).strip(),
        }
