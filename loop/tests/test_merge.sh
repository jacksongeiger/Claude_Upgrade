#!/usr/bin/env bash
# Plain-bash tests for loop/merge.sh. Builds throwaway git repos under
# mktemp for each case. Exits non-zero if any assertion fails.
#
# Uses tests/fixtures/fake_tests_scorer.py in place of the real
# loop/scorers/tests.py (built in parallel) — same CLI/JSON contract, but its
# result is read from a `tests_state.json` file tracked in the repo, so a
# git merge can change "which tests fail" like a real suite would.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOP_DIR="$(cd "$HERE/.." && pwd)"
MERGE_SH="$LOOP_DIR/merge.sh"
FAKE_SCORER="$HERE/fixtures/fake_tests_scorer.py"

FAILED=0
ALL_TMP=()
trap 'rm -rf "${ALL_TMP[@]}" 2>/dev/null || true' EXIT

fail() { echo "FAIL: $*" >&2; FAILED=1; }
pass() { echo "ok - $*"; }

newtmp() {
    local d
    d=$(mktemp -d)
    ALL_TMP+=("$d")
    printf '%s' "$d"
}

tmp_path() { # a not-yet-existing path, for git worktree add to create
    printf '%s/wt' "$(newtmp)"
}

new_repo() {
    local dir
    dir=$(newtmp)
    git init -q -b trunk "$dir" >/dev/null
    git -C "$dir" config user.email "test@example.com"
    git -C "$dir" config user.name "Nightshift Test"
    printf 'def main():\n    return 1\n' > "$dir/main.py"
    printf '{"n_tests": 3, "failing": []}\n' > "$dir/tests_state.json"
    git -C "$dir" add -A
    git -C "$dir" commit -q -m base
    printf '%s' "$dir"
}

new_branch_wt() { # repo branch wtpath
    git -C "$1" worktree add -q -b "$2" "$3" trunk
}

write_config() { # path max_files
    local path="$1" maxf="${2:-25}"
    jq -n --argjson mf "$maxf" \
        '{test_cmd:"true", max_files_per_iteration:$mf, scorers:[{name:"tests", cmd:"true"}]}' \
        > "$path"
}

write_plan() { # path "id:owned1,owned2" ...
    local path="$1"; shift
    local st='[]'
    for spec in "$@"; do
        local id="${spec%%:*}" owned="${spec#*:}"
        st=$(jq --arg id "$id" --arg owned "$owned" \
            '. + [{id:$id, owned_paths: ($owned | split(",") | map(select(length>0)))}]' <<<"$st")
    done
    jq -n --argjson st "$st" '{iter:1, task_ids: ($st | map(.id)), subtasks: $st, decisions: []}' > "$path"
}

write_report() { # iter_dir id branch worktree
    local iter_dir="$1" id="$2" branch="$3" wt="$4"
    mkdir -p "$iter_dir/tasks/$id"
    jq -n --arg id "$id" --arg branch "$branch" --arg wt "$wt" \
        '{id:$id, status:"done", commit:"", branch:$branch, worktree:$wt,
          files:[], test_output_tail:"", question:null, decisions_made:[]}' \
        > "$iter_dir/tasks/$id/report.json"
}

run_merge() { # repo iter_dir id...
    local repo="$1" iter_dir="$2"; shift 2
    ( cd "$repo" && NIGHTSHIFT_CONFIG="$iter_dir/config.json" NIGHTSHIFT_TESTS_SCORER="$FAKE_SCORER" \
        bash "$MERGE_SH" "$iter_dir" "$@" ) 2>&1
}

# --- cases ------------------------------------------------------------

test_clean_merge() {
    local repo wt iter_dir out code
    repo=$(new_repo)
    wt=$(tmp_path)
    new_branch_wt "$repo" "loop/t-clean" "$wt"
    printf 'def main():\n    return 2\n' > "$wt/main.py"
    git -C "$wt" commit -aqm "clean change"

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-clean:main.py"
    write_report "$iter_dir" "t-clean" "loop/t-clean" "$wt"

    out=$(run_merge "$repo" "$iter_dir" t-clean); code=$?

    [ "$code" -eq 0 ] && pass "clean merge: exit 0" || fail "clean merge: exit $code: $out"
    echo "$out" | grep -q "^MERGED t-clean " && pass "clean merge: MERGED line" || fail "clean merge: no MERGED line: $out"
    [ "$(jq -r '.merged[0] // empty' "$iter_dir/merge.json")" = "t-clean" ] \
        && pass "clean merge: merge.json.merged" || fail "clean merge: merge.json wrong: $(cat "$iter_dir/merge.json")"
    [ -d "$wt" ] && fail "clean merge: worktree not removed" || pass "clean merge: worktree removed"
    git -C "$repo" rev-parse --verify "refs/heads/loop/t-clean" >/dev/null 2>&1 \
        && fail "clean merge: branch not deleted" || pass "clean merge: branch deleted"
    [ "$(jq -r '.n_tests' "$iter_dir/floor.json" 2>/dev/null)" = "3" ] \
        && pass "clean merge: floor.json cached" || fail "clean merge: floor.json wrong: $(cat "$iter_dir/floor.json" 2>/dev/null)"
}

