#!/bin/sh
# Healthy as soon as the launcher has finished bridge setup and written
# the QEMU command file (vm-start.sh is then ready to be invoked). The
# VM itself may or may not be currently running — that's a separate
# concern, surfaced via /api/devices/<host>/status.
test -x /tmp/qemu-cmd.sh
