#!/bin/sh
# Equal-architecture acceptance in the native updater bypasses this fallback.
if [ "$1" != Amlogic-ng.arm ] || [ "$2" != Amlogic-ng.arm ]; then
  echo "This candidate does not support cross-architecture migration." >&2
  exit 1
fi
if [ "$(uname -r)" = 3.14.29 ]; then
  echo "Legacy kernel upgrades are unsupported." >&2
  exit 1
fi
