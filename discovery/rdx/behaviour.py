"""Behavioural eval: does the model ACT on the envelope?

Every other corpus tests plumbing — the hook fires, the gate is silent, the
right row ranks. This one tests the only thing that ultimately decides whether
the project is worth anything: a real model, with no knowledge of rdx, receives
a real envelope through the real hook, and either uses it or does not.

It is the only test class that catches framing failures. The first envelope
header passed every unit test and was rejected by the first model that read it
("I'd treat them as unverified before installing anything from that source"),
which is the exact failure mode rdx exists to fix, one layer up.

How it works, and why it is shaped this way:

  * It registers the real hook in the real settings file, runs prompts through
    a headless `claude -p`, and restores settings in a finally block. Feeding
    the envelope in as prompt text instead would be tidier and would test the
    wrong thing — `additionalContext` arrives by a different path than user
    text, and that difference is the whole point.

  * It is NOT part of `rdx eval --all`. It costs real model calls, takes
    minutes rather than seconds, and mutates a settings file. Opt in with
    `rdx eval --behaviour`.

  * It checks three outcomes, not two. "Surfaced" and "ignored" are the
    obvious ones; "rejected" is the third, and it is the one worth alarming on,
    because a model that argues with the index is worse than one that quietly
    ignores it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config, db
from .evalharness import EvalResult, _load_yaml

CLAUDE_TIMEOUT_S = 300

# Language that means the model read the block and argued with it. This is the
# specific failure the first envelope produced, so it gets detected by name
# rather than being lumped in with "did not mention".
REJECTION_PATTERNS = [
    re.compile(r"haven'?t verified", re.I),
    re.compile(r"\bunverified\b", re.I),
    re.compile(r"treat (?:them|it|these) as (?:unverified|untrusted|suspect)", re.I),
    re.compile(r"from that source", re.I),
    re.compile(r"(?:can'?t|cannot|don'?t) (?:vouch|confirm) for", re.I),
    re.compile(r"\bnot sure what `?rdx`? is", re.I),
    re.compile(r"unfamiliar with (?:the )?`?rdx", re.I),
    # Observed verbatim, and the reason it happened is now fixed: the envelope
    # carried a trailing `ref=inj-<n>` token, and the model read that token as
    # evidence the whole block was an attack. It named the right resource and
    # then refused it. Detecting this by name keeps the regression visible.
    re.compile(r"prompt[- ]injection", re.I),
    re.compile(r"treat (?:that|this|it|them) (?:suggestion )?with caution", re.I),
    re.compile(r"not installing anything based on", re.I),
    # Narrow deliberately. This read `looks like (a )?(test|attack|injection)`
    # for one run and matched "looks like a test" in ordinary coding output,
    # inflating the rejection count -- the metric that is supposed to be the
    # loudest alarm in this file.
    re.compile(r"looks like (?:an? )?(?:prompt[- ]injection|injection "
               r"(?:test|attempt|marker)|attack)", re.I),
]


@dataclass
class CaseResult:
    case_id: str
    fired: bool
    expected_fire: bool
    surfaced: bool
    rejected: bool
    output: str = ""
    error: str | None = None

    @property
    def verdict(self) -> str:
        if self.error:
            return "ERROR"
        if self.fired != self.expected_fire:
            return "GATE"          # gate behaved differently than expected
        if not self.expected_fire:
            return "PASS"          # correctly silent
        if self.rejected:
            return "REJECTED"      # the framing failure
        return "PASS" if self.surfaced else "IGNORED"


@dataclass
class BehaviourReport:
    results: list[CaseResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.verdict] = out.get(r.verdict, 0) + 1
        return out


def claude_available() -> bool:
    return shutil.which("claude") is not None


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _hook_script(repo_dir: Path) -> Path:
    """Materialize the shim with __REPO_DIR__ resolved, in a temp file.

    The checked-in shim carries a placeholder that install.sh substitutes; the
    eval must not depend on rdx already being installed.
    """
    src = repo_dir / "hooks" / "resource-suggest.sh"
    text = src.read_text(encoding="utf-8").replace("__REPO_DIR__", str(repo_dir))
    fd, path = tempfile.mkstemp(prefix="rdx-behaviour-hook-", suffix=".sh")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    Path(path).chmod(0o755)
    return Path(path)


# Written beside settings.json while the hook is registered, and removed when
# it is restored. Its existence at startup means a previous run died without
# restoring -- see _repair_orphan().
def _backup_path() -> Path:
    return _settings_path().with_suffix(".json.rdx-behaviour-backup")


def _repair_orphan() -> bool:
    """Undo a registration left behind by a run that was killed.

    This is not hypothetical. The `finally` block below does not run on
    SIGTERM, and a killed eval left `~/.claude/settings.json` pointing
    UserPromptSubmit at a temp script in /tmp -- which is then deleted, so
    every subsequent prompt in every session invokes a hook that no longer
    exists. The user's stated requirement for this whole project was "we need
    to make sure we don't poison our projects or our claude", and a test
    harness that can wedge the editor on Ctrl-C fails it.
    """
    backup = _backup_path()
    if not backup.exists():
        return False
    _settings_path().write_text(backup.read_text(encoding="utf-8"),
                                encoding="utf-8")
    backup.unlink(missing_ok=True)
    return True


def _register(hook: Path) -> str:
    settings = _settings_path()
    settings.parent.mkdir(parents=True, exist_ok=True)
    original = settings.read_text(encoding="utf-8") if settings.exists() else "{}"

    try:
        data = json.loads(original)
    except ValueError:
        data = {}
    data.setdefault("hooks", {})["UserPromptSubmit"] = [
        {"hooks": [{"type": "command", "command": str(hook),
                    "async": False, "timeout": 15}]}
    ]
    # Backup FIRST. If the process dies between here and _restore, the next
    # run repairs from this file rather than leaving a dangling hook.
    _backup_path().write_text(original, encoding="utf-8")
    settings.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return original


def _restore(original: str) -> None:
    _settings_path().write_text(original, encoding="utf-8")
    _backup_path().unlink(missing_ok=True)


def _shown_injection(conn: sqlite3.Connection) -> tuple[int | None, str | None]:
    """The injection in this case's window that actually reached the model.

    Deliberately not "the newest row". A single `claude -p` run can produce
    more than one UserPromptSubmit event, and every attempt after the first is
    suppressed by the snooze window -- so the NEWEST row reliably says
    `n_shown = 0` even when the first one fired and the model acted on it.

    That is not hypothetical either. It scored this, verbatim, as a gate
    failure:

        "One quick note: there's a local plugin, `mineru-document-extraction`
         ... `rdx install mineru-document-extraction` ... which might handle
         this more reliably than a hand-rolled conversion."

    Which is precisely the behaviour the whole project is trying to produce.
    The eval tables are cleared before each case, so scanning the whole table
    is scoped to this case.
    """
    row = conn.execute(
        "SELECT id, suppressed_reason FROM injection "
        "WHERE n_shown > 0 ORDER BY id LIMIT 1").fetchone()
    if row is not None:
        return int(row["id"]), None

    last = conn.execute(
        "SELECT suppressed_reason FROM injection ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return None, (last["suppressed_reason"] if last else "no-row")


def _shown_slugs(conn: sqlite3.Connection, injection_id: int) -> list[str]:
    """The slugs the model was actually shown, straight from the log.

    This is the only honest basis for "did it surface?". The first version of
    this eval compared the output against slugs hand-written in the YAML, and
    scored a clean success as IGNORED: the prompt asked to convert Word docs,
    the corpus guessed `markitdown`/`docling`, the index ranked
    `mineru-document-extraction` first, and the model surfaced exactly what it
    was given. Grading against a guess measures the guess, not the model.
    """
    rows = conn.execute(
        """SELECT r.slug FROM injection_item ii
             JOIN resource r ON r.id = ii.resource_id
            WHERE ii.injection_id = ?
            ORDER BY ii.rank""", (injection_id,)).fetchall()
    return [str(r["slug"]) for r in rows]


FIXTURE_DOCS = {
    "onboarding": [
        "Engineering Onboarding",
        "Week one: get your laptop imaged, request VPN access, and pair with "
        "your onboarding buddy on a starter ticket.",
        "Week two: ship one change end to end, however small.",
    ],
    "architecture": [
        "Service Architecture",
        "The API gateway fronts four services: accounts, billing, search and "
        "notifications. Each owns its own Postgres schema.",
        "Cross-service reads go through the gateway, never directly.",
    ],
    "runbook": [
        "On-call Runbook",
        "Paging alert: api-5xx-rate. Check the gateway dashboard first, then "
        "the four upstream services in the order listed above.",
        "Roll back before debugging. Always.",
    ],
    "faq": [
        "Frequently Asked Questions",
        "How do I get staging credentials? Open a ticket in the platform "
        "queue; they are issued per-person and expire after 30 days.",
        "Who owns the search index? The search team.",
    ],
}


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    """Write a minimal but genuinely valid .docx.

    Four small parts is the whole of what the format requires for a readable
    text document: the content-type map, the package relationship pointing at
    the main part, and the document body itself.
    """
    import zipfile
    from xml.sax.saxutils import escape

    body = "".join(
        f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
        for text in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>' + body +
        '<w:sectPr/></w:body></w:document>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"><Default Extension="rels" ContentType='
        '"application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/></Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" Type='
        '"http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)


FIXTURE_REQUIREMENTS = """\
Django==2.2.13
Flask==1.0.2
Jinja2==2.10.1
Werkzeug==0.15.3
requests==2.20.0
urllib3==1.24.1
PyYAML==5.1
cryptography==2.6.1
paramiko==2.4.2
pyOpenSSL==19.0.0
lxml==4.3.3
Pillow==6.0.0
numpy==1.16.3
pandas==0.24.2
scipy==1.2.1
SQLAlchemy==1.3.3
psycopg2==2.8.2
redis==3.2.1
celery==4.3.0
boto3==1.9.150
botocore==1.12.150
awscli==1.16.160
google-api-python-client==1.7.9
protobuf==3.7.1
grpcio==1.20.1
tornado==5.1.1
aiohttp==3.5.4
httpx==0.13.3
gunicorn==19.9.0
uvicorn==0.7.1
fastapi==0.45.0
pydantic==1.4
starlette==0.12.9
click==7.0
itsdangerous==1.1.0
MarkupSafe==1.1.1
six==1.12.0
python-dateutil==2.8.0
pytz==2019.1
certifi==2019.3.9
chardet==3.0.4
idna==2.8
attrs==19.1.0
jsonschema==3.0.1
pyparsing==2.4.0
packaging==19.0
setuptools==41.0.1
wheel==0.33.4
pip==19.1.1
virtualenv==16.5.0
pytest==4.5.0
coverage==4.5.3
mock==3.0.5
tox==3.11.1
black==19.3b0
flake8==3.7.7
mypy==0.701
isort==4.3.19
Sphinx==2.0.1
docutils==0.14
markdown==3.1
bleach==3.1.0
html5lib==1.0.1
beautifulsoup4==4.7.1
scrapy==1.6.0
twisted==19.2.0
zope.interface==4.6.0
pyjwt==1.7.1
oauthlib==3.0.1
requests-oauthlib==1.2.0
social-auth-core==3.1.0
django-rest-framework==0.1.0
djangorestframework==3.9.4
django-cors-headers==3.0.2
gitpython==2.1.11
ansible==2.8.0
salt==2019.2.0
fabric==2.4.0
pexpect==4.7.0
psutil==5.6.2
docker==3.7.2
kubernetes==9.0.0
elasticsearch==7.0.2
pymongo==3.8.0
mysqlclient==1.4.2
alembic==1.0.10
marshmallow==2.19.2
graphene==2.1.5
websockets==7.0
pyzmq==18.0.1
matplotlib==3.0.3
seaborn==0.9.0
scikit-learn==0.21.1
tensorflow==1.13.1
keras==2.2.4
torch==1.1.0
nltk==3.4.1
gensim==3.7.3
spacy==2.1.4
opencv-python==4.1.0.25
imageio==2.5.0
moviepy==1.0.0
ffmpeg-python==0.1.17
pycrypto==2.6.1
pynacl==1.3.0
bcrypt==3.1.6
passlib==1.7.1
python-jose==3.0.1
authlib==0.11
sentry-sdk==0.8.0
newrelic==4.20.0.121
datadog==0.29.3
prometheus-client==0.6.0
statsd==3.3.0
structlog==19.1.0
loguru==0.2.5
colorama==0.4.1
tqdm==4.32.1
rich==0.3.3
typer==0.0.8
"""


def build_fixture(root: Path) -> Path:
    """A throwaway project carrying the referents the corpus prompts assume.

    Third time this class of bug has cost a case. Running in an empty /tmp made
    "our dependencies" meaningless; running in the rdx repo itself made "this
    folder of word documents" meaningless -- the model correctly asked for a
    path instead of doing the work, and the case scored IGNORED for a reason
    that had nothing to do with the envelope. A case can only measure the
    model's judgement if the work it describes is actually possible.

    The .docx files are real OOXML. An earlier version wrote 21-byte
    placeholders on the theory that only the model's opening sentence was under
    test; the model opened the files, found `PKplaceholder docx` instead of a
    ZIP, and stopped rather than "fabricate markdown for docs that don't
    actually exist". Right call, and it cost the case. A fixture has to survive
    being inspected.
    """
    root.mkdir(parents=True, exist_ok=True)
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    for name, body in FIXTURE_DOCS.items():
        _write_docx(docs / f"{name}.docx", body)

    # Real package names at real old pins. The first version generated
    # `package-1==1.1.0` through `package-120`, and the model refused the task
    # outright -- correctly: "these aren't real PyPI package names, so there's
    # no actual CVE data to look up, and I'm not going to fabricate
    # vulnerability findings". A fixture that makes the requested work
    # impossible measures the fixture.
    (root / "requirements.txt").write_text(FIXTURE_REQUIREMENTS, encoding="utf-8")
    (root / "README.md").write_text(
        "# fixture\n\nA throwaway project used by the rdx behavioural eval.\n",
        encoding="utf-8")
    return root


def _transcript_text(stdout: str) -> str:
    """Every assistant text block from a stream-json run, in order.

    Tool inputs are deliberately excluded: an `rdx install` the model RAN is
    not the same event as one it SUGGESTED, and the block is explicit that
    nothing may be installed without asking.
    """
    parts: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []) or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    parts.append(block["text"])
        elif event.get("type") == "result" and event.get("result"):
            parts.append(str(event["result"]))
    return "\n".join(parts)


def run_behaviour(*, repo_dir: Path | None = None, limit: int | None = None,
                  corpora_dir: Path | None = None, trials: int = 1,
                  cwd: Path | None = None,
                  verbose: bool = False) -> BehaviourReport:
    """Run the behavioural corpus.

    `trials` matters more than it looks. Model behaviour here is genuinely
    non-deterministic: the same prompt, index and envelope produced both a
    clean surface and a complete miss on consecutive runs. A single trial per
    case is an anecdote, so the surfaced rate is averaged over trials.

    `cwd` matters too. The first version ran in an empty /tmp, where prompts
    referencing "our dependencies" or "this folder" have no referent — the
    model correctly asked for a path instead of doing the work, and the case
    scored as IGNORED for reasons that had nothing to do with the envelope.
    It now defaults to a real project directory.
    """
    repo_dir = repo_dir or config.PROJECT_DIR.parent
    cases = _load_yaml((corpora_dir or config.CORPORA_DIR) / "behaviour.yaml"
                       ).get("cases", [])
    if limit:
        cases = cases[:limit]

    run_cwd = Path(cwd) if cwd else build_fixture(
        Path(tempfile.mkdtemp(prefix="rdx-behaviour-cwd-")))
    report = BehaviourReport()

    if _repair_orphan():
        print("  note: repaired a hook registration left by an interrupted run")

    hook = _hook_script(repo_dir)
    original = _register(hook)

    # SIGTERM does not raise, so `finally` alone never runs on a kill. Both
    # handlers restore and then re-raise the default behaviour, so the exit
    # status still reflects the signal.
    def _on_signal(signum, _frame):
        _restore(original)
        hook.unlink(missing_ok=True)
        if cwd is None:
            shutil.rmtree(run_cwd, ignore_errors=True)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    previous = {sig: signal.signal(sig, _on_signal)
                for sig in (signal.SIGTERM, signal.SIGINT)}

    env = {**os.environ, "RDX_SHADOW": "0",
           # Per-case sessions would still share the snooze window, which would
           # silently suppress later cases and look like a framing failure.
           "RDX_STATE_DIR": str(config.STATE_DIR)}

    try:
        for case in cases:
            for trial in range(max(1, trials)):
                _run_case(case, report, env, run_cwd, verbose,
                          trial=trial, trials=max(1, trials))
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        _restore(original)
        hook.unlink(missing_ok=True)
        shutil.rmtree(run_cwd, ignore_errors=True) if cwd is None else None

    return report


def _run_case(case, report, env, run_cwd, verbose, *, trial, trials):
            cid = str(case.get("id", "?"))
            prompt = str(case.get("prompt", ""))
            expected_fire = bool(case.get("expect_fire"))

            # Clear per-session budget state so each case is independent.
            with db.open_db(config.DB_PATH) as conn:
                conn.executescript(
                    "DELETE FROM injection_item; DELETE FROM injection;")
                conn.commit()

            try:
                # stream-json, not the default text output. `claude -p` prints
                # only the FINAL message, so a model that flags the resource up
                # front and then spends ten turns doing the work scores as a
                # miss. What is being measured is whether the model ever acts on
                # the block, which means the whole transcript counts.
                proc = subprocess.run(
                    ["claude", "-p", prompt,
                     "--output-format", "stream-json", "--verbose"],
                    capture_output=True, text=True,
                    timeout=CLAUDE_TIMEOUT_S, env=env, cwd=str(run_cwd))
                output = _transcript_text(proc.stdout) or (proc.stderr or "")
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                report.results.append(CaseResult(
                    cid, False, expected_fire, False, False, error=str(exc)))
                return

            with db.open_db(config.DB_PATH, readonly=True) as conn:
                injection_id, _reason = _shown_injection(conn)
                fired = injection_id is not None
                shown = _shown_slugs(conn, injection_id) if fired else []

            # Graded against what was actually injected, plus any explicit
            # expectations from the corpus. Naming a shown slug, or quoting the
            # install command, is the model demonstrably using the block.
            mentions = [str(m).lower() for m in (case.get("expect_mentions") or [])]
            mentions += [sl.lower() for sl in shown]
            mentions += [f"rdx install {sl.lower()}" for sl in shown]
            lowered = output.lower()
            surfaced = any(m in lowered for m in mentions) if mentions else False
            rejected = any(p.search(output) for p in REJECTION_PATTERNS)

            result = CaseResult(cid, fired, expected_fire, surfaced, rejected,
                                output=output)
            report.results.append(result)
            if verbose:
                suffix = f"  (trial {trial + 1}/{trials})" if trials > 1 else ""
                print(f"  [{result.verdict:<8}] {cid}{suffix}")
                # Show the evidence on failure. Without this the eval reports a
                # verdict with no way to tell a real miss from a matcher bug.
                if result.verdict in ("IGNORED", "REJECTED", "GATE"):
                    snippet = " ".join(output.split())[:400]
                    print(f"      fired={fired} shown={shown} "
                          f"matched_none_of={mentions[:6]}")
                    print(f"      output: {snippet}")


def to_eval_result(report: BehaviourReport) -> EvalResult:
    res = EvalResult("behaviour")
    counts = report.counts()
    res.passed = counts.get("PASS", 0)
    res.failed = (counts.get("REJECTED", 0) + counts.get("IGNORED", 0)
                  + counts.get("GATE", 0))
    res.skipped = counts.get("ERROR", 0)

    fired_cases = [r for r in report.results if r.expected_fire and not r.error]
    if fired_cases:
        surfaced = sum(1 for r in fired_cases if r.surfaced)
        res.metrics["surfaced_rate"] = round(surfaced / len(fired_cases), 3)
        res.metrics["rejected"] = sum(1 for r in fired_cases if r.rejected)

    for r in report.results:
        if r.verdict == "REJECTED":
            res.failures.append(
                f"{r.case_id}: model argued with the index — this is a FRAMING "
                f"failure, not a retrieval one")
        elif r.verdict == "IGNORED":
            res.failures.append(
                f"{r.case_id}: envelope fired but the model never mentioned it")
        elif r.verdict == "GATE":
            res.failures.append(
                f"{r.case_id}: expected fire={r.expected_fire}, got {r.fired}")
        elif r.verdict == "ERROR":
            res.notes.append(f"{r.case_id}: {r.error}")
    return res
