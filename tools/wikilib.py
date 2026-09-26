"""Shared loading/serialisation for the wiki. Stdlib + PyYAML only."""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import yaml

# The vault is full of arrows, dashes and Cyrillic, and a Windows console
# defaults to a codepage that cannot encode them. Without this, printing a
# search result raises UnicodeEncodeError on the author's own machine while
# working fine in CI.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # already redirected, or not a tty
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.path.join(ROOT, "wiki")

# type -> directory under wiki/
TYPE_DIR = {
    "overview": "",
    "entity": "entities",
    "concept": "concepts",
    "source": "sources",
    "research": "research",
    "query": "queries",
    "comparison": "comparisons",
    "synthesis": "synthesis",
    "thesis": "thesis",
    "methodology": "methodology",
    "finding": "findings",
    "log": "log",
}
DIR_TYPE = {v: k for k, v in TYPE_DIR.items() if v}

COMMON_REQUIRED = ["type", "title", "summary", "tags", "related", "created", "updated", "author", "status"]
TYPE_REQUIRED = {
    "source": [],  # url-or-venue is checked in lint.py; see the note there
    "research": ["question"],
    "thesis": ["confidence", "status"],
    "finding": ["source", "confidence", "replicated"],
    "log": ["date"],
}
STATUSES = {"current", "superseded", "draft"}
CYCLE_CLASSES = {"A", "B", "C", "D", "E"}
CONFIDENCE = {"low", "medium", "high"}

# fields emitted in this order, others appended alphabetically
FIELD_ORDER = [
    "type", "title", "summary", "tags", "entities", "related",
    "created", "updated", "author", "status", "supersedes", "superseded_by",
    "question", "cycle_class", "authors", "year", "url", "venue",
    "source", "confidence", "replicated", "stance", "review_after", "claims",
]

LINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+)")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9.]+)*$")
CODE_FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# derived pages, excluded from load_pages() but valid link targets
GENERATED_SLUGS = {"index", "log", "state"}

# Frontmatter fields whose values are [[links]].
LINK_FM_KEYS = ("related", "supersedes", "superseded_by", "source", "entities")

# The subset the machinery acts on rather than merely displays. A typo in one
# of these does not degrade retrieval, it inverts it: an unmatched `supersedes`
# leaves the outdated page marked `current` and search keeps serving the old
# number. These are lint errors; everything else is a warning.
LOAD_BEARING_FM_KEYS = ("supersedes", "superseded_by", "source")


def claim_key(claim: dict) -> tuple[str, str, str]:
    """The identity of a claim: `(subject, metric, qualifier)`.

    One definition, because two disagreeing ones silently break contradiction
    detection. An explicit `qualifier: null` and an absent `qualifier` are the
    same claim — `.get("qualifier", "")` alone returns None for the former and
    keys it as the string "None", which then never collides with the latter.
    """
    return (
        str(claim.get("subject", "") or ""),
        str(claim.get("metric", "") or ""),
        str(claim.get("qualifier", "") or ""),
    )


def link_target(captured: str) -> str:
    """Normalise a captured [[link]] target.

    Inside a markdown table an alias pipe must be escaped, so `[[slug\\|Alias]]`
    is correct authoring and the capture keeps a trailing backslash.
    """
    return captured.strip().rstrip("\\").strip()


