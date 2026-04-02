#!/bin/bash
# Add AGPL-3.0 license headers to all Python files

HEADER="# SPDX-License-Identifier: AGPL-3.0-or-later"

find . -name "*.py" -type f ! -path "./.venv/*" ! -path "./venv/*" ! -path "./__pycache__/*" -print0 | while IFS= read -r -d '' file; do
    if ! head -n 1 "$file" | grep -q "SPDX-License-Identifier"; then
        echo "Adding header to $file"
        # Create temp file with header + original content
        { echo "$HEADER"; cat "$file"; } > "$file.tmp"
        mv "$file.tmp" "$file"
    fi
done

echo "Done!"
