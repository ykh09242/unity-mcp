#!/usr/bin/env bash
# Compile MCP for Unity's C# with Roslyn, against Unity's reference assemblies.
#
# WHY THIS EXISTS: Unity will not open a project without an activated license, and GitHub
# withholds secrets from fork PRs -- so PRs to this repo have historically gone unverified
# (unity-tests.yml skips and still reports a green check). This script never launches the
# Editor. It uses a Unity installation purely as a source of reference DLLs and invokes the
# bundled Roslyn compiler directly, which needs no license. That makes a real semantic
# compile check possible on any PR, including forks, with zero secrets.
#
# It does NOT run tests -- that still needs a licensed Editor.
#
# Usage (inside unityci/editor, or against a local Hub install):
#   UNITY_DATA=/opt/unity/Editor/Data UNITY_VERSION=2021.3.45f2 \
#   EDITOR_COROUTINES_SOURCE=.unity-ci/2021.3.45f2/packages/com.unity.editorcoroutines tools/compile-check.sh
#
# Windows, from Git Bash against a Hub install (no Docker, no license):
#   UNITY_DATA="C:/Program Files/Unity/Hub/Editor/2021.3.45f2/Editor/Data" UNITY_VERSION=2021.3.45f2 \
#   EXTRA_REFS=/c/refs EDITOR_COROUTINES_SOURCE=/c/packages/com.unity.editorcoroutines@1.0.1 tools/compile-check.sh
# where /c/refs holds Newtonsoft.Json.dll and nunit.framework.dll, e.g. copied from
# TestProjects/UnityMCPTests/Library/PackageCache/com.unity.nuget.newtonsoft-json@*/Runtime/ and
# .../com.unity.ext.nunit@*/net40/unity-custom/. Takes ~1 min per Unity version.
#
# Env:
#   UNITY_DATA     Editor/Data directory                 (default /opt/unity/Editor/Data)
#   UNITY_VERSION  e.g. 2021.3.45f2                      (required for version defines)
#   REPO           repo root                             (default: this script's parent)
#   EXTRA_REFS     dir holding Newtonsoft/nunit DLLs     (default $REPO/.compile-refs)
#   TEST_FRAMEWORK_SOURCE  extracted pinned UPM package   (optional; compiles its TestRunner APIs)
#   EDITOR_COROUTINES_SOURCE extracted pinned UPM package (required; compiles its Editor assembly)
#   TEST_PROJECT   isolated test project                 (optional; compiles fixture and EditMode tests)
#   COMPILE_INPUT_UGUI  compile optional uGUI assemblies  (default 0; set 1 to enable)
#   COMPILE_INPUT_SYSTEM compile optional Input System adapters (default 0)
#   INPUT_SYSTEM_REFS directory with verified Unity.InputSystem DLLs (required when selected)
#   DISABLED_UNITY_MODULES space-separated physics/physics2d/screencapture (default empty; removes metadata and symbols)
#   PLATFORMS      editor platforms to compile           (default "win osx linux")
#   OUT            scratch dir                           (default /tmp/mcp-compile-check)
#
# MAINTENANCE: tools/compile-refs/{Runtime,Editor}.txt and tools/compile-defines.txt are
# captured from Unity's own generated .csproj files for the pinned defaultVersion. They are
# NOT globs on purpose -- Editor/Data holds the entire .NET 4.8 BCL plus vendored libraries
# (ExCSS.Unity redefines System.Tuple; cscompmgd.dll redefines Microsoft.CSharp.CompilerError)
# that Unity deliberately does not reference. Regenerate them when defaultVersion changes:
# open TestProjects/UnityMCPTests in that Editor, then re-derive from the generated csprojs.
# Keep this portable reference set free of removed modules and optional playback SDKs.
# Every remaining declared reference is required; missing metadata fails before Roslyn.
# Unity 2021.3/2022.3 use explicit legacy module profiles, with shared Runtime/Editor BCL
# lists. Keep profile selection tied to the version, never to whichever DLLs happen to exist.
set -uo pipefail

die() { echo "::error::$*" >&2; exit 2; }

# Roslyn runs as a Windows process under Git Bash, so every path handed to it must be
# X:/... form, not the MSYS /x/... form that `pwd` produces there. `pwd -W` is the MSYS
# spelling; on Linux/macOS it is an invalid option and we fall back to plain `pwd`.
winpath() { (cd "$1" 2>/dev/null && (pwd -W 2>/dev/null || pwd)) || die "directory not found: $1"; }