test_floor_newly_failing() {
    local repo wt iter_dir out code head_before head_after
    repo=$(new_repo)
    wt=$(tmp_path)
    new_branch_wt "$repo" "loop/t-bad" "$wt"
    printf 'def main():\n    return 2\n' > "$wt/main.py"
    printf '{"n_tests": 3, "failing": ["tests/test_x.py::test_new"]}\n' > "$wt/tests_state.json"
    git -C "$wt" commit -aqm "introduces a failing test"

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-bad:main.py,tests_state.json"
    write_report "$iter_dir" "t-bad" "loop/t-bad" "$wt"

    head_before=$(git -C "$repo" rev-parse HEAD)
    out=$(run_merge "$repo" "$iter_dir" t-bad); code=$?
    head_after=$(git -C "$repo" rev-parse HEAD)

    [ "$code" -eq 5 ] && pass "floor: exit 5" || fail "floor: exit $code: $out"
    echo "$out" | grep -q "^FLOOR t-bad newly-failing:.*test_new" \
        && pass "floor: FLOOR line names new failure" || fail "floor: no FLOOR line: $out"
    [ "$head_before" = "$head_after" ] && pass "floor: HEAD reset" || fail "floor: HEAD not reset"
    jq -e '.rejected["t-bad"]' "$iter_dir/merge.json" >/dev/null \
        && pass "floor: merge.json.rejected" || fail "floor: merge.json missing rejection: $(cat "$iter_dir/merge.json")"
    git -C "$repo" tag -l "nightshift/rejected/t-bad" | grep -q . \
        && pass "floor: rejected branch tagged" || fail "floor: branch not tagged"
    [ -d "$wt" ] && pass "floor: worktree kept for inspection" || fail "floor: worktree removed"
}

test_scope_violation() {
    local repo wt iter_dir out code head_before head_after
    repo=$(new_repo)
    wt=$(tmp_path)
    new_branch_wt "$repo" "loop/t-scope" "$wt"
    printf 'def main():\n    return 2\n' > "$wt/main.py"
    printf 'SECRET=1\n' > "$wt/secret.py"
    git -C "$wt" add -A
    git -C "$wt" commit -qm "touches a file outside owned_paths"

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-scope:main.py"
    write_report "$iter_dir" "t-scope" "loop/t-scope" "$wt"

    head_before=$(git -C "$repo" rev-parse HEAD)
    out=$(run_merge "$repo" "$iter_dir" t-scope); code=$?
    head_after=$(git -C "$repo" rev-parse HEAD)

    [ "$code" -eq 5 ] && pass "scope: exit 5" || fail "scope: exit $code: $out"
    echo "$out" | grep -q "^SCOPE t-scope .*secret.py" \
        && pass "scope: SCOPE line names stray file" || fail "scope: no SCOPE line: $out"
    [ "$head_before" = "$head_after" ] && pass "scope: HEAD reset" || fail "scope: HEAD not reset"
}

test_conflict() {
    local repo wt iter_dir out code head_before head_after
    repo=$(new_repo)
    wt=$(tmp_path)
    new_branch_wt "$repo" "loop/t-conflict" "$wt"
    sed -i 's/return 1/return 99/' "$wt/main.py"
    git -C "$wt" commit -aqm "branch-side change"
    sed -i 's/return 1/return 2/' "$repo/main.py"
    git -C "$repo" commit -aqm "loop-side change to the same line"

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-conflict:main.py"
    write_report "$iter_dir" "t-conflict" "loop/t-conflict" "$wt"

    head_before=$(git -C "$repo" rev-parse HEAD)
    out=$(run_merge "$repo" "$iter_dir" t-conflict); code=$?
    head_after=$(git -C "$repo" rev-parse HEAD)

    [ "$code" -eq 5 ] && pass "conflict: exit 5" || fail "conflict: exit $code: $out"
    echo "$out" | grep -q "^CONFLICT t-conflict$" && pass "conflict: CONFLICT line" || fail "conflict: no CONFLICT line: $out"
    [ "$head_before" = "$head_after" ] && pass "conflict: HEAD unchanged" || fail "conflict: HEAD moved"
    [ -z "$(git -C "$repo" status --porcelain)" ] && pass "conflict: working tree clean after abort" \
        || fail "conflict: working tree dirty after abort"
}

