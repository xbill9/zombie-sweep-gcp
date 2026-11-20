#!/usr/bin/env bash
# PostToolUse(Write|Edit): lint the edited file; exit 2 feeds errors back to Claude.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
[[ -f "$f" ]] || exit 0
case "$f" in
  *.py)   out=$(ruff check --no-cache --select E9,F --output-format concise "$f" 2>&1) ;;
  *.sh)   out=$(shellcheck -S warning "$f" 2>&1) ;;
  *.yaml|*.yml) out=$(python3 -c 'import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))' "$f" 2>&1) ;;
  *) exit 0 ;;
esac
[[ $? -eq 0 ]] && exit 0
echo "$out" >&2; exit 2