@dataclass
class Page:
    slug: str
    path: str
    fm: dict
    body: str
    raw: str
    errors: list = field(default_factory=list)

    @property
    def rel(self) -> str:
        return os.path.relpath(self.path, ROOT).replace("\\", "/")

    @property
    def dirname(self) -> str:
        return os.path.basename(os.path.dirname(self.path))

    @property
    def type(self) -> str:
        return self.fm.get("type") or DIR_TYPE.get(self.dirname, "")

    def body_links(self) -> set[str]:
        """Wiki links from the prose.

        Links inside fenced or inline code are examples in documentation, not
        links — `[[page-slug]]` in a template block must not read as a broken
        reference to a page named "page-slug".
        """
        prose = CODE_FENCE_RE.sub("", self.body)
        prose = INLINE_CODE_RE.sub("", prose)
        return set(link_target(m.group(1)) for m in LINK_RE.finditer(prose))

    def fm_links(self, *keys: str) -> set[str]:
        """Wiki links from the named frontmatter fields.

        Kept separate from `body_links` because severity differs: a broken
        `supersedes` target silently leaves an outdated page marked `current`,
        whereas a broken link in prose is merely a dead end.
        """
        found: set[str] = set()
        for key in keys:
            val = self.fm.get(key)
            for item in val if isinstance(val, list) else [val]:
                if isinstance(item, str):
                    found.update(link_target(m.group(1)) for m in LINK_RE.finditer(item))
        return found

    def links(self) -> set[str]:
        """Every wiki link on the page, body and frontmatter alike."""
        return self.body_links() | self.fm_links(*LINK_FM_KEYS)


def split_frontmatter(text: str):
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end < 0:
        return None, text
    raw = text[3:end]
    body = text[end + 4 :].lstrip("\n")
    try:
        fm = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return "ERROR", body
    return (fm if isinstance(fm, dict) else "ERROR"), body


DATE_FIELDS = ("created", "updated", "date", "as_of")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def dump_frontmatter(fm: dict) -> str:
    """Deterministic YAML in a fixed field order, Obsidian-friendly."""
    fm = dict(fm)
    # keep dates unquoted and uniform whether they arrived as str or date
    for key in DATE_FIELDS:
        value = fm.get(key)
        if isinstance(value, str) and ISO_DATE.match(value):
            fm[key] = date.fromisoformat(value)
    ordered = {k: fm[k] for k in FIELD_ORDER if k in fm}
    ordered.update({k: v for k, v in sorted(fm.items()) if k not in ordered})
    out = yaml.safe_dump(
        ordered, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10**6
    )
    return f"---\n{out}---\n"


def render(page: Page) -> str:
    return dump_frontmatter(page.fm) + "\n" + page.body.lstrip("\n")


def load_pages(include_generated: bool = False) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    collisions: dict[str, set] = defaultdict(set)
    for dirpath, dirnames, filenames in os.walk(WIKI):
        dirnames[:] = [d for d in dirnames if d != "assets"]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            slug = fn[:-3]
            raw = open(path, encoding="utf-8", errors="replace").read()
            fm, body = split_frontmatter(raw)
            page = Page(slug=slug, path=path, fm={} if not isinstance(fm, dict) else fm, body=body, raw=raw)
            if fm == "ERROR":
                page.errors.append("frontmatter is not valid YAML")
            elif fm is None:
                page.errors.append("no frontmatter")
            if is_generated(raw):
                if slug in GENERATED_SLUGS:
                    if not include_generated:
                        continue
                else:
                    # A page carrying the marker would otherwise vanish from
                    # lint, build and every index while still looking fine in
                    # Obsidian — invisible, and a one-line way to hide a page
                    # from review. Keep it and say so.
                    page.errors.append(
                        "carries the `<!-- generated:` marker but is not a "
                        "generated file; the marker hides a page from every tool"
                    )
            if slug in pages:
                collisions[slug].add(pages[slug].rel)
                collisions[slug].add(page.rel)
            pages[slug] = page

    # Reported on the surviving page: the dict is keyed by slug, so the earlier
    # file is already gone by the time anything downstream could notice.
    for slug, paths in collisions.items():
        pages[slug].errors.append(
            f"duplicate slug across {len(paths)} files ({', '.join(sorted(paths))}); "
            f"[[{slug}]] resolves to whichever the filesystem yields first"
        )
    return pages


GENERATED_MARK = "<!-- generated:"


def is_generated(text: str) -> bool:
    return GENERATED_MARK in text[:2000]


def generated_header(tool: str) -> str:
    return (
        f"{GENERATED_MARK} {tool} — do not edit by hand.\n"
        f"     Regenerate with: python tools/{tool} -->\n"
    )


def write_if_changed(path: str, content: str) -> bool:
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old == content:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return True


def today() -> str:
    return date.today().isoformat()


def eprint(*a):
    print(*a, file=sys.stderr)