UNITY_DATA=$(winpath "${UNITY_DATA:-/opt/unity/Editor/Data}")
REPO=$(winpath "${REPO:-"$(dirname "${BASH_SOURCE[0]}")/.."}")
EXTRA_REFS=${EXTRA_REFS:-"$REPO/.compile-refs"}
[ -d "$EXTRA_REFS" ] && EXTRA_REFS=$(winpath "$EXTRA_REFS")
TEST_FRAMEWORK_SOURCE=${TEST_FRAMEWORK_SOURCE:-}
if [ -n "$TEST_FRAMEWORK_SOURCE" ]; then
  TEST_FRAMEWORK_SOURCE=$(winpath "$TEST_FRAMEWORK_SOURCE") || exit 2
fi
EDITOR_COROUTINES_SOURCE=${EDITOR_COROUTINES_SOURCE:-}
[ -n "$EDITOR_COROUTINES_SOURCE" ] || die "EDITOR_COROUTINES_SOURCE must be set to the pinned Editor Coroutines package"
EDITOR_COROUTINES_SOURCE=$(winpath "$EDITOR_COROUTINES_SOURCE") || exit 2
[ -f "$EDITOR_COROUTINES_SOURCE/Editor/Unity.EditorCoroutines.Editor.asmdef" ] \
  || die "Editor Coroutines assembly definition not found: Unity.EditorCoroutines.Editor"
[ -n "$(find "$EDITOR_COROUTINES_SOURCE/Editor" -name '*.cs' -type f -print -quit)" ] \
  || die "Editor Coroutines Editor sources not found"
TEST_PROJECT=${TEST_PROJECT:-}
if [ -n "$TEST_PROJECT" ]; then
  TEST_PROJECT=$(winpath "$TEST_PROJECT") || exit 2
  for entry in Assets/Scripts/TestAsmdef/TestAsmdef.asmdef Assets/Tests/EditMode/MCPForUnityTests.Editor.asmdef; do
    [ -f "$TEST_PROJECT/$entry" ] || die "test assembly definition not found: $entry"
    source_dir="$TEST_PROJECT/${entry%/*}"
    [ -n "$(find "$source_dir" -name '*.cs' -type f -print -quit)" ] || die "test assembly sources not found: $source_dir"
  done
fi
COMPILE_INPUT_UGUI=${COMPILE_INPUT_UGUI:-0}
case "$COMPILE_INPUT_UGUI" in 0|1) ;; *) die "COMPILE_INPUT_UGUI must be 0 or 1" ;; esac
if [ "$COMPILE_INPUT_UGUI" -eq 1 ]; then
  for entry in \
    MCPForUnity/Runtime/PlayScenarios/UGUI/MCPForUnity.Input.UGUI.Runtime.asmdef \
    MCPForUnity/Editor/Tools/Input/UGUI/MCPForUnity.Input.UGUI.Editor.asmdef; do
    [ -f "$REPO/$entry" ] || die "optional uGUI assembly definition not found: $entry"
  done
  if [ -n "$TEST_PROJECT" ]; then
    entry=Assets/Tests/EditMode/Tools/Input/UGUI/MCPForUnity.Input.UGUI.Tests.asmdef
    [ -f "$TEST_PROJECT/$entry" ] || die "optional uGUI test assembly definition not found: $entry"
  fi
fi
COMPILE_INPUT_SYSTEM=${COMPILE_INPUT_SYSTEM:-0}
case "$COMPILE_INPUT_SYSTEM" in 0|1) ;; *) die "COMPILE_INPUT_SYSTEM must be 0 or 1" ;; esac
INPUT_SYSTEM_REFS=${INPUT_SYSTEM_REFS:-}
if [ "$COMPILE_INPUT_SYSTEM" -eq 1 ]; then
  [ -n "$INPUT_SYSTEM_REFS" ] || die "INPUT_SYSTEM_REFS must select verified Input System reference assemblies"
  INPUT_SYSTEM_REFS=$(winpath "$INPUT_SYSTEM_REFS") || exit 2
  [ -f "$INPUT_SYSTEM_REFS/Unity.InputSystem.dll" ] || die "Input System reference not found: Unity.InputSystem.dll"
  for entry in \
    MCPForUnity/Runtime/PlayScenarios/InputSystem/MCPForUnity.Input.InputSystem.Runtime.asmdef \
    MCPForUnity/Editor/Tools/Input/InputSystem/MCPForUnity.Input.InputSystem.Editor.asmdef; do
    [ -f "$REPO/$entry" ] || die "optional Input System assembly definition not found: $entry"
  done
  if [ -n "$TEST_PROJECT" ]; then
    entry=Assets/Tests/EditMode/Tools/Input/InputSystem/MCPForUnity.Input.InputSystem.Tests.asmdef
    [ -f "$TEST_PROJECT/$entry" ] || die "optional Input System test assembly definition not found: $entry"
    [ -f "$INPUT_SYSTEM_REFS/Unity.InputSystem.TestFramework.dll" ] || die "Input System reference not found: Unity.InputSystem.TestFramework.dll"
  fi
