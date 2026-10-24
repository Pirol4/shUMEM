#!/usr/bin/env bash
#
# Fetch the start of a MAWI samplepoint-F daily trace (WIDE backbone, public,
# anonymized, headers only) and turn it into the load pattern rate_sweep.py
# --trace replays (harness/trace/trace_schedule.py, CLAUDE.md 20.18). The trace
# is streamed: only the first SECONDS of it are downloaded (~1.5 GB of the
# ~12 GB file for 60 s), never stored whole.
#
# Usage (on the tgen, as your own user):
#   harness/trace/fetch_mawi.sh [<YYYYMMDDHHMM> [<seconds>]]
#   default: 202610141400 (Wed 14 Oct 2026, 14:00 JST), 60 s
# Writes $TRACE_DIR/mawi-<id>.json (TRACE_DIR default /mydata/trace).

set -euo pipefail

ID="${1:-202610141400}"
USE_S="${2:-60}"
TRACE_DIR="${TRACE_DIR:-/mydata/trace}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="http://mawi.nezu.wide.ad.jp/mawi/samplepoint-F/${ID:0:4}/${ID}.pcap.gz"
OUT="$TRACE_DIR/mawi-$ID.json"

[[ "$ID" =~ ^[0-9]{12}$ ]] || { echo "usage: $0 [<YYYYMMDDHHMM> [<seconds>]]" >&2; exit 1; }
mkdir -p "$TRACE_DIR"
echo "streaming the first $USE_S s of $URL"
# trace_schedule.py stops reading after USE_S seconds of trace; curl and
# gunzip then end on a broken pipe, which is expected, not a failure.
set +o pipefail
curl -s "$URL" | gunzip -c 2>/dev/null \
    | python3 "$SCRIPT_DIR/trace_schedule.py" - --seconds "$USE_S" --out "$OUT" \
        --source "MAWI samplepoint-F $ID ($URL), first $USE_S s"
set -o pipefail
[[ -s "$OUT" ]] || { echo "ERROR: $OUT was not written" >&2; exit 1; }
echo "ready: $OUT"
