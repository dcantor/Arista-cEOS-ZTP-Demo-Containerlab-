#!/bin/sh
# Print "running" or "stopped" depending on whether the VM (QEMU
# subprocess) is alive.
PIDFILE=/tmp/qemu.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "running"
else
    echo "stopped"
fi