fi
DISABLED_UNITY_MODULES=${DISABLED_UNITY_MODULES:-}
for module in $DISABLED_UNITY_MODULES; do
  case "$module" in physics|physics2d|screencapture) ;; *) die "unknown optional Unity module: $module" ;; esac
done
module_enabled() {
  local disabled
  for disabled in $DISABLED_UNITY_MODULES; do
    [ "$disabled" != "$1" ] || return 1
  done
  return 0
}
module_reference_enabled() {
  case "$1" in
    */UnityEngine.PhysicsModule.dll) module_enabled physics ;;
    */UnityEngine.Physics2DModule.dll) module_enabled physics2d ;;
    */UnityEngine.ScreenCaptureModule.dll) module_enabled screencapture ;;
    *) return 0 ;;
  esac
}
PLATFORMS=${PLATFORMS:-"win osx linux"}
OUT=${OUT:-/tmp/mcp-compile-check}
mkdir -p "$OUT" && OUT=$(winpath "$OUT")
LIBCACHE="$UNITY_DATA/Resources/PackageManager/ProjectTemplates/libcache"
UNITY_VERSION=${UNITY_VERSION:-}
[ -n "$UNITY_VERSION" ] || die "UNITY_VERSION must be set (e.g. 2021.3.45f2)"
ver_major=${UNITY_VERSION%%.*}
version_tail=${UNITY_VERSION#*.}
ver_minor=${version_tail%%.*}
ver_patch=${version_tail#*.}
ver_patch=${ver_patch%%.*}
ver_patch=${ver_patch%%[a-z]*}

compiler_failure() {
  echo "Compiler distribution candidates (diagnostic only):" >&2
  find "$UNITY_DATA" -maxdepth 10 -type f \
    \( -name 'csc.dll' -o -name 'csc.exe' -o -name 'dotnet' -o -name 'dotnet.exe' \) \
    -print 2>/dev/null | sort >&2
  die "$*"
}

# Unity 6.6+ locates Roslyn inside its scripting DotNetSdk. Require one coherent
# SDK rather than choosing the first version or mixing it with a system runtime.
if [ "$ver_major" -gt 6000 ] || { [ "$ver_major" -eq 6000 ] && [ "$ver_minor" -ge 6 ]; }; then
  sdk_roots=()
  sdk_inventory=$(find "$UNITY_DATA" -maxdepth 10 -name DotNetSdk -print) \
    || compiler_failure "SDK directory inventory failed"
  while IFS= read -r root; do [ -z "$root" ] || sdk_roots+=("$root"); done <<< "$sdk_inventory"
  [ "${#sdk_roots[@]}" -eq 1 ] || compiler_failure "SDK root must be unique: found ${#sdk_roots[@]}"
  sdk_root=${sdk_roots[0]}
  canonical_data=$(readlink -f "$UNITY_DATA") || compiler_failure "SDK data path cannot be resolved"
  sdk_links=$(find "$sdk_root" -maxdepth 10 -type l -print) || compiler_failure "SDK symlink inventory failed"
  while IFS= read -r link; do
    [ -n "$link" ] || continue
    target=$(readlink -f "$link") || compiler_failure "SDK symlink cannot be resolved: $link"
    case "$target" in "$canonical_data"/*) ;; *) compiler_failure "SDK external symlink: $link" ;; esac
  done <<< "$sdk_links"
  if [ -L "$sdk_root" ]; then
    compiler_failure "SDK root cannot be a symlink: $sdk_root"
  fi
  [ -d "$sdk_root/sdk" ] || compiler_failure "SDK version directory not found: $sdk_root/sdk"
  sdk_compilers=()
  for sdk_version in "$sdk_root"/sdk/*; do
    [[ "${sdk_version##*/}" =~ ^[1-9][0-9]*\.[0-9]+\.[0-9]+$ ]] || continue
    [ -f "$sdk_version/Roslyn/bincore/csc.dll" ] || compiler_failure "SDK Roslyn compiler not found: $sdk_version"
    sdk_compilers+=("$sdk_version/Roslyn/bincore/csc.dll")
  done
  [ "${#sdk_compilers[@]}" -eq 1 ] || compiler_failure "SDK compiler must be unique: found ${#sdk_compilers[@]}"
  CSC=${sdk_compilers[0]}
  # Enumerate real directory entries: Git Bash's -f/-x checks transparently add
  # .exe and would count one Windows executable twice when probing both names.
  sdk_runtimes=()
  runtime_inventory=$(find "$sdk_root" -maxdepth 1 \( -type f -o -type l \) \
    \( -name dotnet -o -name dotnet.exe \) -print) || compiler_failure "SDK runtime inventory failed"
  while IFS= read -r runtime; do [ -z "$runtime" ] || sdk_runtimes+=("$runtime"); done <<< "$runtime_inventory"
  [ "${#sdk_runtimes[@]}" -eq 1 ] || compiler_failure "SDK executable runtime must be unique: found ${#sdk_runtimes[@]}"
  DOTNET=${sdk_runtimes[0]}
  [ -f "$DOTNET" ] && [ -x "$DOTNET" ] || compiler_failure "SDK runtime is not executable: $DOTNET"
  export DOTNET_ROOT="$sdk_root" DOTNET_MULTILEVEL_LOOKUP=0
else
  CSC="$UNITY_DATA/DotNetSdkRoslyn/csc.dll"
  [ -f "$CSC" ] || compiler_failure "Roslyn compiler not found: $CSC"
  DOTNET="$UNITY_DATA/NetCoreRuntime/dotnet"
  [ -x "$DOTNET" ] || DOTNET="$(command -v dotnet)" || die "no dotnet runtime available"
fi

echo "Unity version : $UNITY_VERSION"
echo "Unity data    : $UNITY_DATA"
echo "Compiler      : $CSC"
echo "Runtime       : $DOTNET"
echo "Editor Coroutines: $EDITOR_COROUTINES_SOURCE"
if [ -n "$TEST_FRAMEWORK_SOURCE" ]; then
  echo "Test Framework: $TEST_FRAMEWORK_SOURCE"
  for assembly in UnityEngine.TestRunner UnityEditor.TestRunner; do
    [ -d "$TEST_FRAMEWORK_SOURCE/$assembly" ] || die "Test Framework source not found: $assembly"
  done
fi

# ---------------------------------------------------------------- defines ----
# The version ladder must be exact: defining UNITY_2022_1_OR_NEWER on a 2021.3 build
# compiles the wrong #if branches and invents errors that do not exist.
UNITY_RELEASES="5.3 5.4 5.5 5.6 2017.1 2017.2 2017.3 2017.4 2018.1 2018.2 2018.3 2018.4 \
2019.1 2019.2 2019.3 2019.4 2020.1 2020.2 2020.3 2021.1 2021.2 2021.3 2022.1 2022.2 2022.3 2023.1 2023.2 \
6000.0 6000.1 6000.2 6000.3 6000.4 6000.5 6000.6 6000.7"

REFS_ROOT="$REPO/tools/compile-refs"
case "$ver_major.$ver_minor" in
  2021.3|2022.3|6000.3|6000.6|6000.7|7000.0) REFS_PROFILE="$REFS_ROOT/$ver_major.$ver_minor" ;;
  *) REFS_PROFILE="$REFS_ROOT" ;;
