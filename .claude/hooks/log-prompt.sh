#!/bin/bash
# Appends user prompts and assistant responses to prompt-log.md at the repo root.
# Usage: log-prompt.sh prompt|response   (hook JSON arrives on stdin)
LOG="${CLAUDE_PROJECT_DIR:-$(pwd)}/prompt-log.md"
INPUT=$(cat)
TS=$(date '+%Y-%m-%d %H:%M:%S')
[ -f "$LOG" ] || printf '# Prompt Log\n\nAll prompts and responses from the AI tool, logged automatically by hooks.\n' > "$LOG"

case "$1" in
  prompt)
    P=$(printf '%s' "$INPUT" | jq -r '.prompt // empty')
    printf '\n---\n\n## [%s] Prompt\n\n%s\n' "$TS" "$P" >> "$LOG"
    ;;
  response)
    # Debug: record what the Stop hook actually receives (gitignored).
    printf '%s %s\n' "$TS" "$(printf '%s' "$INPUT" | jq -c 'with_entries(.value |= (tostring|.[0:80]))')" >> "$(dirname "$0")/debug.log"
    # Prefer the message the harness hands us directly, if present.
    R=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty')
    if [ -z "$R" ]; then
      T=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty')
      # Fall back to the newest transcript for this project if the path is missing/stale.
      [ -f "$T" ] || T=$(ls -t "$HOME/.claude/projects/$(printf '%s' "${CLAUDE_PROJECT_DIR:-$PWD}" | sed 's#[/.]#-#g')"/*.jsonl 2>/dev/null | head -1)
      [ -f "$T" ] || exit 0
      # The final message may not be flushed yet; retry briefly.
      for _ in 1 2 3 4 5 6; do
        R=$(jq -rs '
          def txt: if (.message.content|type)=="string" then .message.content
                   else [.message.content[]? | select(.type=="text") | .text] | join("\n") end;
          . as $all
          | ([$all | to_entries[] | select(.value.type=="user" and ((.value.message.content|type)=="string"
              or ([.value.message.content[]? | select(.type=="tool_result")] | length)==0)) | .key] | last // -1) as $i
          | [$all[($i+1):][] | select(.type=="assistant") | txt | select(length>0)] | join("\n\n")' "$T")
        [ -n "$R" ] && break
        sleep 0.5
      done
    fi
    [ -n "$R" ] && printf '\n## [%s] Response\n\n%s\n' "$TS" "$R" >> "$LOG"
    ;;
esac
exit 0
