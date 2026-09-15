"""The sanitizer. Everything this module passes gets injected into Claude's
context on every prompt, silently, forever. A hole here has unbounded blast
radius, so the design is deliberately paranoid and deliberately boring.

Six ordered stages, and the order is load-bearing:

  1. field whitelist       - discard unknown keys without inspecting them
  2. normalize + strip     - NFKC, then delete invisible/control characters
  3. injection screening   - run on the NORMALIZED but NOT-YET-STRIPPED text
  4. markup strip          - remove tags, fences, table pipes, shell metachars
  5. length caps           - truncate at word boundaries; https-only URLs
  6. envelope invariant    - assert the result cannot forge envelope structure

Stage 2 must precede stage 3 so that `ｉｇｎｏｒｅ` (fullwidth) and
`i<ZWSP>g<ZWSP>n...` both normalize into the literal trigger words before the
regexes look at them. Stage 3 must precede stage 4 so we screen what we would
otherwise silently erase - a `</system>` tag is evidence, not noise.

The invariant in stage 6 is the security argument for the whole system: the
envelope's delimiters are `<` and `>` and its columns are `|`, and content
provably cannot contain any of them, so content cannot forge a delimiter, open
a fake block, or inject an extra column.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import config

# --------------------------------------------------------------------------
# Stage 6: the invariant. Defined first because everything else serves it.
# --------------------------------------------------------------------------

# No control chars, and none of: < > { } [ ] ` $ | \
# Those are exactly the characters used to forge structure, fake a tool call,
# or expand in a shell.
ENVELOPE_FORBIDDEN = "<>{}[]`$|\\"
ENVELOPE_SAFE_RE = re.compile(
    r"\A[^\x00-\x1f\x7f" + re.escape(ENVELOPE_FORBIDDEN) + r"]*\Z"
)

PLACEHOLDER = "(no description available)"


def envelope_safe(text: str, *, max_len: int = config.MAX_SUMMARY_LEN) -> bool:
    """True when `text` can never alter the structure of a rendered envelope."""
    return len(text) <= max_len and bool(ENVELOPE_SAFE_RE.match(text))


# --------------------------------------------------------------------------
# Stage 2: normalization and invisible-character removal
# --------------------------------------------------------------------------

# Whitespace controls carry word boundaries. Deleting them would jam tokens
# together ("Helper.Human:"), which both garbles the summary and blinds the
# line-anchored role_forge pattern. They collapse to a space instead.
_WHITESPACE_CONTROLS = "\t\n\r\v\f"


def _is_removable(ch: str) -> bool:
    cp = ord(ch)
    if ch in _WHITESPACE_CONTROLS:               # handled by _to_space, not deleted
        return False
    if cp < 0x20 or 0x7F <= cp <= 0x9F:          # C0 and C1 controls
        return True
    if 0x200B <= cp <= 0x200F:                   # zero-width + directional marks
        return True
    if 0x202A <= cp <= 0x202E:                   # bidi embedding/override
        return True
    if 0x2060 <= cp <= 0x2064:                   # word joiner, invisible operators
        return True
    if 0x2066 <= cp <= 0x2069:                   # bidi isolates
        return True
    if cp in (0xFEFF, 0x00AD):                   # BOM, soft hyphen
        return True
    if 0xE0000 <= cp <= 0xE007F:                 # Unicode tag chars (ASCII smuggling)
        return True
    if unicodedata.category(ch) == "Cf":         # any other format character
        return True
    return False


def normalize(text: str) -> str:
    """NFKC-normalize, then delete every invisible or control character.

    The U+E0000-E007F tag range is the one that matters most: a payload written
    entirely in tag characters is invisible in every editor and terminal but
    reads as plain ASCII to a model. Deleting it is non-negotiable.
    """
    text = unicodedata.normalize("NFKC", text)
    return "".join(
        " " if ch in _WHITESPACE_CONTROLS else ch
        for ch in text
        if not _is_removable(ch)
    )


# --------------------------------------------------------------------------
# Stage 3: injection screening
# --------------------------------------------------------------------------

INJECTION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "instr_override": [
        re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
                   r"(?:instruction|message|prompt|rule|direction)", re.I),
        re.compile(r"disregard\s+(?:the\s+)?(?:above|previous|prior|earlier|all)", re.I),
        re.compile(r"\byou\s+are\s+now\s+(?:an?|the)\b", re.I),
        re.compile(r"\bnew\s+instructions?\s*:", re.I),
        re.compile(r"\bsystem\s+prompt\b", re.I),
        re.compile(r"\bforget\s+(?:everything|all|your)\b", re.I),
        re.compile(r"\boverride\s+(?:your|the)\s+(?:instruction|rule|guardrail)", re.I),
    ],
    "role_forge": [
        re.compile(r"</?\s*(?:system|assistant|user|human)\s*>", re.I),
        re.compile(r"\[/?\s*INST\s*\]", re.I),
        re.compile(r"<\|[^|>]{0,40}\|>"),
        # Anchored on any whitespace, not just \n: normalize() has already
        # collapsed newlines to spaces by the time screening runs.
        re.compile(r"(?:^|\s)(?:Human|Assistant|System)\s*:", re.I),
    ],
    "tool_forge": [
        re.compile(r"\bmcp__\w+", re.I),
        re.compile(r"</?\s*(?:function_calls|invoke|function_results|antml)", re.I),
        re.compile(r"\bantml\s*:", re.I),
        re.compile(r"<\s*invoke\s+name\s*=", re.I),
        re.compile(r"\btool_use\b", re.I),
    ],
    "exfil": [
        re.compile(r"\bcurl\b[^\n]{0,120}\|\s*(?:ba|z|d)?sh\b", re.I),
        re.compile(r"\bwget\b[^\n]{0,120}\|\s*(?:ba|z|d)?sh\b", re.I),
        re.compile(r"\bbase64\s+(?:-d|--decode)\b", re.I),
        re.compile(r"(?:^|[\s/~])\.env\b", re.I),
        re.compile(r"~?/\.(?:ssh|aws|gnupg|kube)\b", re.I),
        re.compile(r"\brm\s+-[rRf]{1,2}[rRf]*\s+[/~]", re.I),
        re.compile(r"\beval\s*\(", re.I),
        re.compile(r"\bid_rsa\b|\bcredentials\.json\b", re.I),
    ],
    "self_approval": [
        re.compile(r"\balways\s+approve\b", re.I),
        re.compile(r"\bno\s+confirmation\s+(?:needed|required)\b", re.I),
        re.compile(r"\bwithout\s+(?:asking|confirmation|prompting)\b", re.I),
        re.compile(r"\bauto[-\s]?install\b", re.I),
        re.compile(r"\bsafe\s+to\s+run\b", re.I),
        re.compile(r"\bthis\s+is\s+trusted\b", re.I),
        re.compile(r"\bpre[-\s]?approved\b", re.I),
        re.compile(r"\bskip\s+(?:the\s+)?(?:permission|confirmation|review)", re.I),
    ],
}


# Advisory patterns FLAG but never quarantine.
#
# This split exists because of a real false positive: the official first-party
# `context7` plugin was quarantined for the sentence "set CONTEXT7_API_KEY for
# higher rate limits". Documenting that a tool accepts a credential is normal;
# it is not evidence of exfiltration. Conflating the two would have thrown away
# a plugin the user actually has installed.
#
# What it DOES mean is that the resource needs secrets, which forces it into
# the red install tier (explicit review) - a downgrade, not a deletion.
ADVISORY_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "needs_secrets": [
        re.compile(r"\b[A-Z][A-Z0-9]{2,}_(?:API_)?(?:KEY|TOKEN|SECRET)\b"),
        re.compile(r"\b(?:api[-\s]?key|access[-\s]?token|client[-\s]?secret)\b", re.I),
        re.compile(r"\bOAuth\b"),
    ],
}


def screen_injection(text: str) -> list[str]:
    """Return every BLOCKING pattern family that matched.

    Runs on normalized-but-unstripped text. Any non-empty result means
    quarantine: the resource is stored for audit but can never be retrieved.
    """
    return sorted(
        family
        for family, patterns in INJECTION_PATTERNS.items()
        if any(p.search(text) for p in patterns)
    )


def screen_advisory(text: str) -> list[str]:
    """Return advisory families that matched. These flag, never block."""
    return sorted(
        family
        for family, patterns in ADVISORY_PATTERNS.items()
        if any(p.search(text) for p in patterns)
    )


# --------------------------------------------------------------------------
# Stage 4 + 5: markup stripping and length caps
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]{0,200}>")
_FENCE_RE = re.compile(r"```+|~~~+")
_LEADING_MD_RE = re.compile(r"^\s*(?:#{1,6}\s*|-{3,}\s*|\*{1,3}\s*|>\s*|\|\s*)")
_WS_RE = re.compile(r"\s+")
_FORBIDDEN_RE = re.compile("[" + re.escape(ENVELOPE_FORBIDDEN) + "]")

_URL_RE = re.compile(r"\Ahttps://[A-Za-z0-9.\-]+(?::\d{1,5})?(?:/[^\s]*)?\Z")


def strip_markup(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    text = _FENCE_RE.sub(" ", text)
    text = _LEADING_MD_RE.sub("", text)
    text = _FORBIDDEN_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:.-") + "…"


def sanitize_line(text: str, *, max_len: int) -> tuple[str, list[str]]:
    """Full single-field pipeline. Returns (clean_text, flags).

    Flags reported here are advisory (e.g. non_ascii_heavy); quarantine-worthy
    flags come from screen_injection() on the caller's side, because screening
    must see the text before markup stripping.
    """
    flags: list[str] = []
    normalized = normalize(text)
    if normalized != text:
        flags.append("invisible_chars_removed")

    stripped = strip_markup(normalized)
    if _FORBIDDEN_RE.search(normalized):
        flags.append("structural_chars_removed")

    out = _truncate(stripped, max_len)
    if len(stripped) > max_len:
        flags.append("truncated")

    if out:
        non_ascii = sum(1 for c in out if ord(c) > 127)
        if non_ascii / len(out) > 0.30:
            flags.append("non_ascii_heavy")

    # Belt and braces: the invariant is asserted, never repaired.
    if not envelope_safe(out, max_len=max_len):
        return "", flags + ["invariant_violation"]
    return out, flags


def sanitize_url(url: str | None) -> tuple[str | None, list[str]]:
    """https only. No http, data:, javascript:, file: - no exceptions."""
    if not url:
        return None, []
    candidate = normalize(url).strip()
    if len(candidate) > config.MAX_URL_LEN:
        return None, ["url_too_long"]
    if not _URL_RE.match(candidate):
        return None, ["url_rejected"]
    return candidate, []


def sanitize_slug(raw: str) -> str:
    """Slugs address resources on the CLI, so they are ASCII-only by force."""
    s = normalize(raw).lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-._")
    s = re.sub(r"-{2,}", "-", s)
    return s[:64] or "unnamed"


# --------------------------------------------------------------------------
# Stage 1 + orchestration
# --------------------------------------------------------------------------

@dataclass
class SanitizeResult:
    name: str
    summary: str
    url: str | None
    tags: list[str]
    flags: list[str] = field(default_factory=list)
    # Only these make a resource unreachable. Advisory flags (truncated,
    # invisible_chars_removed, needs_secrets, ...) live in `flags` and are
    # informational; filtering retrieval on ALL flags would silently hide most
    # of the index, since long descriptions are routinely truncated.
    blocking_flags: list[str] = field(default_factory=list)
    quarantine: bool = False
    dropped_fields: int = 0

    @property
    def needs_secrets(self) -> bool:
        return "needs_secrets" in self.flags


def apply_whitelist(fields: Mapping[str, Any],
                    whitelist: Iterable[str]) -> tuple[dict[str, Any], int]:
    """Discard every key not on the funnel's whitelist, without inspecting it.

    This is what keeps README bodies, author bios and arbitrary nested JSON out
    of the pipeline entirely - the cheapest defense available, applied first.
    """
    allowed = set(whitelist)
    kept = {k: v for k, v in fields.items() if k in allowed}
    return kept, len(fields) - len(kept)


def sanitize_text_field(raw: str, *,
                        max_len: int) -> tuple[str, list[str], list[str]]:
    """Screen then clean one field.

    Returns (clean_text, advisory_flags, blocking_flags).
    """
    if not raw:
        return "", [], []
    normalized = normalize(raw)
    blocking = screen_injection(normalized)
    advisory = screen_advisory(normalized)
    clean, flags = sanitize_line(raw, max_len=max_len)
    return clean, sorted(set(flags + advisory)), blocking


def sanitize_draft(name: str, summary: str, *, url: str | None = None,
                   tags: Sequence[str] = (),
                   extra_text: Sequence[str] = ()) -> SanitizeResult:
    """Sanitize the fields of one resource.

    `extra_text` holds any additional untrusted strings that should be screened
    (e.g. a category label) but are not themselves rendered.
    """
    flags: list[str] = []
    blocking: list[str] = []

    clean_name, name_flags, name_block = sanitize_text_field(
        name, max_len=config.MAX_NAME_LEN)
    clean_summary, sum_flags, sum_block = sanitize_text_field(
        summary, max_len=config.MAX_SUMMARY_LEN)
    flags += name_flags + sum_flags
    blocking += name_block + sum_block

    clean_url, url_flags = sanitize_url(url)
    flags += url_flags

    clean_tags: list[str] = []
    for tag in list(tags)[: config.MAX_TAGS]:
        t, t_flags, t_block = sanitize_text_field(
            str(tag), max_len=config.MAX_TAG_LEN)
        blocking += t_block
        flags += t_flags
        if t:
            clean_tags.append(t)

    for blob in extra_text:
        if not blob:
            continue
        normalized_blob = normalize(str(blob))
        blocking += screen_injection(normalized_blob)
        flags += screen_advisory(normalized_blob)

    if not clean_summary:
        clean_summary = PLACEHOLDER
    if not clean_name:
        clean_name = "unnamed"

    # Final assertion. If either of these fails we have a bug, not bad input.
    assert envelope_safe(clean_name, max_len=config.MAX_NAME_LEN)
    assert envelope_safe(clean_summary, max_len=config.MAX_SUMMARY_LEN)

    blocking = sorted(set(blocking))
    return SanitizeResult(
        name=clean_name,
        summary=clean_summary,
        url=clean_url,
        tags=clean_tags,
        flags=sorted(set(flags + blocking)),
        blocking_flags=blocking,
        quarantine=bool(blocking),
    )