esac

version_defines() {
  local rel rM rm
  for rel in $UNITY_RELEASES; do
    rM=${rel%%.*}; rm=${rel##*.}
    if [ "$rM" -lt "$ver_major" ] || { [ "$rM" -eq "$ver_major" ] && [ "$rm" -le "$ver_minor" ]; }; then
      echo "-define:UNITY_${rM}_${rm}_OR_NEWER"
    fi
  done
  # A newly released minor also defines its own OR_NEWER symbol before the static
  # historical ladder is refreshed (including alpha and beta Editor versions).
  case " $UNITY_RELEASES " in
    *" $ver_major.$ver_minor "*) ;;
    *) echo "-define:UNITY_${ver_major}_${ver_minor}_OR_NEWER" ;;
  esac
  echo "-define:UNITY_${ver_major}"
  echo "-define:UNITY_${ver_major}_${ver_minor}"
  [ -n "$ver_patch" ] && echo "-define:UNITY_${ver_major}_${ver_minor}_${ver_patch}"
}

platform_defines() {
  case "$1" in
    win)   printf '%s\n' -define:UNITY_EDITOR_WIN -define:UNITY_STANDALONE_WIN -define:PLATFORM_STANDALONE_WIN ;;
    osx)   printf '%s\n' -define:UNITY_EDITOR_OSX -define:UNITY_STANDALONE_OSX -define:PLATFORM_STANDALONE_OSX ;;
    linux) printf '%s\n' -define:UNITY_EDITOR_LINUX -define:UNITY_STANDALONE_LINUX -define:PLATFORM_STANDALONE_LINUX ;;
    *) die "unknown platform '$1' (expected win|osx|linux)" ;;
  esac
}

