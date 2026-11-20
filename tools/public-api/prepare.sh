#!/usr/bin/env bash
# Prepare the reviewed source and SDK-only checker in a new scratch directory.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
destination=${1:?expected a new output directory}
mkdir -p "$(dirname "$destination")"
mkdir "$destination"
destination=$(cd "$destination" && (pwd -W 2>/dev/null || pwd))
revision=$(tr -d '\r\n' < "$repo/tools/public-api/baseline.txt")
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || { echo "Expected a full public API baseline commit" >&2; exit 1; }
if ! git -C "$repo" cat-file -e "$revision^{commit}" 2>/dev/null; then
  git -C "$repo" fetch --no-tags --depth=1 origin "$revision"
fi
test "$(git -C "$repo" rev-parse "$revision^{commit}")" = "$revision"
git -C "$repo" archive "$revision" MCPForUnity/Editor MCPForUnity/Runtime | tar -xf - -C "$destination"
sdk_python=python3
if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then sdk_python=python; fi
"$sdk_python" "$repo/tools/run_dotnet.py" build "$repo/tools/public-api/checker/PublicApi.csproj" --nologo \
  --artifacts-path "$destination/dotnet" -o "$destination/checker"
printf '%s\n' "$revision" > "$destination/revision.txt"
echo "Prepared public API baseline $revision"
