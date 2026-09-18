#!/usr/bin/env bash
# Nightshift statusLine renderer.
#
# Reads the Claude Code statusLine JSON payload on stdin, writes up to 3 lines
# to stdout. Contract: loop/README.md ("state.json", "scores.jsonl row").
#
# Hard rules:
#   - must complete in well under 100ms on the hot path
#   - must NEVER print an error or a traceback — on any failure, print
#     whatever lines were already built and exit 0
#
# No `set -e` / `set -u` on purpose: a single failed substitution must not
# abort the whole line. Every external read is guarded (test -f, 2>/dev/null,
# default values) instead.

DIM=$'\033[2m'
RESET=$'\033[0m'
GREEN=$'\033[32m'
AMBER=$'\033[33m'
RED=$'\033[31m'

# Path to the rdx launcher. Overridable for tests; defaults to the documented
# fixed location.
RDX_SH="${NIGHTSHIFT_RDX_SH:-$HOME/Claude_Upgrade/discovery/rdx.sh}"

join_by() {
    # join_by <delim> <items...>
    local d="$1"; shift
    if [ "$#" -eq 0 ]; then return 0; fi
    local first="$1"; shift
    printf '%s' "$first"
    printf '%s' "${@/#/$d}"
}

payload="$(cat 2>/dev/null)"

# --------------------------------------------------------------------------
# line 1 — model / cost / context / rdx
# --------------------------------------------------------------------------

