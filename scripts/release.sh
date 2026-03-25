#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Cortex — Release Script
#
# Creates a GitHub release with semantic version + timestamp build ID.
# Usage:
#   ./scripts/release.sh [patch|minor|major]
#   ./scripts/release.sh              # auto-patch (default)
#
# Versioning:  v{major}.{minor}.{patch}+{YYYYMMDD.HHMM}
# Tags:        v0.1.1, v0.1.2, etc.
# Also updates: "latest" release always points to newest.
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

BUMP="${1:-patch}"
VERSION_FILE="cortex/version.py"
PYPROJECT="pyproject.toml"
TIMESTAMP=$(date -u +"%Y%m%d.%H%M")

# ── Read current version ──────────────────────────────────────────
CURRENT=$(grep '__version__ =' "$VERSION_FILE" | sed 's/.*"\(.*\)"/\1/')
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

case "$BUMP" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch) PATCH=$((PATCH + 1)) ;;
    *) echo "Usage: $0 [patch|minor|major]"; exit 1 ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
TAG="v${NEW_VERSION}"
FULL_VERSION="${NEW_VERSION}+${TIMESTAMP}"

echo "📦 Bumping: ${CURRENT} → ${NEW_VERSION} (${FULL_VERSION})"

# ── Update version files ──────────────────────────────────────────
sed -i "s/__version__ = \".*\"/__version__ = \"${NEW_VERSION}\"/" "$VERSION_FILE"
sed -i "s/__version_tuple__ = (.*)/__version_tuple__ = (${MAJOR}, ${MINOR}, ${PATCH})/" "$VERSION_FILE"
sed -i "s/^version = \".*\"/version = \"${NEW_VERSION}\"/" "$PYPROJECT"

# ── Commit & tag ──────────────────────────────────────────────────
git add "$VERSION_FILE" "$PYPROJECT"
git commit -m "release: v${NEW_VERSION}

Build: ${FULL_VERSION}

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

git tag -a "$TAG" -m "Atlas Cortex ${FULL_VERSION}"
git push origin main --tags

# ── Create GitHub release ─────────────────────────────────────────
NOTES=$(cat <<EOF
## Atlas Cortex ${FULL_VERSION}

### What's New
$(git log --oneline "$(git describe --tags --abbrev=0 HEAD~1 2>/dev/null || git rev-list --max-parents=0 HEAD)..HEAD~1" 2>/dev/null | sed 's/^/- /' || echo "- Initial release")

### Installation
\`\`\`bash
# Quick install
curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/scripts/install.sh | bash

# Or clone and install
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex && pip install -e .
\`\`\`

### Docker
\`\`\`bash
docker pull ghcr.io/betanu701/atlas-cortex:${TAG}
docker pull ghcr.io/betanu701/atlas-cortex:latest
\`\`\`
EOF
)

gh release create "$TAG" \
    --title "Atlas Cortex ${FULL_VERSION}" \
    --notes "$NOTES" \
    --latest

echo ""
echo "✅ Released: ${TAG} (${FULL_VERSION})"
echo "   https://github.com/Betanu701/atlas-cortex/releases/tag/${TAG}"
