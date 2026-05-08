#!/usr/bin/env bash
# CDN cache diagnostic for raw.githubusercontent.com doc URLs
# Reads only — no project files are modified.

LOG=/tmp/cdn_diag.log
TS=$(date +%s)

URLS=(
  "https://raw.githubusercontent.com/aye5788/xrp_grid/main/00_PROJECT_OVERVIEW.md"
  "https://raw.githubusercontent.com/aye5788/xrp_grid/main/01_CURRENT_STATE.md"
  "https://raw.githubusercontent.com/aye5788/xrp_grid/main/02_NEXT_BUILD_TASKS.md"
  "https://raw.githubusercontent.com/aye5788/xrp_grid/main/03_INSTRUCTIONS_TO_CLAUDE.md"
)

VARIANTS=(
  ""
  "?bust=v=1"
  "?bust=${TS}"
  "?bust=20260508-diag"
)

VARIANT_LABELS=(
  "NO_QS"
  "STATIC_BUST"
  "TIMESTAMP_BUST"
  "NAMED_BUST"
)

: > "$LOG"

sep() {
  printf '\n%s\n' "================================================================" >> "$LOG"
}

for URL in "${URLS[@]}"; do
  DOCNAME=$(basename "$URL")
  sep
  printf 'DOCUMENT: %s\n' "$DOCNAME" >> "$LOG"
  sep

  for i in 0 1 2 3; do
    QS="${VARIANTS[$i]}"
    LABEL="${VARIANT_LABELS[$i]}"
    FULL_URL="${URL}${QS}"

    printf '\n--- %s  url=%s\n' "$LABEL" "$FULL_URL" >> "$LOG"

    # Fetch headers + body together; headers go to a temp file
    HEADER_FILE=$(mktemp)
    BODY=$(curl -s -D "$HEADER_FILE" "$FULL_URL")
    HTTP_STATUS=$(grep -m1 '^HTTP/' "$HEADER_FILE" | tr -d '\r')

    BODY_LEN=$(printf '%s' "$BODY" | wc -c)
    BODY_HASH=$(printf '%s' "$BODY" | sha256sum | awk '{print $1}')
    CONTAINS_DEFERRED=$(printf '%s' "$BODY" | grep -c "Deferred from 2026-05-07" || true)

    printf 'STATUS:          %s\n' "$HTTP_STATUS" >> "$LOG"
    printf 'BODY_LEN:        %d bytes\n' "$BODY_LEN" >> "$LOG"
    printf 'BODY_SHA256:     %s\n' "$BODY_HASH" >> "$LOG"
    printf 'CONTAINS_DEFERRED_LINE: %d\n' "$CONTAINS_DEFERRED" >> "$LOG"

    printf '\nHEADERS:\n' >> "$LOG"
    # Print only cache-relevant headers clearly, then all headers
    grep -iE '^(cache-control|age|etag|x-cache|cf-cache-status|last-modified|expires|vary|content-length|date|x-served-by|x-timer)' "$HEADER_FILE" \
      | tr -d '\r' \
      | sed 's/^/  /' >> "$LOG"

    printf '\nFULL RESPONSE HEADERS:\n' >> "$LOG"
    cat "$HEADER_FILE" | tr -d '\r' | sed 's/^/  /' >> "$LOG"

    rm -f "$HEADER_FILE"
  done

  sep
done

printf '\nDIAGNOSTIC COMPLETE: %s\n' "$(date -u)" >> "$LOG"
printf 'Log written to %s\n' "$LOG"