test_mixed_merge_and_reject() {
    local repo wt_ok wt_bad iter_dir out code
    repo=$(new_repo)
    wt_ok=$(tmp_path)
    new_branch_wt "$repo" "loop/t-ok" "$wt_ok"
    printf 'def main():\n    return 2\n' > "$wt_ok/main.py"
    git -C "$wt_ok" commit -aqm "ok change"

    wt_bad=$(tmp_path)
    new_branch_wt "$repo" "loop/t-bad2" "$wt_bad"
    printf 'stray=1\n' > "$wt_bad/stray.py"
    git -C "$wt_bad" add -A
    git -C "$wt_bad" commit -qm "stray file"

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-ok:main.py" "t-bad2:main.py"
    write_report "$iter_dir" "t-ok" "loop/t-ok" "$wt_ok"
    write_report "$iter_dir" "t-bad2" "loop/t-bad2" "$wt_bad"

    out=$(run_merge "$repo" "$iter_dir" t-ok t-bad2); code=$?

    [ "$code" -eq 0 ] && pass "mixed: exit 0" || fail "mixed: exit $code: $out"
    [ "$(jq -c '.merged' "$iter_dir/merge.json")" = '["t-ok"]' ] \
        && pass "mixed: merge.json.merged" || fail "mixed: merge.json wrong: $(cat "$iter_dir/merge.json")"
    jq -e '.rejected["t-bad2"]' "$iter_dir/merge.json" >/dev/null \
        && pass "mixed: merge.json.rejected" || fail "mixed: merge.json missing t-bad2"
}

test_all_rejected() {
    local repo wt1 wt2 iter_dir out code
    repo=$(new_repo)
    wt1=$(tmp_path)
    new_branch_wt "$repo" "loop/t-r1" "$wt1"
    printf 'stray=1\n' > "$wt1/stray1.py"; git -C "$wt1" add -A; git -C "$wt1" commit -qm s1

    wt2=$(tmp_path)
    new_branch_wt "$repo" "loop/t-r2" "$wt2"
    printf 'stray=2\n' > "$wt2/stray2.py"; git -C "$wt2" add -A; git -C "$wt2" commit -qm s2

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-r1:main.py" "t-r2:main.py"
    write_report "$iter_dir" "t-r1" "loop/t-r1" "$wt1"
    write_report "$iter_dir" "t-r2" "loop/t-r2" "$wt2"

    out=$(run_merge "$repo" "$iter_dir" t-r1 t-r2); code=$?

    [ "$code" -eq 5 ] && pass "all-rejected: exit 5" || fail "all-rejected: exit $code: $out"
    [ "$(jq -c '.merged' "$iter_dir/merge.json")" = '[]' ] \
        && pass "all-rejected: merge.json.merged empty" || fail "all-rejected: merge.json wrong: $(cat "$iter_dir/merge.json")"
}

test_merge_json_is_cumulative() {
    # the planner merges first-pass approvals, then a revised subtask later:
    # the second call must not erase the first record
    local repo wt1 wt2 iter_dir out
    repo=$(new_repo)
    wt1=$(tmp_path); new_branch_wt "$repo" "loop/t-a" "$wt1"
    printf 'A\n' > "$wt1/a.txt"; git -C "$wt1" add a.txt; git -C "$wt1" commit -qm "a"
    wt2=$(tmp_path); new_branch_wt "$repo" "loop/t-b" "$wt2"
    printf 'B\n' > "$wt2/b.txt"; git -C "$wt2" add b.txt; git -C "$wt2" commit -qm "b"
    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-a:a.txt" "t-b:b.txt"
    write_report "$iter_dir" "t-a" "loop/t-a" "$wt1"
    write_report "$iter_dir" "t-b" "loop/t-b" "$wt2"
    run_merge "$repo" "$iter_dir" t-a >/dev/null
    run_merge "$repo" "$iter_dir" t-b >/dev/null
    out=$(jq -c '.merged' "$iter_dir/merge.json")
    [ "$out" = '["t-a","t-b"]' ] && pass "merge.json cumulative across calls" || fail "merge.json=$out"
}

# --- bonus coverage (spec items 4/5, beyond the required case list) ------

