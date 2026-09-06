#!/usr/bin/env bash
# Bring photos uploaded through the page (+ foto) down into this checkout.
#
#   scripts/pull_uploads.sh
#
# RUN THIS BEFORE EVERY DEPLOY RSYNC. The deploy overwrites the bucket's
# site/anomalias/data/media.json with the local one, so without a pull first it would
# erase every uploaded photo from the live site while their jpegs sit orphaned in the
# bucket. build_data.py refuses to rebuild if any media.json entry has lost its source,
# which is the backstop for exactly that mistake.
#
# The originals live in the bucket only: images/raw/up-* is git-ignored (anomalias is a
# public repo and these are full-resolution field photos carrying GPS EXIF).
set -euo pipefail
cd "$(dirname "$0")/.."

BUCKET="${ECOTONO_BUCKET:-gs://ecotono-data}"

# media.json is about to be replaced by the bucket's copy. Insist it is committed, so
# `git checkout site/data/media.json` can always undo this. --quiet HEAD (not a bare
# `git diff`) so staged-but-uncommitted edits are caught too.
if ! git diff --quiet HEAD -- site/data/media.json; then
  echo "!! site/data/media.json has uncommitted edits — commit or stash them first." >&2
  echo "   (this script replaces it with the bucket's copy)" >&2
  exit 1
fi

if gcloud storage ls "$BUCKET/raw/anomalias" >/dev/null 2>&1; then
  echo "↓ $BUCKET/raw/anomalias  ->  images/raw/"
  gcloud storage rsync "$BUCKET/raw/anomalias" images/raw    # fatal on error, deliberately:
                                                             # `|| true` here would let the
                                                             # rebuild below delete photos
else
  echo "(no uploads in the bucket yet)"
fi

echo "↓ $BUCKET/site/anomalias/data/media.json"
gcloud storage cp "$BUCKET/site/anomalias/data/media.json" site/data/media.json

# Not a no-op: the web copy and thumb are NOT pulled, so this regenerates them from the
# originals with build_data's exact recipe, and re-keys title/text/anom by src.
python3 scripts/build_data.py media

echo
echo "Now: fill in title/text in site/data/media.json, commit, then deploy with"
echo "  gcloud storage rsync --recursive site $BUCKET/site/anomalias"
git status --short images/raw site/data site/media
