#!/bin/sh
# Healthy when the qemu process exists. Real readiness comes from inside
# vEOS (it'll appear in /api/devices once it POSTs event=done to /log).
pgrep -f qemu-system-x86_64 >/dev/null
