#!/usr/bin/env bash
# Nightshift merge step — see loop/README.md ("merge.json", the tests hard
# floor) for the contract this implements.
#
# Usage: bash merge.sh <iter_dir> <subtask-id>...
# Run from inside the loop worktree, on the loop branch.
#
# Env:
#   NIGHTSHIFT_CONFIG       path to config.json (required) — test_cmd,
#                           max_files_per_iteration, scorers[].
#   NIGHTSHIFT_TESTS_SCORER override for the tests scorer script path
#                           (defaults to <this dir>/scorers/tests.py).
#
# Exit codes: 0 at least one id merged · 5 every id rejected · 1 bad args/error.

set -uo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { echo "usage: merge.sh <iter_dir> <subtask-id>..." >&2; }

if [ "$#" -lt 2 ]; then
    usage
    exit 1
fi

iter_dir_arg="$1"; shift
ids=("$@")

if [ -z "$iter_dir_arg" ] || [ ! -d "$iter_dir_arg" ]; then
    echo "merge.sh: iter_dir not found: $iter_dir_arg" >&2
    exit 1
fi
iter_dir="$(cd "$iter_dir_arg" && pwd)"

plan_json="$iter_dir/plan.json"
if [ ! -f "$plan_json" ]; then
    echo "merge.sh: plan.json not found in $iter_dir" >&2
    exit 1
fi

if [ -z "${NIGHTSHIFT_CONFIG:-}" ] || [ ! -f "$NIGHTSHIFT_CONFIG" ]; then
    echo "merge.sh: NIGHTSHIFT_CONFIG must point to an existing config.json" >&2
    exit 1
fi

