# Publish a Docker image (manual).
 #
 # Requirements:
 # - Docker installed with buildx support (e.g. Docker Desktop).
 # - Authenticated to the target registry (e.g. run: docker login).
 # - Run from any directory; the script resolves the repository build context.
 #
 # Usage:
 #   ./docker_publish.sh <image> <version>
 #   IMAGE=<image> ./docker_publish.sh <version>
 #
 # Examples:
 #   ./docker_publish.sh msanatan/mcp-for-unity-server 9.3.1
 #   IMAGE=msanatan/mcp-for-unity-server ./docker_publish.sh v9.3.1
 #
 # Tags pushed:
 # - vX.Y.Z
 # - vX.Y
 # - vX
 # Prereleases receive only vX.Y.Z-prerelease (no major/minor aliases).
 # Versions may have a leading v; build metadata (+...) is unsupported.
 set -euo pipefail

 REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
 
 if [[ "${1:-}" == "" || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
   echo "Usage: $(basename "$0") <image> <version>" >&2
   echo "       $(basename "$0") <version>        # if IMAGE env var is set" >&2
   echo "Example: $(basename "$0") youruser/mcp-for-unity-server 1.2.3" >&2
   exit 2
 fi
 
 if [[ "${2:-}" != "" ]]; then
   IMAGE="$1"
   VERSION_RAW="$2"
 else
   if [[ "${IMAGE:-}" == "" ]]; then
     echo "Error: IMAGE env var is required when calling with a single arg." >&2
     echo "Usage: $(basename "$0") <image> <version>" >&2
     exit 2
   fi
   VERSION_RAW="$1"
 fi
 
 VERSION="${VERSION_RAW#v}"

 # SemVer core/prerelease syntax, restricted to Docker's unchanged tag syntax.
 NUMBER='(0|[1-9][0-9]*)'
 IDENTIFIER="($NUMBER|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
 VERSION_PATTERN="^$NUMBER\.$NUMBER\.$NUMBER(-$IDENTIFIER(\.$IDENTIFIER)*)?$"
 if [[ ! "$VERSION" =~ $VERSION_PATTERN || "$VERSION" == *[!0-9A-Za-z.-]* ]] || (( ${#VERSION} > 127 )); then
   echo "Error: unsupported version. Use X.Y.Z[-prerelease] without build metadata (tag limit: 128 characters)." >&2
   exit 2
 fi

 TAGS=(-t "$IMAGE:v$VERSION")
 if [[ "$VERSION" != *-* ]]; then
   MAJOR="${VERSION%%.*}"
   MINOR="${VERSION%.*}"
   TAGS+=(-t "$IMAGE:v$MINOR" -t "$IMAGE:v$MAJOR")
 fi
 
 docker buildx build \
   --platform linux/amd64 \
   -f "$REPO_ROOT/Server/Dockerfile" \
   "${TAGS[@]}" \
   --push \
   "$REPO_ROOT"
