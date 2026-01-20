#!/bin/bash
# Auto-format Python files after Edit/Write operations

file_path=$(jq -r '.tool_input.file_path' 2>/dev/null)

if [[ -z "$file_path" || "$file_path" == "null" ]]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR" || exit 0

# Python files: lint and format with ruff
if [[ "$file_path" == *.py ]]; then
  uv run ruff check --fix "$file_path" 2>/dev/null
  uv run ruff format "$file_path" 2>/dev/null
fi

exit 0
