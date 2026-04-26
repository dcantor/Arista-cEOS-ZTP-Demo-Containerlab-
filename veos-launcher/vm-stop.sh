#!/bin/sh
# Stop the vEOS VM (QEMU subprocess). Idempotent: silently no-op if not
# running. Called by the ztp-app via `docker exec`.
set -eu

PIDFILE=/tmp/qemu.pid

if [ ! -f "$PIDFILE" ]; then
    echo "not running (no pidfile)"
    exit 0
fi

PID=$(cat "$PIDFILE")
if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "not running (stale pidfile cleaned)"
    exit 0
fi

# Try graceful shutdown first; QEMU treats SIGTERM as a clean shutdown
# request (it'll forward an ACPI powerdown to the guest).
kill "$PID" 2>/dev/null || true
i=0
while kill -0 "$PID" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
        echo "QEMU still alive after 15s, sending SIGKILL" >&2
        kill -KILL "$PID" 2>/dev/null || true
        break
    fi
    sleep 0.5
done

rm -f "$PIDFILE"
echo "stopped"