test_scope_drift() {
    local repo wt iter_dir out code head_before head_after
    repo=$(new_repo)
    wt=$(tmp_path)
    new_branch_wt "$repo" "loop/t-drift" "$wt"
    printf 'def main():\n    return 2\n' > "$wt/main.py"
    printf '{"n_tests": 3, "failing": [], "note": "touched"}\n' > "$wt/tests_state.json"
    git -C "$wt" commit -aqm "touches two owned files"

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 1   # max_files_per_iteration = 1
    write_plan "$iter_dir/plan.json" "t-drift:main.py,tests_state.json"
    write_report "$iter_dir" "t-drift" "loop/t-drift" "$wt"

    head_before=$(git -C "$repo" rev-parse HEAD)
    out=$(run_merge "$repo" "$iter_dir" t-drift); code=$?
    head_after=$(git -C "$repo" rev-parse HEAD)

    [ "$code" -eq 5 ] && pass "scope-drift: exit 5" || fail "scope-drift: exit $code: $out"
    echo "$out" | grep -q "^SCOPE-DRIFT t-drift$" && pass "scope-drift: line" || fail "scope-drift: no line: $out"
    [ "$head_before" = "$head_after" ] && pass "scope-drift: HEAD reset" || fail "scope-drift: HEAD not reset"
}

test_tests_removed_without_approval() {
    local repo wt iter_dir out code head_before head_after
    repo=$(new_repo)
    wt=$(tmp_path)
    new_branch_wt "$repo" "loop/t-remove" "$wt"
    printf 'def main():\n    return 2\n' > "$wt/main.py"
    printf '{"n_tests": 2, "failing": []}\n' > "$wt/tests_state.json"
    git -C "$wt" commit -aqm "removes a test, unreviewed"

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-remove:main.py,tests_state.json"
    write_report "$iter_dir" "t-remove" "loop/t-remove" "$wt"

    head_before=$(git -C "$repo" rev-parse HEAD)
    out=$(run_merge "$repo" "$iter_dir" t-remove); code=$?
    head_after=$(git -C "$repo" rev-parse HEAD)

    [ "$code" -eq 5 ] && pass "tests-removed unapproved: exit 5" || fail "tests-removed unapproved: exit $code: $out"
    echo "$out" | grep -q "^FLOOR t-remove tests removed$" \
        && pass "tests-removed unapproved: FLOOR line" || fail "tests-removed unapproved: no line: $out"
    [ "$head_before" = "$head_after" ] && pass "tests-removed unapproved: HEAD reset" || fail "tests-removed unapproved: HEAD not reset"
}

test_tests_removed_with_approval() {
    local repo wt iter_dir out code
    repo=$(new_repo)
    wt=$(tmp_path)
    new_branch_wt "$repo" "loop/t-remove-ok" "$wt"
    printf 'def main():\n    return 2\n' > "$wt/main.py"
    printf '{"n_tests": 2, "failing": []}\n' > "$wt/tests_state.json"
    git -C "$wt" commit -aqm "removes a test, reviewer approved"

    iter_dir=$(newtmp)
    write_config "$iter_dir/config.json" 25
    write_plan "$iter_dir/plan.json" "t-remove-ok:main.py,tests_state.json"
    write_report "$iter_dir" "t-remove-ok" "loop/t-remove-ok" "$wt"
    jq -n '{verdict:"approve", test_delta:{removed:1}}' > "$iter_dir/tasks/t-remove-ok/review.json"

    out=$(run_merge "$repo" "$iter_dir" t-remove-ok); code=$?

    [ "$code" -eq 0 ] && pass "tests-removed approved: exit 0" || fail "tests-removed approved: exit $code: $out"
    echo "$out" | grep -q "^MERGED t-remove-ok " \
        && pass "tests-removed approved: MERGED line" || fail "tests-removed approved: no MERGED line: $out"
}

test_bad_args() {
    local out code
    out=$(bash "$MERGE_SH" 2>&1); code=$?
    [ "$code" -eq 1 ] && pass "bad args: exit 1" || fail "bad args: exit $code: $out"
}

# --- run --------------------------------------------------------------

test_clean_merge
test_floor_newly_failing
test_scope_violation
test_conflict
test_mixed_merge_and_reject
test_all_rejected
test_scope_drift
test_tests_removed_without_approval
test_tests_removed_with_approval
test_merge_json_is_cumulative
test_bad_args

if [ "$FAILED" -ne 0 ]; then
    echo "=== test_merge.sh: FAILURES ===" >&2
    exit 1
fi
echo "=== test_merge.sh: all passed ==="
exit 0
