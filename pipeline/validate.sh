#!/usr/bin/env bash
# validate.sh — the validation stage's driver. The only entry point the
# /jg-validate command calls. It runs every role as a fresh `claude -p` child
# through child.sh, does the redaction and the freeze as script steps, holds
# the stage to one budget across PIVOT passes, and lets validate.py compute
# the verdict. No model sequences the roles.
#
#   validate.sh --project DIR --idea "text or URL" --build-usd N
#               [--toml FILE] [--cap USD] [--no-refetch] [--max-pivots N]
#
# Exit: 0 GO · 2 NO-GO · 3 needs-human (cap reached, unmatched measure, bad
# shape twice) · 4 infra (blocked here) · 5 PIVOT left for the human · 1 usage.

set -uo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT="$PWD"; IDEA=""; BUILD=""; TOML="$KIT/validate.toml"; CAP=""; NOREFETCH=""; MAX_PIVOTS=1
while [ $# -gt 0 ]; do
    case "$1" in
        --project) PROJECT="$2"; shift 2 ;;
        --idea) IDEA="$2"; shift 2 ;;
        --build-usd) BUILD="$2"; shift 2 ;;
        --toml) TOML="$2"; shift 2 ;;
        --cap) CAP="$2"; shift 2 ;;
        --no-refetch) NOREFETCH="--no-refetch"; shift ;;
        --max-pivots) MAX_PIVOTS="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done
[ -n "$IDEA" ] && [ -n "$BUILD" ] || { echo "validate.sh: --idea and --build-usd required" >&2; exit 1; }
PROJECT=$(cd "$PROJECT" && pwd -P)
PIPE="$PROJECT/.pipeline"; mkdir -p "$PIPE"
LOG="$PIPE/events.log"
note() { printf '%s validate %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; echo "validate: $*" >&2; }
finish() { echo "$DIR"; exit "$1"; }   # the directory is the last line of stdout on every exit
prev() { for f in "$@"; do [ -f "$DIR/$f" ] && mv -f "$DIR/$f" "$DIR/$f.prev"; done; return 0; }

# ---- init ----------------------------------------------------------------
INIT=$(python3 "$KIT/validate.py" init --project "$PROJECT" --idea "$IDEA" --build-usd "$BUILD" --toml "$TOML") || { echo "$INIT"; exit 1; }
DIR=$(jq -r .dir <<<"$INIT"); SLUG=$(jq -r .slug <<<"$INIT"); BAND=$(jq -r .band <<<"$INIT")
[ -n "$CAP" ] || CAP=$(jq -r .cap_usd <<<"$INIT")
export VALIDATE_DIR="$DIR"
note "start slug=$SLUG band=$BAND cap=\$$CAP"

# per-child ceilings and models from the toml
tomlget() { python3 - "$TOML" "$1" "$2" <<'PY'
import sys, tomllib
cfg = tomllib.load(open(sys.argv[1], "rb")); print(cfg[sys.argv[2]][sys.argv[3]])
PY
}
BAND_RULE=$(python3 - "$TOML" "$BAND" <<'PY'
import sys, tomllib
cfg = tomllib.load(open(sys.argv[1], "rb")); r = cfg["require"][sys.argv[2]]
print(f"core claim needs tier {r['core']}, every other claim tier {r['other']}; the core claim is never satisfied by tier 1")
PY
)

# ---- budget: the stage total is the sum of this slug's ledger rows ------------
spent() {  # one number, always: jq 1.7 prints 0 AND exits 2 on a missing file, which once came out as "0\n0"
    local v=""
    [ -f "$PIPE/ledger.jsonl" ] && v=$(jq -s --arg s "validate:$SLUG:" 'map(select(.stage | startswith($s)) | .cost_usd) | add // 0' "$PIPE/ledger.jsonl" 2>/dev/null | head -1)
    case "$v" in ''|null) v=0 ;; esac
    echo "$v"
}
remaining() { python3 -c "print(round($CAP - $(spent), 4))"; }
child() {  # child ROLE PROMPT MODEL CEILING TURNS [--sub ...]
    local role="$1" prompt="$2" model="$3" ceil="$4" turns="$5"; shift 5
    local left; left=$(remaining)
    if python3 -c "import sys; sys.exit(0 if $left < 0.25 else 1)"; then
        note "cap reached before $role (spent \$$(spent) of \$$CAP)"; return 90
    fi
    prev "${ROLE_FILES[$role]:-}"
    local budget; budget=$(python3 -c "print(min($ceil, $left))")
    note "$role model=$model budget=\$$budget"
    bash "$KIT/child.sh" --project "$PROJECT" --prompt "$prompt" --stage "validate:$SLUG:$role" \
        --budget "$budget" --model "$model" --turns "$turns" --timeout-min 20 \
        --sub "KIT=$KIT" --sub "DIR=$DIR" --sub "IDEA=$IDEA" --sub "BUILD_USD=$BUILD" --sub "BAND=$BAND" \
        --sub "BAND_RULE=$BAND_RULE" --sub-file "REACHABLE=$DIR/reachable.txt" --sub "PREVIOUS=$PREVIOUS" "$@" > "$DIR/$role.run" 2>>"$DIR/driver.log"
    local rc=$?
    note "$role rc=$rc cost=\$$(spent) total"
    [ "$rc" = 0 ] || note "$role exited $rc (ceiling was \$$budget; a valid file still counts, the schema check decides)"
    return $rc
}
schema() { python3 "$KIT/validate.py" schema --dir "$DIR" --file "$1" | tee -a "$DIR/driver.log" | jq -e .ok >/dev/null; }

