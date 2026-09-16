#!/usr/bin/env bash
# Tests for loop/statusline.sh.
#
# Run with:
#   bash loop/tests/test_statusline.sh
#
# Plain assertions; the whole script exits non-zero on the first failure.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUSLINE="$HERE/../statusline.sh"
FAILURES=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

assert_contains() {  # assert_contains <haystack> <needle> <description>
    if printf '%s' "$1" | grep -qF -- "$2"; then
        pass "$3"
    else
        fail "$3 (expected to find: $2)"
        printf '  --- actual output ---\n%s\n  ----------------------\n' "$1"
    fi
}

assert_not_contains() {
    if printf '%s' "$1" | grep -qF -- "$2"; then
        fail "$3 (did not expect to find: $2)"
        printf '  --- actual output ---\n%s\n  ----------------------\n' "$1"
    else
        pass "$3"
    fi
}

assert_line_count() {  # assert_line_count <output> <n> <description>
    local n
    n=$(printf '%s\n' "$1" | grep -c .)
    if [ "$n" -eq "$2" ]; then
        pass "$3"
    else
        fail "$3 (expected $2 non-empty lines, got $n)"
        printf '  --- actual output ---\n%s\n  ----------------------\n' "$1"
    fi
}

run_statusline() {  # run_statusline <payload-json> <rdx-sh-path>
    NIGHTSHIFT_RDX_SH="$2" bash "$STATUSLINE" <<< "$1"
}

# ---------------------------------------------------------------------------
# fixture project
# ---------------------------------------------------------------------------

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

mkdir -p "$TMP/.loop"

write_state() {
    cat > "$TMP/.loop/state.json"
}

write_scores() {
    cat > "$TMP/.loop/scores.jsonl"
}

payload() {  # payload <project_dir>
    printf '{"model":{"display_name":"Sonnet 5"},"cost":{"total_cost_usd":1.2345},"context_window":{"used_percentage":42.7},"workspace":{"project_dir":"%s"}}' "$1"
}

# ---------------------------------------------------------------------------
# 1. no rdx, no .loop/state.json — line 1 only
# ---------------------------------------------------------------------------

BARE="$(mktemp -d)"
out="$(run_statusline "$(payload "$BARE")" /nonexistent/rdx.sh)"
assert_line_count "$out" 1 "no state.json: exactly one line printed"
assert_contains "$out" "Sonnet 5" "no state.json: model name present"
assert_contains "$out" '$1.23' "no state.json: cost present"
assert_contains "$out" "ctx 43%" "no state.json: context pct present"
rm -rf "$BARE"

# ---------------------------------------------------------------------------
# 2. rdx present — line 1 includes its output
# ---------------------------------------------------------------------------

FAKE_RDX="$TMP/fake_rdx.sh"
cat > "$FAKE_RDX" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null   # consume stdin like the real rdx statusline does
echo "rdx 42 idx"
EOF
chmod +x "$FAKE_RDX"
out="$(run_statusline "$(payload "$BARE")" "$FAKE_RDX")"
assert_contains "$out" "rdx 42 idx" "rdx present: its line is appended to line 1"

# rdx absent (file doesn't exist) -> segment omitted, no crash
out="$(run_statusline "$(payload "$BARE")" "$TMP/does-not-exist.sh")"
assert_not_contains "$out" "rdx" "rdx absent: segment omitted"

# rdx present but fails -> segment omitted, no crash, exit 0
FAKE_RDX_BAD="$TMP/fake_rdx_bad.sh"
cat > "$FAKE_RDX_BAD" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
exit 1
EOF
chmod +x "$FAKE_RDX_BAD"
out="$(run_statusline "$(payload "$BARE")" "$FAKE_RDX_BAD")"
rc=$?
if [ "$rc" -eq 0 ]; then pass "rdx failing: statusline still exits 0"; else fail "rdx failing: statusline still exits 0 (got rc=$rc)"; fi
assert_line_count "$out" 1 "rdx failing: segment omitted, only line 1 printed"

# ---------------------------------------------------------------------------
# 3. running loop — line 2 with green glyph, sparkline, bar, live spend
# ---------------------------------------------------------------------------

write_state <<'EOF'
{"run":"loop/2026-09-16","iter":7,"phase":"EXEC","cap_usd":40,"spent_usd":18.4,
 "live_spend_usd":0.9,"score":71.4,"best":71.4,"delta":2.1,"flat":0,"rung":1,
 "task":"bl-014","agents":[{"id":"a1","type":"exec-sonnet","since":"2020-01-01T00:00:00Z"}],
 "stop_reason":null}
EOF
write_scores <<'EOF'
{"iter":0,"composite":60.0}
{"iter":1,"composite":62.0}
{"iter":2,"composite":64.0}
{"iter":3,"composite":63.0}
{"iter":4,"composite":68.0}
{"iter":5,"composite":70.0}
{"iter":6,"composite":69.0}
{"iter":7,"composite":71.4}
EOF
touch "$TMP/.loop/heartbeat"

