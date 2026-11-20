#!/usr/bin/env bash
# Sourced by compile-check.sh after Unity has successfully compiled the candidate.
check_public_api() {
  local candidate="$1" baseline="$1/public-api/baseline" assembly side
  local assemblies=(Runtime)
  [ "$compile_profile" != editor ] || assemblies+=(Editor)
  for assembly in "${assemblies[@]}"; do
    local manifest="$REFS_PROFILE/$assembly.txt" package="MCPForUnity.$assembly"
    local extra=()
    if [ "$assembly" = Editor ]; then
      extra+=("$baseline/MCPForUnity.Runtime.dll" "$candidate/Unity.EditorCoroutines.Editor.dll" "${editor_roslyn_refs[@]}")
    fi
    compile "$package" "$PUBLIC_API_INPUTS/MCPForUnity/$assembly" "$platform" "$manifest" \
      --public-api-output "$baseline" "${extra[@]}" || return 1
    "${PUBLIC_API_DOTNET:-dotnet}" "$PUBLIC_API_INPUTS/checker/PublicApi.dll" "$REPO/tools/public-api/surface.txt" \
      "$baseline/$package.dll" "$candidate/$package.dll" \
      "$PUBLIC_API_INPUTS/MCPForUnity/$assembly/$package.asmdef" "$REPO/MCPForUnity/$assembly/$package.asmdef" || return 1
    for side in "$baseline" "$candidate"; do
      extra=("$side/$package.dll")
      [ "$assembly" != Editor ] || extra+=("$side/MCPForUnity.Runtime.dll")
      compile "IndependentExtension.$assembly" "$REPO/tools/public-api/consumer/$assembly" "$platform" "$manifest" \
        --public-api-output "$side/public-api-consumer" "${extra[@]}" || return 1
    done
    "${PUBLIC_API_DOTNET:-dotnet}" "$PUBLIC_API_INPUTS/checker/PublicApi.dll" --run-consumer \
      "$candidate/$package.dll" "$baseline/public-api-consumer/IndependentExtension.$assembly.dll" || return 1
  done
}
