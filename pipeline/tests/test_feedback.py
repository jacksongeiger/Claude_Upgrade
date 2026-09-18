"""Tests for pipeline/feedback.py.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest \
        pipeline/tests/test_feedback.py -q -p no:cacheprovider
"""
import json
import subprocess
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1]
ROOT = PIPELINE_DIR.parent
FEEDBACK = PIPELINE_DIR / "feedback.py"

sys.path.insert(0, str(ROOT / "loop"))
import backlog_io  # noqa: E402

INBOX = """\
## 2026-09-10
- App crashed with a traceback when saving
- Search feels slow, takes 10 seconds

## 2026-09-12
- I can't find the export button anywhere
"""


def run_feedback(args, cwd):
    return subprocess.run([sys.executable, str(FEEDBACK)] + list(args),
                           cwd=str(cwd), capture_output=True, text=True)


def read_backlog(path):
    if not Path(path).exists():
        return []
    return backlog_io.load(str(path))


def setup_inbox(tmp_path, text=INBOX):
    p = tmp_path / "FEEDBACK.md"
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# basic run: three bullets, two headings, keyword mapping
# ---------------------------------------------------------------------------

def test_three_bullets_mapped_and_added(tmp_path):
    inbox = setup_inbox(tmp_path)
    backlog = tmp_path / "backlog.yaml"

    proc = run_feedback(["--inbox", str(inbox), "--backlog", str(backlog)], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "added 3 rows" in proc.stdout

    rows = read_backlog(backlog)
    assert len(rows) == 3

    by_title = {r["title"]: r for r in rows}
    crash = by_title["App crashed with a traceback when saving"]
    assert crash["dimension"] == "tests"
    assert crash["status"] == "open"
    assert crash["source"] == "production"
    assert crash["rung"] == 1
    assert crash["est"] == "S"
    assert crash["attempts"] == 0
    assert crash["iter_added"] == 0
    assert crash["note"] == "from FEEDBACK.md 2026-09-10"
    assert crash["id"].startswith("fb-") and len(crash["id"]) == 11

    slow = by_title["Search feels slow, takes 10 seconds"]
    assert slow["dimension"] == "perf"
    assert slow["status"] == "open"

    confuse = by_title["I can't find the export button anywhere"]
    assert confuse["dimension"] == "persona"
    assert confuse["status"] == "open"
    assert confuse["note"] == "from FEEDBACK.md 2026-09-12"


def test_none_dimension_is_needs_human(tmp_path):
    inbox = setup_inbox(tmp_path, "## 2026-09-10\n- The button color looks off to me\n")
    backlog = tmp_path / "backlog.yaml"
    run_feedback(["--inbox", str(inbox), "--backlog", str(backlog)], tmp_path)
    rows = read_backlog(backlog)
    assert len(rows) == 1
    assert rows[0]["dimension"] == "none"
    assert rows[0]["status"] == "needs-human"


# ---------------------------------------------------------------------------
# heading rewrite
# ---------------------------------------------------------------------------

def test_processed_headings_rewritten_bullets_kept(tmp_path):
    inbox = setup_inbox(tmp_path)
    backlog = tmp_path / "backlog.yaml"
    run_feedback(["--inbox", str(inbox), "--backlog", str(backlog)], tmp_path)

    text = inbox.read_text()
    assert "## processed 2026-09-10" in text
    assert "## processed 2026-09-12" in text
    assert "- App crashed with a traceback when saving" in text
    assert "- Search feels slow, takes 10 seconds" in text
    assert "- I can't find the export button anywhere" in text
    # nothing left unprocessed
    assert "## 2026-09-10" not in text.replace("## processed 2026-09-10", "")
    assert "## 2026-09-12" not in text.replace("## processed 2026-09-12", "")


def test_already_processed_heading_is_left_alone_and_not_reread(tmp_path):
    text = "## processed 2026-09-01\n- Old bullet, should not be re-added\n"
    inbox = setup_inbox(tmp_path, text)
    backlog = tmp_path / "backlog.yaml"
    proc = run_feedback(["--inbox", str(inbox), "--backlog", str(backlog)], tmp_path)
    assert "added 0 rows" in proc.stdout
    assert read_backlog(backlog) == []
    assert inbox.read_text() == text


# ---------------------------------------------------------------------------
# dedupe across runs
# ---------------------------------------------------------------------------

def test_dedupe_on_second_run_with_new_heading(tmp_path):
    inbox = setup_inbox(tmp_path)
    backlog = tmp_path / "backlog.yaml"
    run_feedback(["--inbox", str(inbox), "--backlog", str(backlog)], tmp_path)
    assert len(read_backlog(backlog)) == 3

    # same bullet text resurfaces under a brand new, unprocessed heading
    inbox.write_text(inbox.read_text() + "\n## 2026-09-15\n- App crashed with a traceback when saving\n")
    proc = run_feedback(["--inbox", str(inbox), "--backlog", str(backlog)], tmp_path)
    assert "added 0 rows" in proc.stdout
    rows = read_backlog(backlog)
    assert len(rows) == 3  # no duplicate appended
    assert "## processed 2026-09-15" in inbox.read_text()


def test_dedupe_within_same_run(tmp_path):
    inbox = setup_inbox(
        tmp_path,
        "## 2026-09-10\n- App crashed with a traceback when saving\n"
        "- App crashed with a traceback when saving\n",
    )
    backlog = tmp_path / "backlog.yaml"
    proc = run_feedback(["--inbox", str(inbox), "--backlog", str(backlog)], tmp_path)
    assert "added 1 rows" in proc.stdout
    assert len(read_backlog(backlog)) == 1


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------

def test_dry_run_leaves_files_untouched(tmp_path):
    inbox = setup_inbox(tmp_path)
    backlog = tmp_path / "backlog.yaml"
    inbox_before = inbox.read_text()

    proc = run_feedback(["--inbox", str(inbox), "--backlog", str(backlog), "--dry-run"], tmp_path)
    assert proc.returncode == 0
    assert "added 3 rows" in proc.stdout
    assert "App crashed with a traceback when saving" in proc.stdout

    assert not backlog.exists()
    assert inbox.read_text() == inbox_before


# ---------------------------------------------------------------------------
# sentry export
# ---------------------------------------------------------------------------

def test_sentry_export_adds_tests_rows(tmp_path):
    inbox = setup_inbox(tmp_path, "")
    backlog = tmp_path / "backlog.yaml"
    sentry = tmp_path / "export.json"
    sentry.write_text(json.dumps([
        {"title": "TypeError in checkout()", "count": 42, "culprit": "checkout.js"},
        {"title": "NullPointerException in save()", "count": 7, "culprit": "save.py"},
    ]))

    proc = run_feedback(["--inbox", str(inbox), "--backlog", str(backlog), "--sentry", str(sentry)], tmp_path)
    assert proc.returncode == 0
    assert "added 2 rows" in proc.stdout

    rows = read_backlog(backlog)
    assert len(rows) == 2
    by_title = {r["title"]: r for r in rows}
    row = by_title["TypeError in checkout()"]
    assert row["dimension"] == "tests"
    assert row["status"] == "open"
    assert row["source"] == "production"
    assert "42" in row["note"] and "checkout.js" in row["note"]


def test_sentry_dedupes_against_existing_backlog(tmp_path):
    inbox = setup_inbox(tmp_path, "")
    backlog = tmp_path / "backlog.yaml"
    sentry = tmp_path / "export.json"
    entries = [{"title": "TypeError in checkout()", "count": 1, "culprit": "checkout.js"}]
    sentry.write_text(json.dumps(entries))

    run_feedback(["--inbox", str(inbox), "--backlog", str(backlog), "--sentry", str(sentry)], tmp_path)
    assert len(read_backlog(backlog)) == 1

    sentry.write_text(json.dumps(entries))  # same title again
    proc = run_feedback(["--inbox", str(inbox), "--backlog", str(backlog), "--sentry", str(sentry)], tmp_path)
    assert "added 0 rows" in proc.stdout
    assert len(read_backlog(backlog)) == 1


def test_existing_backlog_rows_are_preserved(tmp_path):
    backlog = tmp_path / "backlog.yaml"
    backlog_io.dump({"rows": [
        {"id": "bl-001", "title": "pre-existing row", "dimension": "tests", "est": "M",
         "source": "todo", "status": "open", "rung": 1, "attempts": 0, "iter_added": 0, "note": ""},
    ]}, str(backlog))
    inbox = setup_inbox(tmp_path)

    run_feedback(["--inbox", str(inbox), "--backlog", str(backlog)], tmp_path)
    rows = read_backlog(backlog)
    ids = [r["id"] for r in rows]
    assert "bl-001" in ids
    assert len(rows) == 4
