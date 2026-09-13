#!/bin/sh
# Runs the package tests and fails unless tests actually ran.
#
# With only the Command Line Tools installed (no Xcode), SwiftPM does not discover Swift Testing on its
# own: plain `swift test` builds, runs zero tests and exits 0. Pointing swiftc at the CLT frameworks
# directory wakes the discovery up; Package.swift supplies the matching link/rpath flags.
set -eu
cd "$(dirname "$0")/.."

extra=""
case "$(xcode-select -p 2>/dev/null || true)" in
  /Library/Developer/CommandLineTools*)
    extra="-Xswiftc -F/Library/Developer/CommandLineTools/Library/Developer/Frameworks"
    ;;
esac

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

# shellcheck disable=SC2086
if ! swift test $extra "$@" 2>&1 | tee "$log"; then
  exit 1
fi

if ! grep -Eq 'Test run with [1-9][0-9]* tests? ' "$log"; then
  echo "scripts/test.sh: no tests ran (Swift Testing was not discovered)" >&2
  exit 1
fi