# --------------------------------------------------------------- references ----
# Resolve in the caller's shell; ordinary manifest entries need no subshell.
resolve_ref() {
  resolved_ref=
  case "$1" in
    DATA/*)     resolved_ref="$UNITY_DATA/${1#DATA/}" ;;
    EXTRA/*)    resolved_ref="$EXTRA_REFS/${1#EXTRA/}" ;;
    COMPILED/*) resolved_ref="$OUT/$platform/${1#COMPILED/}" ;;
    LIBCACHE/*)
      if [ -n "$TEST_FRAMEWORK_SOURCE" ] && [[ "$1" == *TestRunner.dll ]]; then
        resolved_ref="$OUT/$platform/${1#LIBCACHE/}"
      else
        resolved_ref=$(find "$LIBCACHE" -path '*/ScriptAssemblies/*' -name "${1#LIBCACHE/}" 2>/dev/null | head -1)
      fi ;;
  esac
}

# Enumerate only sources owned by one asmdef. Nested asmdefs compile separately,
# including disabled optional assemblies; never merge their types into a parent.
assembly_sources() {
  local srcdir="$1" definition
  local prune=()
  while IFS= read -r -d '' definition; do
    [ "${#prune[@]}" -eq 0 ] || prune+=(-o)
    prune+=(-path "${definition%/*}")
  done < <(find "$srcdir" -mindepth 2 -name '*.asmdef' -type f -print0)
  if [ "${#prune[@]}" -gt 0 ]; then
    find "$srcdir" \( "${prune[@]}" \) -prune -o -name '*.cs' -type f -print | sort
  else
    find "$srcdir" -name '*.cs' -type f -print | sort
  fi
}

# Source trees and asmdef ownership are fixed inputs for one invocation. Reuse
# their sorted inventories across assembly variants/platforms, never across runs.
# Indexed arrays also support Bash versions without associative arrays.
source_directories=()
source_inventories=()
load_assembly_sources() {
  local srcdir="$1" index
  for index in "${!source_directories[@]}"; do
    if [ "${source_directories[$index]}" = "$srcdir" ]; then
      assembly_source_list="${source_inventories[$index]}"
      return 0
    fi
  done
  assembly_source_list=$(assembly_sources "$srcdir") || return 1
  source_directories+=("$srcdir")
  source_inventories+=("$assembly_source_list")
}

# ------------------------------------------------------------------ compile ----
# Output assembly names must be exactly MCPForUnity.Runtime / MCPForUnity.Editor:
# MCPForUnity/Runtime/AssemblyInfo.cs grants InternalsVisibleTo by assembly NAME, so a
# platform suffix in the filename would make Runtime's internals invisible to Editor.
compile() {
  local name="$1" srcdir="$2" platform="$3" manifest="$4"; shift 4
  local dir="$OUT/$platform"
  [ -d "$dir" ] || mkdir -p "$dir" || return 1
  local rsp="$dir/$name.rsp"
  local missing=0 nrefs=0 nsrc=0 sources
  local bcl="$REFS_ROOT/BCL/Editor.txt"
  # Initial Unity 7 uses CoreCLR with the .NET Standard 2.1 scripting API surface,
  # including Editor assemblies. The bundled .NET 10 SDK does not widen that surface.
  case "$ver_major.$ver_minor:$name" in
    7000.0:*|*:MCPForUnity.Runtime|*:MCPForUnity.Input.UGUI.Runtime|*:MCPForUnity.Input.InputSystem.Runtime|*:TestAsmdef) bcl="$REFS_ROOT/BCL/Runtime.txt" ;;
  esac
  [ -d "$srcdir" ] && load_assembly_sources "$srcdir" && [ -n "$assembly_source_list" ] || {
    echo "::error::assembly sources not found: $srcdir" >&2; return 1;
  }
  sources=$assembly_source_list
  while IFS= read -r f; do
    [ -f "$f" ] || { echo "::error::assembly source not found: $f" >&2; return 1; }
  done <<< "$sources"
  for required in "$bcl" "$manifest"; do
    [ -f "$required" ] || { echo "::error::reference manifest not found: $required" >&2; return 1; }
  done
  local ignored_warnings=CS1701,CS1702 # benign netstandard facade version unification
  if [ "$name" = UnityEditor.TestRunner ]; then
    # Native Unity also disables these unused/unassigned field diagnostics for TestRunner.
    ignored_warnings+=,CS0169,CS0649
    if [ "$UNITY_VERSION" = 6000.0.84f1 ]; then
      # Verified bundled UTF 1.6.0 SwitchPlatformSetup uses obsolete switchRedirectWritesToHostMount.
      # Keep this vendor exception tied to the exact Editor; product obsolescence stays fatal.
      ignored_warnings+=,CS0618
    fi
  fi

  {
    echo "-target:library"
    echo "-langversion:9.0"
    echo "-nostdlib+"
    echo "-preferreduilang:en-US"
    echo "-nowarn:$ignored_warnings"
    echo "-out:\"$dir/$name.dll\""
    case "$name" in UnityEngine.TestRunner|UnityEditor.TestRunner) echo "-define:UNITY_TESTS_FRAMEWORK" ;; esac
    case "$name" in
      MCPForUnity.Runtime|MCPForUnity.Editor|MCPForUnity.Input.UGUI.*|MCPForUnity.Input.InputSystem.*|MCPForUnity.CustomTools.Roslyn*|TestAsmdef|MCPForUnityTests.EditMode)
        echo "-warnaserror+" ;;
    esac
    case "$name" in MCPForUnity.Input.UGUI.*) echo "-define:MCP_INPUT_UGUI" ;; esac
    case "$name" in MCPForUnity.Input.InputSystem.*) echo "-define:MCP_INPUT_SYSTEM"; echo "-define:ENABLE_INPUT_SYSTEM" ;; esac
    case "$name" in MCPForUnity.CustomTools.RoslynOn) echo "-define:USE_ROSLYN" ;; esac
    case "$name" in
      UnityEngine.UI|UnityEditor.UI)
        # Module versionDefines from the verified bundled uGUI 7.0.0 asmdefs.
        module_enabled physics && echo "-define:PACKAGE_PHYSICS"
        module_enabled physics2d && echo "-define:PACKAGE_PHYSICS2D"
        echo "-define:PACKAGE_ANIMATION" ;;
    esac
    if [ "$name" = UnityEngine.UI ]; then
      printf '%s\n' -define:PACKAGE_TILEMAP -define:PACKAGE_UITOOLKIT
    fi
    # ${var%$'\r'} strips the CR a core.autocrlf checkout appends to every line: a CR inside
    # -define:FOO silently defines the wrong symbol, and inside a LIBCACHE/ name it makes
    # `find -name` match nothing, so the Editor build fails on TestRunner/UI types.
    while read -r d; do
      d=${d%$'\r'}
      case "$ver_major.$ver_minor:$d" in
        7000.0:ENABLE_MONO|7000.0:PLATFORM_SUPPORTS_MONO|7000.0:NET_4_6|7000.0:NET_UNITY_4_8) continue ;;
      esac
      [ -n "$d" ] && echo "-define:$d"
    done < "$REPO/tools/compile-defines.txt"
    if [ "$ver_major.$ver_minor" = 7000.0 ]; then
      printf '%s\n' -define:ENABLE_CORECLR -define:NET_STANDARD_2_1 -define:NET_STANDARD -define:NETSTANDARD2_1 -define:NETSTANDARD
    fi
    module_enabled physics && echo "-define:MCP_FOR_UNITY_PHYSICS"
    module_enabled physics2d && echo "-define:MCP_FOR_UNITY_PHYSICS2D"
    module_enabled screencapture && echo "-define:MCP_FOR_UNITY_SCREEN_CAPTURE"
    version_defines
    platform_defines "$platform"
    for reference_manifest in "$bcl" "$manifest"; do
      while read -r entry; do
        entry=${entry%$'\r'}
        [ -n "$entry" ] || continue
        # Compile the pinned package itself without referencing the template's TestRunner.
        case "$name:$entry" in
          UnityEngine.TestRunner:LIBCACHE/*TestRunner.dll|UnityEditor.TestRunner:LIBCACHE/*TestRunner.dll|Unity.EditorCoroutines.Editor:LIBCACHE/*TestRunner.dll|UnityEngine.UI:LIBCACHE/*TestRunner.dll|UnityEditor.UI:LIBCACHE/*TestRunner.dll|UnityEngine.UI:COMPILED/*|UnityEditor.UI:COMPILED/*) continue ;;
        esac
        module_reference_enabled "$entry" || continue
        resolve_ref "$entry"
        local p="$resolved_ref"
        if [ -n "$p" ] && [ -f "$p" ]; then echo "-r:\"$p\""; nrefs=$((nrefs+1))
        else echo "::error::required reference not found: $entry" >&2; missing=$((missing+1)); fi
      done < "$reference_manifest"
    done
    for r in "$@"; do
      if [ -f "$r" ]; then echo "-r:\"$r\""; nrefs=$((nrefs+1))
      else echo "::error::required assembly reference not found: $r" >&2; missing=$((missing+1)); fi
    done
    while read -r f; do echo "\"$f\""; nsrc=$((nsrc+1)); done <<< "$sources"
  } > "$rsp"

  if [ "$missing" -ne 0 ]; then
    echo "::error::$name [$platform] has $missing missing required references" >&2
    return 1
  fi

  echo "--- $name [$platform] : $nsrc sources, $nrefs refs ---"
  # Filter the compiler stream with shell builtins; per-assembly grep/sed forks
  # are expensive on Windows. Keep the compiler's exit status.
  "$DOTNET" "$CSC" "@$rsp" 2>&1 | while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'Microsoft (R)'*|Copyright*) ;;
      *) printf '%s\n' "$line" ;;
    esac
  done
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ] || [ ! -f "$dir/$name.dll" ]; then
    echo "::error::$name failed to compile for $platform"
    return 1
  fi
  echo "OK  $name [$platform]"
}

failed=0
for platform in $PLATFORMS; do
  if [ "$ver_major.$ver_minor" = 7000.0 ]; then
    ugui="$UNITY_DATA/Resources/PackageManager/BuiltInPackages/com.unity.ugui"
    compile UnityEngine.UI "$ugui/Runtime/UGUI" "$platform" "$REFS_PROFILE/Runtime.txt" \
      || { failed=1; continue; }
    compile UnityEditor.UI "$ugui/Editor/UGUI" "$platform" "$REFS_PROFILE/Editor.txt" \
      "$OUT/$platform/UnityEngine.UI.dll" || { failed=1; continue; }
  fi
  if [ -n "$TEST_FRAMEWORK_SOURCE" ]; then
    compile UnityEngine.TestRunner "$TEST_FRAMEWORK_SOURCE/UnityEngine.TestRunner" "$platform" \
      "$REFS_PROFILE/Editor.txt" || { failed=1; continue; }
    cecil_refs=()
    # These editor versions ship Unity's fork with the Mono.Cecil namespace. Select
    # its complete editor-managed group by version, never by DLL search order.
    case "$ver_major.$ver_minor" in
      2021.3|2022.3|6000.3|6000.6|6000.7|7000.0) cecil_dir="$UNITY_DATA/Managed"; cecil_name=Unity.Cecil ;;
      *) cecil_dir="$UNITY_DATA/Tools/Compilation/ApiUpdater"; cecil_name=Mono.Cecil ;;
    esac
    for suffix in '' .Pdb .Mdb .Rocks; do
      ref="$cecil_dir/$cecil_name$suffix.dll"
      if [ ! -f "$ref" ]; then
        echo "available Cecil reference candidates (diagnostic only):" >&2
        find "$UNITY_DATA/Managed" "$UNITY_DATA/Tools/ScriptUpdater" "$UNITY_DATA/Tools/Compilation/ApiUpdater" \
          -maxdepth 1 -type f \( -name 'Mono.Cecil*.dll' -o -name 'Unity.Cecil*.dll' \) 2>/dev/null | sort >&2 || true
        die "Test Framework reference not found: $ref"
      fi
      cecil_refs+=("$ref")
    done
    compile UnityEditor.TestRunner "$TEST_FRAMEWORK_SOURCE/UnityEditor.TestRunner" "$platform" \
      "$REFS_PROFILE/Editor.txt" "$OUT/$platform/UnityEngine.TestRunner.dll" \
      "${cecil_refs[@]}" || { failed=1; continue; }
  fi
  compile MCPForUnity.Runtime "$REPO/MCPForUnity/Runtime" "$platform" \
    "$REFS_PROFILE/Runtime.txt" || { failed=1; continue; }
  if [ "$COMPILE_INPUT_UGUI" -eq 1 ]; then
    compile MCPForUnity.Input.UGUI.Runtime "$REPO/MCPForUnity/Runtime/PlayScenarios/UGUI" "$platform" \
      "$REFS_PROFILE/Runtime.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" \
      || { failed=1; continue; }
  fi
  if [ "$COMPILE_INPUT_SYSTEM" -eq 1 ]; then
    compile MCPForUnity.Input.InputSystem.Runtime "$REPO/MCPForUnity/Runtime/PlayScenarios/InputSystem" "$platform" \
      "$REFS_PROFILE/Runtime.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" "$INPUT_SYSTEM_REFS/Unity.InputSystem.dll" \
      || { failed=1; continue; }
  fi
  compile Unity.EditorCoroutines.Editor "$EDITOR_COROUTINES_SOURCE/Editor" "$platform" \
    "$REFS_PROFILE/Editor.txt" || { failed=1; continue; }
  compile MCPForUnity.Editor "$REPO/MCPForUnity/Editor" "$platform" \
    "$REFS_PROFILE/Editor.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" \
    "$OUT/$platform/Unity.EditorCoroutines.Editor.dll" || { failed=1; continue; }
  if [ "$COMPILE_INPUT_UGUI" -eq 1 ]; then
    compile MCPForUnity.Input.UGUI.Editor "$REPO/MCPForUnity/Editor/Tools/Input/UGUI" "$platform" \
      "$REFS_PROFILE/Editor.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" \
      "$OUT/$platform/MCPForUnity.Editor.dll" "$OUT/$platform/MCPForUnity.Input.UGUI.Runtime.dll" \
      || { failed=1; continue; }
  fi
  if [ "$COMPILE_INPUT_SYSTEM" -eq 1 ]; then
    compile MCPForUnity.Input.InputSystem.Editor "$REPO/MCPForUnity/Editor/Tools/Input/InputSystem" "$platform" \
      "$REFS_PROFILE/Editor.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" "$OUT/$platform/MCPForUnity.Editor.dll" \
      "$OUT/$platform/MCPForUnity.Input.InputSystem.Runtime.dll" "$INPUT_SYSTEM_REFS/Unity.InputSystem.dll" \
      || { failed=1; continue; }
  fi
  # Use Unity's coherent Mono/.NET Framework Roslyn group in both modes: the
  # compiler helper still references CodeAnalysis under UNITY_EDITOR when off.
  roslyn_refs=()
  for name in Microsoft.CodeAnalysis Microsoft.CodeAnalysis.CSharp System.Collections.Immutable System.Reflection.Metadata; do
    roslyn_refs+=("$UNITY_DATA/MonoBleedingEdge/lib/mono/4.5/$name.dll")
  done
  for assembly in MCPForUnity.CustomTools.RoslynOff MCPForUnity.CustomTools.RoslynOn; do
    compile "$assembly" "$REPO/CustomTools/RoslynRuntimeCompilation" "$platform" \
      "$REFS_PROFILE/Editor.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" \
      "$OUT/$platform/MCPForUnity.Editor.dll" "${roslyn_refs[@]}" || failed=1
  done
  if [ -n "$TEST_PROJECT" ]; then
    # These names/references mirror the owned asmdefs; the harness contract test
    # checks their JSON so a changed assembly graph cannot silently lose coverage.
    compile TestAsmdef "$TEST_PROJECT/Assets/Scripts/TestAsmdef" "$platform" \
      "$REFS_PROFILE/Runtime.txt" || { failed=1; continue; }
    compile MCPForUnityTests.EditMode "$TEST_PROJECT/Assets/Tests/EditMode" "$platform" \
      "$REFS_PROFILE/Editor.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" \
      "$OUT/$platform/MCPForUnity.Editor.dll" "$OUT/$platform/TestAsmdef.dll" \
      "$OUT/$platform/Unity.EditorCoroutines.Editor.dll" || { failed=1; continue; }
    if [ "$COMPILE_INPUT_UGUI" -eq 1 ]; then
      compile MCPForUnity.Input.UGUI.Tests "$TEST_PROJECT/Assets/Tests/EditMode/Tools/Input/UGUI" "$platform" \
        "$REFS_PROFILE/Editor.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" \
        "$OUT/$platform/MCPForUnity.Editor.dll" "$OUT/$platform/MCPForUnity.Input.UGUI.Editor.dll" \
        "$OUT/$platform/MCPForUnity.Input.UGUI.Runtime.dll" \
        || failed=1
    fi
    if [ "$COMPILE_INPUT_SYSTEM" -eq 1 ]; then
      compile MCPForUnity.Input.InputSystem.Tests "$TEST_PROJECT/Assets/Tests/EditMode/Tools/Input/InputSystem" "$platform" \
        "$REFS_PROFILE/Editor.txt" "$OUT/$platform/MCPForUnity.Runtime.dll" "$OUT/$platform/MCPForUnity.Editor.dll" \
        "$OUT/$platform/MCPForUnity.Input.InputSystem.Runtime.dll" "$OUT/$platform/MCPForUnity.Input.InputSystem.Editor.dll" \
        "$INPUT_SYSTEM_REFS/Unity.InputSystem.dll" "$INPUT_SYSTEM_REFS/Unity.InputSystem.TestFramework.dll" \
        || failed=1
    fi
  fi
done

[ "$failed" -eq 0 ] || { echo "::error::compile check FAILED"; exit 1; }
echo "compile check passed for: $PLATFORMS"