command -v jq >/dev/null 2>&1 || { echo "merge.sh: jq is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "merge.sh: python3 is required" >&2; exit 1; }

git rev-parse --git-dir >/dev/null 2>&1 || { echo "merge.sh: not inside a git repo" >&2; exit 1; }

# --- config -----------------------------------------------------------
tests_cfg=$(jq -c '([.scorers[]? | select(.name=="tests")] | .[0]) // empty' "$NIGHTSHIFT_CONFIG")
if [ -z "$tests_cfg" ] || [ "$tests_cfg" = "null" ]; then
    test_cmd=$(jq -r '.test_cmd // empty' "$NIGHTSHIFT_CONFIG")
    tests_cfg=$(jq -cn --arg cmd "$test_cmd" '{name:"tests",cmd:$cmd}')
fi
max_files=$(jq -r '.max_files_per_iteration // 25' "$NIGHTSHIFT_CONFIG")

TESTS_SCORER="${NIGHTSHIFT_TESTS_SCORER:-$KIT_DIR/scorers/tests.py}"
if [ ! -f "$TESTS_SCORER" ]; then
    echo "merge.sh: tests scorer not found at $TESTS_SCORER" >&2
    exit 1
fi

# --- paths --------------------------------------------------------------
common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
project_root=$(dirname "$common_dir")
mkdir -p "$project_root/.loop" 2>/dev/null || true
events_jsonl="$project_root/.loop/events.jsonl"
events_log="$project_root/.loop/events.log"

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

merge_json="$iter_dir/merge.json"
# Cumulative: the planner may merge the first-pass approvals, then a revised
# subtask later in the same iteration; the record must keep both.
[ -s "$merge_json" ] && jq -e '.merged and .rejected' "$merge_json" >/dev/null 2>&1 || echo '{"merged":[],"rejected":{}}' > "$merge_json"

# --- helpers --------------------------------------------------------------

add_merged() {
    jq --arg id "$1" '.merged += [$id]' "$merge_json" > "$merge_json.tmp" && mv "$merge_json.tmp" "$merge_json"
}

add_rejected() {
    jq --arg id "$1" --arg r "$2" '.rejected[$id] = $r' "$merge_json" > "$merge_json.tmp" && mv "$merge_json.tmp" "$merge_json"
}

log_merged_event() {
    local id="$1" sha="$2" ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    jq -cn --arg ts "$ts" --arg id "$id" --arg sha "$sha" \
        '{ts:$ts,event:"merged",id:$id,sha:$sha}' >> "$events_jsonl" 2>/dev/null
    printf '%s MERGED %s %s\n' "$ts" "$id" "$sha" >> "$events_log" 2>/dev/null
}

tag_rejected_branch() {
    local id="$1" branch="$2"
    [ -n "$branch" ] || return 0
    git rev-parse --verify "$branch" >/dev/null 2>&1 || return 0
    git tag -f "nightshift/rejected/$id" "$branch" >/dev/null 2>&1 || true
}

# Runs the tests scorer against the current worktree. Sets SCORE_OK,
# SCORE_ERROR, SCORE_NTESTS and writes the (unsorted) failing test ids, one
# per line, to $1.
run_tests_scorer() {
    local outfile="$1" out
    out=$(python3 "$TESTS_SCORER" --config "$tests_cfg" --workdir . 2>"$scratch/scorer_err")
    if [ -z "$out" ]; then
        SCORE_OK="false"
        SCORE_ERROR="scorer produced no output: $(cat "$scratch/scorer_err" 2>/dev/null)"
        : > "$outfile"
        return 1
    fi
    SCORE_OK=$(printf '%s' "$out" | jq -r '.ok // false')
    SCORE_ERROR=$(printf '%s' "$out" | jq -r '.error // empty')
    SCORE_NTESTS=$(printf '%s' "$out" | jq -r '.raw.n_tests // 0')
    printf '%s' "$out" | jq -r '.raw.failing[]? // empty' > "$outfile"
    [ "$SCORE_OK" = "true" ]
}

lines_to_json_array() {
    jq -R -s -c 'split("\n") | map(select(length>0))' "$1"
}

# --- base + floor -----------------------------------------------------
orig_head=$(git rev-parse HEAD)

target_json="$iter_dir/target.json"
base=""
if [ -f "$target_json" ]; then
    base=$(jq -r '.pre_sha // empty' "$target_json")
fi
[ -n "$base" ] || base="$orig_head"

floor_file="$iter_dir/floor.json"
floor_failing_file="$scratch/floor_failing.txt"

if [ -f "$floor_file" ]; then
    floor_n_tests=$(jq -r '.n_tests // 0' "$floor_file")
    jq -r '.failing[]? // empty' "$floor_file" | sort -u > "$floor_failing_file"
else
    if ! run_tests_scorer "$floor_failing_file"; then
        echo "merge.sh: could not establish the tests floor: $SCORE_ERROR" >&2
        exit 1
    fi
    floor_n_tests="$SCORE_NTESTS"
    sort -u -o "$floor_failing_file" "$floor_failing_file"
    failing_json=$(lines_to_json_array "$floor_failing_file")
    jq -n --argjson n "$floor_n_tests" --argjson failing "$failing_json" \
        '{n_tests:$n, failing:$failing}' > "$floor_file"
fi

# --- main loop ------------------------------------------------------------
for id in "${ids[@]}"; do
    if ! jq -e --arg id "$id" '.subtasks[]? | select(.id==$id)' "$plan_json" >/dev/null 2>&1; then
        echo "SCOPE $id no owned_paths entry in plan.json"
        add_rejected "$id" "no-plan-entry"
        continue
    fi
    owned_file="$scratch/owned_$id.txt"
    jq -r --arg id "$id" '.subtasks[] | select(.id==$id) | .owned_paths[]? // empty' "$plan_json" \
        | sort -u > "$owned_file"

    report_json="$iter_dir/tasks/$id/report.json"
    if [ ! -f "$report_json" ]; then
        echo "SCOPE $id missing report.json"
        add_rejected "$id" "missing-report"
        continue
    fi
    branch=$(jq -r '.branch // empty' "$report_json")
    worktree=$(jq -r '.worktree // empty' "$report_json")
    if [ -z "$branch" ]; then
        echo "SCOPE $id report.json missing branch"
        add_rejected "$id" "missing-branch"
        continue
    fi

    pre=$(git rev-parse HEAD)

    if ! git merge --no-ff --no-edit "$branch" -m "nightshift: merge $id" >"$scratch/merge_out_$id" 2>&1; then
        git merge --abort >/dev/null 2>&1 || true
        echo "CONFLICT $id"
        add_rejected "$id" "conflict"
        tag_rejected_branch "$id" "$branch"
        continue
    fi

    # 3. scope check: diff introduced by this merge must be inside owned_paths
    changed_file="$scratch/changed_$id.txt"
    git diff --name-only "$pre"..HEAD | sort -u > "$changed_file"
    stray=$(comm -23 "$changed_file" "$owned_file")
    if [ -n "$stray" ]; then
        git reset --hard "$pre" >/dev/null 2>&1
        stray_oneline=$(printf '%s' "$stray" | tr '\n' ' ')
        echo "SCOPE $id $stray_oneline"
        add_rejected "$id" "scope: $stray_oneline"
        tag_rejected_branch "$id" "$branch"
        continue
    fi

    # 4. files-changed trip: cumulative size of the iteration so far
    # vendored component kits (shadcn, Bklit) and lockfiles are copied wholesale and do not count
    vendored_re=$(jq -r '.vendored_paths_regex // "(^|/)components/(ui|charts)/|(^|/)(package-lock\\.json|pnpm-lock\\.yaml)$"' "$NIGHTSHIFT_CONFIG")
    nfiles=$(git diff --name-only "$base"..HEAD | grep -vE "$vendored_re" | wc -l | tr -d ' ')
    if [ "$nfiles" -gt "$max_files" ]; then
        git reset --hard "$pre" >/dev/null 2>&1
        echo "SCOPE-DRIFT $id"
        add_rejected "$id" "scope-drift: $nfiles files changed > max $max_files"
        tag_rejected_branch "$id" "$branch"
        continue
    fi

    # 5. tests hard floor
    new_failing_file="$scratch/new_failing_$id.txt"
    if ! run_tests_scorer "$new_failing_file"; then
        git reset --hard "$pre" >/dev/null 2>&1
        echo "FLOOR $id tests scorer error: $SCORE_ERROR"
        add_rejected "$id" "floor: scorer error: $SCORE_ERROR"
        tag_rejected_branch "$id" "$branch"
        continue
    fi
    sort -u -o "$new_failing_file" "$new_failing_file"

    newly_failing=$(comm -13 "$floor_failing_file" "$new_failing_file")
    if [ -n "$newly_failing" ]; then
        git reset --hard "$pre" >/dev/null 2>&1
        newly_oneline=$(printf '%s' "$newly_failing" | tr '\n' ' ')
        echo "FLOOR $id newly-failing: $newly_oneline"
        add_rejected "$id" "floor: newly-failing: $newly_oneline"
        tag_rejected_branch "$id" "$branch"
        continue
    fi

    new_n_tests="$SCORE_NTESTS"
    if [ "$new_n_tests" -lt "$floor_n_tests" ]; then
        reduction=$((floor_n_tests - new_n_tests))
        review_json="$iter_dir/tasks/$id/review.json"
        approved=0
        if [ -f "$review_json" ]; then
            verdict=$(jq -r '.verdict // empty' "$review_json")
            removed=$(jq -r '.test_delta.removed // 0' "$review_json")
            if [ "$verdict" = "approve" ] && [ "$removed" -ge "$reduction" ]; then
                approved=1
            fi
        fi
        if [ "$approved" -ne 1 ]; then
            git reset --hard "$pre" >/dev/null 2>&1
            echo "FLOOR $id tests removed"
            add_rejected "$id" "floor: tests removed ($floor_n_tests -> $new_n_tests)"
            tag_rejected_branch "$id" "$branch"
            continue
        fi
    fi

    # 6. merged
    new_head=$(git rev-parse HEAD)
    echo "MERGED $id $new_head"
    log_merged_event "$id" "$new_head"
    add_merged "$id"

    # 7. cleanup — only on merge
    # never remove the worktree this merge runs in (the build worktree) or the main checkout, even
    # when a report names it: a planner that split a task by hand reported the build worktree, and
    # this step deleted it mid-milestone (2026-09-22)
    if [ -n "$worktree" ]; then
        wt_real=$(cd "$worktree" 2>/dev/null && pwd -P || printf '%s' "$worktree")
        here_real=$(cd "$(git rev-parse --show-toplevel)" && pwd -P)
        main_real=$(cd "$(git worktree list --porcelain | awk '/^worktree /{print substr($0,10); exit}')" 2>/dev/null && pwd -P)
        if [ "$wt_real" = "$here_real" ] || [ "$wt_real" = "$main_real" ]; then
            echo "KEEP $worktree (the worktree this merge runs in)"
        else
            git worktree remove --force "$worktree" >/dev/null 2>&1 || true
        fi
    fi
    git branch -D "$branch" >/dev/null 2>&1 || true
done

merged_count=$(jq -r '.merged | length' "$merge_json")
if [ "$merged_count" -gt 0 ]; then
    exit 0
else
    exit 5
fi
