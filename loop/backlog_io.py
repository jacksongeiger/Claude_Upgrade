"""Minimal reader/writer for the restricted YAML subset used by backlog.yaml.

Grammar supported (see loop/README.md "backlog.yaml"):

    rows:
      - key: value
        key2: value2
      - key: value
        ...

- Exactly one top-level key, ``rows:`` (bare, no inline value), whose block
  is a list of flat mappings.
- Each list item starts with ``  - key: value`` (any amount of leading
  whitespace, a ``-``, then the first ``key: value`` pair). Subsequent
  fields of the same row are plain ``key: value`` lines indented under it
  (no leading ``-``).
- Blank lines and whole-line comments (``#`` after optional whitespace) are
  ignored anywhere.
- A value is either:
    * a double-quoted string (``"..."``), which may contain colons, ``#``,
      or anything else; ``\\"`` and ``\\\\`` are the only escapes, or
    * a bare token, parsed as an ``int`` when it looks like one, else kept
      as a plain string (including the empty string for ``key:`` with
      nothing after it).
- Keys are not restricted to a fixed schema: unknown keys round-trip fine,
  since rows are plain dicts and Python dicts preserve insertion order.

This module intentionally does not implement general YAML -- only the
narrow subset above.
"""

import re

_BLANK_OR_COMMENT = re.compile(r"^\s*(#.*)?$")
_ROWS_HEADER = re.compile(r"^rows:\s*$")
_ROW_START = re.compile(r"^\s*-\s+([A-Za-z_][A-Za-z0-9_]*):\s?(.*)$")
_ROW_FIELD = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_]*):\s?(.*)$")

_SAFE_BARE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")


class BacklogParseError(ValueError):
    pass


def _parse_scalar(raw):
    s = raw.strip()
    if s == "":
        return ""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        out = []
        i = 0
        while i < len(inner):
            c = inner[i]
            if c == "\\" and i + 1 < len(inner) and inner[i + 1] in ('"', "\\"):
                out.append(inner[i + 1])
                i += 2
                continue
            out.append(c)
            i += 1
        return "".join(out)
    try:
        return int(s)
    except ValueError:
        return s


def loads(text):
    """Parse backlog YAML-subset text into a list of row dicts."""
    rows = []
    current = None
    saw_header = False

    for lineno, line in enumerate(text.splitlines(), start=1):
        if _BLANK_OR_COMMENT.match(line):
            continue
        if not saw_header:
            if _ROWS_HEADER.match(line):
                saw_header = True
                continue
            if line.strip() in ("rows: []", "rows: [ ]"):
                return []  # an explicitly empty backlog
            raise BacklogParseError(
                f"line {lineno}: expected top-level 'rows:' header, got {line!r}"
            )

        m = _ROW_START.match(line)
        if m:
            if current is not None:
                rows.append(current)
            current = {}
            key, val = m.group(1), m.group(2)
            current[key] = _parse_scalar(val)
            continue

        m = _ROW_FIELD.match(line)
        if m:
            if current is None:
                raise BacklogParseError(
                    f"line {lineno}: field outside of any row: {line!r}"
                )
            key, val = m.group(1), m.group(2)
            current[key] = _parse_scalar(val)
            continue

        raise BacklogParseError(f"line {lineno}: could not parse: {line!r}")

    if current is not None:
        rows.append(current)

    return rows


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return loads(f.read())


def _fmt_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    s = "" if value is None else str(value)
    if s != "" and _SAFE_BARE.match(s):
        return s
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dumps(rows):
    if isinstance(rows, dict):
        rows = rows.get("rows", [])
    """Serialize a list of row dicts back into the backlog YAML subset."""
    lines = ["rows:"]
    for row in rows:
        items = list(row.items())
        if not items:
            continue
        first_key, first_val = items[0]
        lines.append(f"  - {first_key}: {_fmt_scalar(first_val)}")
        for key, val in items[1:]:
            lines.append(f"    {key}: {_fmt_scalar(val)}")
    return "\n".join(lines) + "\n"


def dump(rows, path):
    # Accept both the bare list and the {"rows": [...]} wrapper.
    if isinstance(rows, dict):
        rows = rows.get("rows", [])
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(rows))
