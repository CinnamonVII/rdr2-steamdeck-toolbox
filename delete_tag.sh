#!/bin/bash

# RDR2 Steam Deck Toolbox Tag Deletion Utility
# Deletes a git tag locally and removes the corresponding GitHub release/remote tag.

set -e

# Configuration
REPO_OWNER="CinnamonVII"
REPO_NAME="rdr2-steamdeck-toolbox"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== RDR2 Steam Deck Toolbox Tag Deletion Utility ===${NC}"

# 1. Get version and release type from user
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

read -p "Enter version number to delete (e.g. 1.0.0): " VERSION
if [ -z "$VERSION" ]; then
    echo -e "${RED}Error: Version cannot be empty.${NC}"
    exit 1
fi

if [ "$TYPE" != "stable" ]; then
    TAG="v$VERSION-$TYPE"
else
    TAG="v$VERSION"
fi

echo -e "${GREEN}Constructed Tag:${NC} $TAG"

# 2. Confirmation
echo -e "${YELLOW}Warning: This will delete the tag '$TAG' locally and from GitHub (including any associated release).${NC}"
read -p "Are you sure you want to proceed? (y/N) " confirm
if [[ ! $confirm =~ ^[Yy]$ ]]; then
    echo "Deletion cancelled."
    exit 0
fi

# 3. Delete locally
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo -e "${BLUE}Deleting local tag '$TAG'...${NC}"
    git tag -d "$TAG"
else
    echo -e "${YELLOW}Notice: Tag '$TAG' not found locally.${NC}"
fi

# 4. Delete from GitHub (Release and Remote Tag)
if command -v gh &> /dev/null; then
    echo -e "${BLUE}Attempting to delete GitHub release and remote tag for '$TAG'...${NC}"
    # --cleanup-tag removes both the release and the tag from the remote
    if gh release delete "$TAG" --repo "$REPO_OWNER/$REPO_NAME" --cleanup-tag --yes 2>/dev/null; then
        echo -e "${GREEN}Successfully deleted GitHub release and remote tag '$TAG'.${NC}"
    else
        echo -e "${YELLOW}GitHub release not found or error occurred. Attempting manual remote tag deletion...${NC}"
        if git push origin --delete "$TAG" 2>/dev/null; then
            echo -e "${GREEN}Successfully deleted remote tag '$TAG'.${NC}"
        else
            echo -e "${YELLOW}Remote tag '$TAG' not found or could not be deleted.${NC}"
        fi
    fi
else
    echo -e "${YELLOW}Notice: GitHub CLI (gh) not found. Attempting manual remote tag deletion...${NC}"
    if git push origin --delete "$TAG" 2>/dev/null; then
        echo -e "${GREEN}Successfully deleted remote tag '$TAG'.${NC}"
    else
        echo -e "${RED}Error: Could not delete remote tag. Check your permissions and remote status.${NC}"
    fi
fi

echo -e "${GREEN}Process complete.${NC}"
