#!/bin/sh
# Start the vEOS VM (QEMU subprocess) inside this wrapper container.
# Idempotent: if the VM is already running, exits 0 with "already running".
# Called by the ztp-app via `docker exec`.
set -eu

PIDFILE=/tmp/qemu.pid
LOGFILE=/tmp/qemu.log
CMDFILE=/tmp/qemu-cmd.sh

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "already running (pid $(cat "$PIDFILE"))"
    exit 0
fi

if [ ! -x "$CMDFILE" ]; then
    echo "qemu-cmd.sh not ready (launcher hasn't finished bridge setup)" >&2
    exit 1
fi

# Detach from the exec session so the QEMU process is reparented to PID 1
# (tini) and survives this script's exit.
nohup "$CMDFILE" >"$LOGFILE" 2>&1 &
echo $! >"$PIDFILE"
echo "started pid $(cat "$PIDFILE")"