line1=""
{
    mapfile -t line1_fields < <(
        printf '%s' "$payload" | jq -r '
            def scalar: if . == null then ""
                        elif (type == "object" or type == "array") then ""
                        else (. | tostring) end;
            def tryf(f): try f catch null;
            ((((tryf(.model.display_name)) // (tryf(.model.name)) // (tryf(.model)) // "?") | scalar) as $m
                | if $m == "" then "?" else $m end),
            (try ((.cost.total_cost_usd // .cost.total // "") | scalar) catch ""),
            (try ((.context_window.used_percentage // "") | scalar) catch ""),
            (try ((.workspace.project_dir // .workspace.current_dir // .cwd // "") | scalar) catch "")
        ' 2>/dev/null
    )
    model="${line1_fields[0]}"
    cost="${line1_fields[1]}"
    ctx="${line1_fields[2]}"
    project="${line1_fields[3]}"

    [ -z "$model" ] && model="?"
    [ -z "$project" ] && project="$PWD"

    parts=("$model")

    if [ -n "$cost" ]; then
        cost_fmt=""
        printf -v cost_fmt '%.2f' "$cost" 2>/dev/null
        [ -n "$cost_fmt" ] && parts+=("\$${cost_fmt}")
    fi

    if [ -n "$ctx" ]; then
        ctx_fmt=""
        printf -v ctx_fmt '%.0f' "$ctx" 2>/dev/null
        [ -n "$ctx_fmt" ] && parts+=("ctx ${ctx_fmt}%")
    fi

    rdx_line=""
    if [ -n "$RDX_SH" ] && [ -f "$RDX_SH" ]; then
        rdx_line=$(printf '%s' "$payload" | timeout 0.4 bash "$RDX_SH" statusline 2>/dev/null)
        rdx_line="${rdx_line%%$'\n'*}"   # rdx renders one line; guard anyway
    fi
    [ -n "$rdx_line" ] && parts+=("$rdx_line")

    body="$(join_by ' · ' "${parts[@]}")"
    [ -n "$body" ] && line1="${DIM}${body}${RESET}"
} 2>/dev/null

[ -n "$line1" ] && printf '%s\n' "$line1"

# --------------------------------------------------------------------------
# line 2 — loop state (only if .loop/state.json exists)
# --------------------------------------------------------------------------

line2=""
{
    state_file="$project/.loop/state.json"
    if [ -f "$state_file" ]; then
        # 13 scalars, one per line (never TSV-split with `read` — bash's IFS
        # whitespace collapsing silently drops empty fields and shifts the
        # rest), then the agent count, then one TSV line per agent (agent
        # fields always have a non-empty fallback, so TSV+read is safe there).
        mapfile -t state_lines < <(jq -r '
            def scalar: if . == null then ""
                        elif (type == "object" or type == "array") then ""
                        else (. | tostring) end;
            ((.run // "") | scalar),
            ((.iter // "") | scalar),
            ((.phase // "") | scalar),
            ((.cap_usd // "") | scalar),
            ((.spent_usd // "") | scalar),
            ((.live_spend_usd // 0) | scalar),
            ((.score // "") | scalar),
            ((.best // "") | scalar),
            ((.delta // "") | scalar),
            ((.flat // "") | scalar),
            ((.rung // "") | scalar),
            ((.task // "") | scalar),
            ((.stop_reason // "") | scalar),
            ((.agents // []) | length | tostring),
            ((.agents // [])[] | [
                (try ((.type // "?") | scalar) catch "?"),
                (try ((.task // "?") | scalar) catch "?"),
                (try (.since | fromdateiso8601 | tostring) catch "0")
            ] | join("|"))
        ' "$state_file" 2>/dev/null)

        if [ "${#state_lines[@]}" -ge 14 ]; then
            run="${state_lines[0]}"; iter="${state_lines[1]}"; phase="${state_lines[2]}"
            cap_usd="${state_lines[3]}"; spent_usd="${state_lines[4]}"; live_usd="${state_lines[5]}"
            score="${state_lines[6]}"; best="${state_lines[7]}"; delta="${state_lines[8]}"
            flat="${state_lines[9]}"; rung="${state_lines[10]}"; task="${state_lines[11]}"
            stop_reason="${state_lines[12]}"
            agent_count="${state_lines[13]}"
            [ -z "$agent_count" ] && agent_count=0

            now_epoch=$(date +%s 2>/dev/null || echo 0)

            glyph="●"; gcolor="$GREEN"; stale_prefix=""; red_stop=0

            if [ "$phase" = "STOPPED" ]; then
                case "$stop_reason" in
                    needs-human|flat) glyph="◐"; gcolor="$AMBER" ;;
                    *) glyph="■"; gcolor="$RED"; red_stop=1 ;;
                esac
            else
                stale=0
                hb_file="$project/.loop/heartbeat"
                if [ -f "$hb_file" ]; then
                    hb_mtime=$(stat -c %Y "$hb_file" 2>/dev/null || echo "$now_epoch")
                    age=$(( now_epoch - hb_mtime ))
                    if [ "$age" -gt 180 ]; then
                        stale=1
                        stale_min=$(( age / 60 ))
                    fi
                fi
                if [ "$stale" -eq 1 ]; then
                    glyph="⚠"; gcolor="$AMBER"; stale_prefix="stale ${stale_min}m "
                else
                    q_file="$project/.loop/questions.md"
                    if [ -f "$q_file" ]; then
                        qcache_file="$project/.loop/.statusline_qcount"
                        qcount=$(grep -c '^## ' "$q_file" 2>/dev/null || echo 0)
                        qprev=0
                        [ -f "$qcache_file" ] && qprev=$(cat "$qcache_file" 2>/dev/null)
                        [ -z "$qprev" ] && qprev=0
                        if [ "$qcount" -gt "$qprev" ] 2>/dev/null; then
                            glyph="◐"; gcolor="$AMBER"
                        fi
                        printf '%s' "$qcount" > "$qcache_file" 2>/dev/null
                    fi
                fi
            fi

            # numeric formatting — one awk pass for everything money/score
            # related. Fields are "|"-joined (not space/tab/newline): those
            # are bash's "IFS whitespace" set, where `read` silently drops
            # empty fields and shifts the rest — "|" splits without that.
            IFS='|' read -r score_fmt best_fmt arrow absdelta eff_fmt tilde cap_fmt bar barred spent_fmt <<< "$(awk -v score="$score" -v best="$best" -v delta="$delta" \
                -v spent="$spent_usd" -v live="$live_usd" -v cap="$cap_usd" '
                BEGIN {
                    if (score == "") score = 0; if (best == "") best = 0; if (delta == "") delta = 0;
                    if (spent == "") spent = 0; if (live == "") live = 0; if (cap == "") cap = 0;
                    arrow = (delta < 0) ? "▼" : "▲";
                    ad = delta; if (ad < 0) ad = -ad;
                    eff = spent; tilde = "";
                    if (live > 0) { eff = spent + live; tilde = "~"; }
                    ratio = (cap > 0) ? eff / cap : 0;
                    if (ratio < 0) ratio = 0; if (ratio > 1) ratio = 1;
                    filled = int(ratio * 10 + 0.5);
                    bar = "";
                    for (i = 0; i < filled; i++) bar = bar "▮";
                    for (i = filled; i < 10; i++) bar = bar "░";
                    barred = (ratio >= 0.8) ? 1 : 0;
                    printf "%.1f|%.1f|%s|%.1f|%.2f|%s|%.2f|%s|%d|%.2f", score, best, arrow, ad, eff, tilde, cap, bar, barred, spent
                }')"

            sparkline=""
            scores_file="$project/.loop/scores.jsonl"
            if [ -f "$scores_file" ]; then
                sparkline=$(tail -n 12 "$scores_file" 2>/dev/null | jq -rs '
                    [ .[] | select(.composite != null) | .composite ] as $vals |
                    if ($vals | length) == 0 then ""
                    else
                        ($vals | min) as $mn | ($vals | max) as $mx |
                        (($mx - $mn)) as $range |
                        ($vals | map(
                            if $range == 0 then 3
                            else ((. - $mn) / $range * 7 | floor)
                            end
                        ) | map(["▁","▂","▃","▄","▅","▆","▇","█"][.]) | join(""))
                    end
                ' 2>/dev/null)
            fi

            if [ "$red_stop" -eq 1 ]; then
                first_c=""; last_c=""
                if [ -f "$scores_file" ]; then
                    first_c=$(head -n1 "$scores_file" 2>/dev/null | jq -r '.composite // empty' 2>/dev/null)
                    last_c=$(tail -n1 "$scores_file" 2>/dev/null | jq -r '.composite // empty' 2>/dev/null)
                fi
                first_fmt="—"; last_fmt="—"
                [ -n "$first_c" ] && printf -v first_fmt '%.1f' "$first_c" 2>/dev/null
                [ -n "$last_c" ] && printf -v last_fmt '%.1f' "$last_c" 2>/dev/null
                line2="${gcolor}${glyph}${RESET} loop STOPPED ${stop_reason} · ${iter} iters · ${first_fmt}→${last_fmt} · \$${spent_fmt}/\$${cap_fmt}"
            else
                bar_disp="$bar"
                [ "$barred" = "1" ] && bar_disp="${RED}${bar}${RESET}"
                money="\$${eff_fmt}${tilde}/\$${cap_fmt}"
                line2="${gcolor}${glyph}${RESET} ${stale_prefix}${run} · iter ${iter} · ${phase} · score ${score_fmt} ${arrow}${absdelta} (best ${best_fmt}) ${sparkline} · flat ${flat}/3 · rung ${rung} · ${money} ${bar_disp}"
            fi
        fi
    fi
} 2>/dev/null

[ -n "$line2" ] && printf '%s\n' "$line2"

# --------------------------------------------------------------------------
# line 3 — active agents (only if state.agents is non-empty)
# --------------------------------------------------------------------------

line3=""
{
    if [ -n "$agent_count" ] && [ "$agent_count" -gt 0 ] 2>/dev/null; then
        now_epoch=${now_epoch:-$(date +%s 2>/dev/null || echo 0)}
        segs=()
        idx=0
        shown=0
        for aline in "${state_lines[@]:14}"; do
            idx=$((idx + 1))
            [ "$shown" -ge 3 ] && break
            IFS='|' read -r atype atask asince <<< "$aline"
            [ -z "$asince" ] && asince=0
            aelapsed=$(( (now_epoch - asince) / 60 ))
            [ "$aelapsed" -lt 0 ] && aelapsed=0
            segs+=("${atype} ${atask} ${aelapsed}m")
            shown=$((shown + 1))
        done
        if [ "${#segs[@]}" -gt 0 ]; then
            line3="↳ $(join_by ' · ' "${segs[@]}")"
            if [ "$agent_count" -gt 3 ]; then
                line3="${line3} +$(( agent_count - 3 ))"
            fi
        fi
    fi
} 2>/dev/null

[ -n "$line3" ] && printf '%s\n' "$line3"

exit 0
