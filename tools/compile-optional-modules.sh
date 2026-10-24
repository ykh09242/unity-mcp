#!/usr/bin/env bash
# Compile every non-default combination without launching Unity. The ordinary
# compile step covers all modules present, including the complete test suite.
set -euo pipefail

script_directory=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
output_root=${OUT:-/tmp/mcp-compile-optional-modules}
modules=(physics physics2d screencapture)
case "${OPTIONAL_MODULE_MATRIX:-key}" in
  key) masks=(1 2 4 7) ;;
  all) masks=(1 2 3 4 5 6 7) ;;
  *) echo "OPTIONAL_MODULE_MATRIX must be key or all" >&2; exit 2 ;;
esac

for mask in "${masks[@]}"; do
  disabled=()
  for index in 0 1 2; do
    if (( mask & (1 << index) )); then
      disabled+=("${modules[$index]}")
    fi
  done
  echo "Optional module compile: absent ${disabled[*]}"
  # The existing test project includes deliberate physics fixtures. Product and
  # selected input adapters still compile; native fixtures run in their own jobs.
  DISABLED_UNITY_MODULES="${disabled[*]}" TEST_PROJECT= OUT="$output_root/$mask" \
    bash "$script_directory/compile-check.sh"
done
