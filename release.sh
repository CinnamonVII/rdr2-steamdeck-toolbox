#!/bin/bash

# RDR2 Steam Deck Toolbox Release Script
# Automatically creates a GitHub release based on the version in rdr2_toolbox.py

set -e

# Configuration
MAIN_SCRIPT="rdr2_toolbox.py"
REPO_OWNER="CinnamonVII"
REPO_NAME="rdr2-steamdeck-toolbox"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== RDR2 Steam Deck Toolbox Release Utility ===${NC}"

# 1. Check for GitHub CLI
if ! command -v gh &> /dev/null; then
    echo -e "${RED}Error: GitHub CLI (gh) is not installed.${NC}"
    echo "Please install it: https://cli.github.com/"
    exit 1
fi

# 2. Get version and release type from user
echo "Select release type:"
echo "1) Stable"
echo "2) Alpha"
echo "3) Beta"
read -p "Option [1-3]: " TYPE_OPT

case $TYPE_OPT in
    2)
        TYPE="alpha"
        ;;
    3)
        TYPE="beta"
        ;;
    *)
        TYPE="stable"
        ;;
esac

read -p "Enter version number (e.g. 1.0.0): " VERSION
if [ -z "$VERSION" ]; then
    echo -e "${RED}Error: Version cannot be empty.${NC}"
    exit 1
fi

if [ "$TYPE" != "stable" ]; then
    TAG="v$VERSION-$TYPE"
else
    TAG="v$VERSION"
fi

echo -e "${GREEN}Detected Version:${NC} $VERSION"
echo -e "${GREEN}Release Type:${NC} $TYPE"
echo -e "${GREEN}Target Tag:${NC} $TAG"

# 3. Check if tag already exists
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo -e "${YELLOW}Warning: Tag $TAG already exists locally.${NC}"
fi

# 4. Confirmation
read -p "Do you want to create and push a release for $TAG? (y/N) " confirm
if [[ ! $confirm =~ ^[Yy]$ ]]; then
    echo "Release cancelled."
    exit 0
fi

# 5. Create and push tag
echo -e "${BLUE}Creating tag $TAG...${NC}"
git tag -a "$TAG" -m "Release $VERSION ($TYPE)"
echo -e "${BLUE}Pushing tag $TAG to origin...${NC}"
git push origin "$TAG"

# 6. Update alpha/beta branch if applicable
if [ "$TYPE" != "stable" ]; then
    echo -e "${BLUE}Updating $TYPE branch...${NC}"
    git branch -f "$TYPE" HEAD
    git push origin "$TYPE"
fi

# 7. Create GitHub Release
echo -e "${BLUE}Creating GitHub release...${NC}"
PRERELEASE_FLAG=""
if [ "$TYPE" != "stable" ]; then
    PRERELEASE_FLAG="--prerelease"
fi

gh release create "$TAG" \
    $PRERELEASE_FLAG \
    --title "$TAG" \
    --generate-notes \
    --repo "$REPO_OWNER/$REPO_NAME" \
    $( [ "$TYPE" == "alpha" ] && echo "save_modifier.py" )

echo -e "${GREEN}Successfully created release $TAG!${NC}"
echo -e "View it here: https://github.com/$REPO_OWNER/$REPO_NAME/releases/tag/$TAG"
