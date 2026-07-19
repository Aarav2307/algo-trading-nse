#!/bin/bash
# scripts/install-hooks.sh — one-time setup, run once after every fresh clone.
#
# .git/hooks/ is NOT version-controlled — git never copies hooks into a new
# clone. This script re-installs the same guard on whichever machine you're
# on. Root cause this protects against: `git add -A` running before
# .gitignore exists (exactly what happened to this repo's server copy in
# July 2026, tracking .env and venv/ into history until a filter-repo pass
# removed it).
#
# Usage: bash scripts/install-hooks.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_FILE="$REPO_ROOT/.git/hooks/pre-commit"

if [ ! -d "$REPO_ROOT/.git" ]; then
    echo "ERROR: $REPO_ROOT/.git not found — run this from inside a clone of the repo."
    exit 1
fi

cat > "$HOOK_FILE" << 'HOOKEOF'
#!/bin/bash
if git diff --cached --name-only | grep -qE '(^|/)\.env$|(^|/)venv/'; then
    echo "BLOCKED: attempting to commit .env or venv/ — these must never be tracked."
    echo "If this is intentional, you must know what you're doing — remove this hook first."
    exit 1
fi

# git-secrets pattern scan — only runs if git-secrets is installed (see below)
if command -v git-secrets >/dev/null 2>&1; then
    git secrets --pre_commit_hook -- "$@"
fi
HOOKEOF

chmod +x "$HOOK_FILE"
echo "Installed pre-commit hook at $HOOK_FILE — blocks committing .env or venv/ at any depth."

if command -v git-secrets >/dev/null 2>&1; then
    ( cd "$REPO_ROOT" &&
      git secrets --register-aws &&
      git secrets --add 'KITE_API_SECRET\s*=\s*.+' &&
      git secrets --add 'ZERODHA_PASSWORD\s*=\s*.+' &&
      git secrets --add 'ZERODHA_TOTP_SECRET\s*=\s*.+' &&
      git secrets --add 'SENDGRID_API_KEY\s*=\s*.+' &&
      git secrets --add --allowed 'your_[a-z_]+' )  # README's placeholder env var docs, e.g. your_kite_api_secret
    # Write the commit-msg hook directly rather than `git secrets --install -f`,
    # which would force-overwrite the merged pre-commit hook written above.
    printf '#!/usr/bin/env bash\ngit secrets --commit_msg_hook -- "$@"\n' > "$REPO_ROOT/.git/hooks/commit-msg"
    chmod +x "$REPO_ROOT/.git/hooks/commit-msg"
    echo "Registered git-secrets patterns (AWS + Kite/Zerodha/SendGrid credentials)."
else
    echo "WARNING: git-secrets not found on this machine — only the path-based .env/venv/ guard is active."
    echo "         Install it (e.g. 'sudo apt-get install git-secrets' on Debian/Ubuntu) and re-run this"
    echo "         script to add pattern-based credential scanning too."
fi