out="$(run_statusline "$(payload "$TMP")" /nonexistent/rdx.sh)"
assert_line_count "$out" 3 "running loop: 3 lines printed (model, loop, agents)"
assert_contains "$out" "●" "running loop: green dot glyph"
assert_contains "$out" "loop/2026-09-16" "running loop: run name"
assert_contains "$out" "iter 7" "running loop: iter number"
assert_contains "$out" "EXEC" "running loop: phase"
assert_contains "$out" "score 71.4" "running loop: score"
assert_contains "$out" "▲2.1" "running loop: up arrow + delta"
assert_contains "$out" "best 71.4" "running loop: best"
assert_contains "$out" "flat 0/3" "running loop: flat counter"
assert_contains "$out" "rung 1" "running loop: rung"
assert_contains "$out" '$19.30~/$40.00' "running loop: live spend shown with tilde"
assert_contains "$out" "▮" "running loop: spend bar filled cells present"
assert_contains "$out" "↳" "running loop: agents line present"
assert_contains "$out" "exec-sonnet" "running loop: agent type shown"

# ---------------------------------------------------------------------------
# 4. stale heartbeat — amber warning glyph + "stale Nm" prefix
# ---------------------------------------------------------------------------

touch -d '-10 minutes' "$TMP/.loop/heartbeat"
out="$(run_statusline "$(payload "$TMP")" /nonexistent/rdx.sh)"
assert_contains "$out" "⚠" "stale heartbeat: warning glyph"
assert_contains "$out" "stale 10m" "stale heartbeat: stale-minutes prefix"
touch "$TMP/.loop/heartbeat"

# ---------------------------------------------------------------------------
# 5. STOPPED needs-human — amber half-circle, normal line shape
# ---------------------------------------------------------------------------

write_state <<'EOF'
{"run":"loop/2026-09-16","iter":9,"phase":"STOPPED","cap_usd":40,"spent_usd":22.1,
 "live_spend_usd":0,"score":75.0,"best":75.0,"delta":0,"flat":3,"rung":1,
 "task":"bl-020","agents":[],"stop_reason":"needs-human"}
EOF
out="$(run_statusline "$(payload "$TMP")" /nonexistent/rdx.sh)"
assert_contains "$out" "◐" "STOPPED needs-human: amber half-circle glyph"
assert_not_contains "$out" "■" "STOPPED needs-human: not the red special line"

# ---------------------------------------------------------------------------
# 6. STOPPED (other reason) — red glyph, special summary line
# ---------------------------------------------------------------------------

write_state <<'EOF'
{"run":"loop/2026-09-16","iter":9,"phase":"STOPPED","cap_usd":40,"spent_usd":22.1,
 "live_spend_usd":0,"score":75.0,"best":75.0,"delta":0,"flat":0,"rung":1,
 "task":"bl-020","agents":[],"stop_reason":"regression"}
EOF
write_scores <<'EOF'
{"iter":0,"composite":60.0}
{"iter":9,"composite":75.0}
EOF
out="$(run_statusline "$(payload "$TMP")" /nonexistent/rdx.sh)"
assert_contains "$out" "■" "STOPPED regression: red glyph"
assert_contains "$out" "loop STOPPED regression" "STOPPED regression: special line text"
assert_contains "$out" "9 iters" "STOPPED regression: iter count"
assert_contains "$out" "60.0→75.0" "STOPPED regression: first->last composite"
assert_contains "$out" '$22.10/$40.00' "STOPPED regression: spend/cap, no tilde"
assert_not_contains "$out" "flat" "STOPPED regression: does not use the normal line shape"

# ---------------------------------------------------------------------------
# 7. never errors on garbage / empty stdin
# ---------------------------------------------------------------------------

out="$(printf 'not json {{{' | NIGHTSHIFT_RDX_SH=/nonexistent bash "$STATUSLINE")"
rc=$?
if [ "$rc" -eq 0 ]; then pass "garbage stdin: exits 0"; else fail "garbage stdin: exits 0 (got rc=$rc)"; fi
assert_not_contains "$out" "error" "garbage stdin: no error text printed"
assert_not_contains "$out" "Traceback" "garbage stdin: no traceback printed"

out="$(printf '' | NIGHTSHIFT_RDX_SH=/nonexistent bash "$STATUSLINE")"
rc=$?
if [ "$rc" -eq 0 ]; then pass "empty stdin: exits 0"; else fail "empty stdin: exits 0 (got rc=$rc)"; fi

# ---------------------------------------------------------------------------
# 8. performance: must be comfortably under 100ms
# ---------------------------------------------------------------------------

start=$(date +%s%N)
run_statusline "$(payload "$TMP")" /nonexistent/rdx.sh >/dev/null
end=$(date +%s%N)
ms=$(( (end - start) / 1000000 ))
if [ "$ms" -lt 100 ]; then
    pass "performance: single run took ${ms}ms (< 100ms)"
else
    fail "performance: single run took ${ms}ms (>= 100ms)"
fi

# ---------------------------------------------------------------------------

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "all statusline tests passed"
    exit 0
else
    echo "$FAILURES statusline test(s) failed"
    exit 1
fi