# ---- probe (a script, once) ----------------------------------------------------
python3 "$KIT/fetch.py" probe --out "$DIR/reachable.json" > "$DIR/probe.out" 2>>"$DIR/driver.log" || true
jq -r '.hosts | to_entries[] | "\(.key): \(if .value.reachable then "reachable" else "BLOCKED (" + (.value.reason // "?") + ")" end)"' "$DIR/reachable.json" > "$DIR/reachable.txt" 2>/dev/null || echo "probe failed" > "$DIR/reachable.txt"
note "probe: $(jq -r '[.hosts[] | select(.reachable)] | length' "$DIR/reachable.json" 2>/dev/null || echo 0) hosts reachable"

declare -A ROLE_FILES=([author]="claims.json" [setter]="plan.json" [skeptic]="skeptic.json" [fetcher]="" [judge]="judge.json")
MODEL_AUTHOR=$(tomlget roles author); MODEL_SETTER=$(tomlget roles setter); MODEL_SKEPTIC=$(tomlget roles skeptic)
MODEL_FETCHER=$(tomlget roles fetcher); MODEL_JUDGE=$(tomlget roles judge)
CAP_AUTHOR=$(tomlget caps author); CAP_SETTER=$(tomlget caps setter); CAP_SKEPTIC=$(tomlget caps skeptic)
CAP_FETCHER=$(tomlget caps fetcher); CAP_JUDGE=$(tomlget caps judge)

PIVOTS=0; PREVIOUS="none (first pass)"
while :; do
    # 1. author
    child author "$KIT/prompts/validate-author.md" "$MODEL_AUTHOR" "$CAP_AUTHOR" 12; rc=$?
    [ "$rc" = 90 ] && finish 3
    if ! schema claims; then
        note "claims.json failed its schema once; one retry"
        child author "$KIT/prompts/validate-author.md" "$MODEL_AUTHOR" "$CAP_AUTHOR" 12 --sub "RETRY=the previous attempt failed the schema check; read the problems in $DIR/driver.log"
        schema claims || { note "claims.json failed its schema twice"; finish 3; }
    fi
    # 2. setter
    child setter "$KIT/prompts/validate-setter.md" "$MODEL_SETTER" "$CAP_SETTER" 12; rc=$?
    [ "$rc" = 90 ] && finish 3
    schema plan || { child setter "$KIT/prompts/validate-setter.md" "$MODEL_SETTER" "$CAP_SETTER" 12; schema plan || { note "plan.json failed its schema twice"; finish 3; }; }
    # 3. skeptic (not on the small band)
    prev skeptic.md
    if [ "$BAND" != "small" ]; then
        python3 "$KIT/validate.py" redact --dir "$DIR" >/dev/null
        child skeptic "$KIT/prompts/validate-skeptic.md" "$MODEL_SKEPTIC" "$CAP_SKEPTIC" 12; rc=$?
        [ "$rc" = 90 ] && finish 3
        schema skeptic || { child skeptic "$KIT/prompts/validate-skeptic.md" "$MODEL_SKEPTIC" "$CAP_SKEPTIC" 12; schema skeptic || { note "skeptic.json failed its schema twice"; finish 3; }; }
    else
        rm -f "$DIR/skeptic.json"
    fi
    # 4. freeze (script)
    FR=$(python3 "$KIT/validate.py" freeze --dir "$DIR"); frc=$?
    echo "$FR" >> "$DIR/driver.log"
    [ "$frc" = 0 ] || { note "freeze refused: $(jq -c .problems <<<"$FR")"; finish 3; }
    # 5. fetcher (read-only plan; the sha is checked after). On a PIVOT pass
    # the ledger is kept: rows for unchanged sources are reused, not re-bought.
    [ "$PIVOTS" = 0 ] && rm -f "$DIR/ledger.jsonl"
    child fetcher "$KIT/prompts/validate-fetcher.md" "$MODEL_FETCHER" "$CAP_FETCHER" 60; rc=$?
    [ "$rc" = 90 ] && finish 3
    python3 "$KIT/validate.py" check-frozen --dir "$DIR" | jq -e .ok >/dev/null || { note "frozen plan edited by the fetcher"; finish 2; }
    [ -s "$DIR/ledger.jsonl" ] || { note "fetcher wrote no ledger rows"; finish 4; }
    # 6. judge (not on the small band)
    if [ "$BAND" != "small" ]; then
        child judge "$KIT/prompts/validate-judge.md" "$MODEL_JUDGE" "$CAP_JUDGE" 30; rc=$?
        [ "$rc" = 90 ] && finish 3
        schema judge || { child judge "$KIT/prompts/validate-judge.md" "$MODEL_JUDGE" "$CAP_JUDGE" 30; schema judge || note "judge.json failed its schema twice; scoring without it"; }
    fi
    # 7. referee (script)
    VR=$(python3 "$KIT/validate.py" verdict --dir "$DIR" --reachable "$DIR/reachable.json" --toml "$TOML" $NOREFETCH); vrc=$?
    echo "$VR" >> "$DIR/driver.log"
    note "verdict=$(jq -r .verdict <<<"$VR" 2>/dev/null) rc=$vrc spent=\$$(spent)"
    if [ "$vrc" = 5 ] && [ "$PIVOTS" -lt "$MAX_PIVOTS" ]; then
        PIVOTS=$((PIVOTS + 1))
        PREVIOUS="$(cat "$DIR/VERDICT.md" 2>/dev/null | head -c 6000)"
        note "PIVOT $PIVOTS: re-running the author with the ledger"
        continue
    fi
    finish "$vrc"
done
