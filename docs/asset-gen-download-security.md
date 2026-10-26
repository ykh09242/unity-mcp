# Asset generation download policy

Provider result URLs are untrusted. Model, image, audio and Sketchfab artifact downloads
share `AssetDownloadPolicy` and `AssetDownloadTransport`.

Downloads require HTTPS on port 443, no userinfo or URL fragment, and a host from the
selected provider's list. The current list is deliberately narrow:

| Provider | Artifact hosts | Provider reference |
| --- | --- | --- |
| Tripo | Exact `cdn.tripo3d.ai` and `tripo-data.rg1.data.tripo3d.com` | [v3 task results](https://developers.tripo3d.ai/en/docs/task-query); [regional task results](https://docs.tripo3d.ai/task-query/get-your-task-result.html) |
| Meshy | `assets.meshy.ai` | [Quickstart and signed model URLs](https://docs.meshy.ai/en/api/quick-start) |
| fal | `fal.media` and its subdomains | [API file examples](https://fal.ai/models/fal-ai/image-editing/professional-photo/api) |
| Sketchfab | `sketchfab-prod-media.s3.amazonaws.com` | [Downloading models](https://sketchfab.com/developers/download-api/downloading-models) |
| OpenRouter | No remote download hosts; inline base64 results continue to work | [Image generation](https://openrouter.ai/docs/guides/overview/multimodal/image-generation) |

Unknown providers and hosts fail closed. A provider that adds a CDN requires an explicit
policy update with a provider reference and tests; do not add shared-storage wildcards
such as `*.amazonaws.com` or a user-configurable bypass.

Every DNS answer must be public. Loopback, private, link-local, multicast, reserved,
documentation, transition/tunnel and known cloud-platform addresses are rejected.
IPv4-mapped IPv6 addresses are checked using the embedded IPv4 address. Mixed
public/private answers fail before connecting. Range references:
[IANA IPv6 address space](https://www.iana.org/assignments/ipv6-address-space/) and
[special-purpose assignments](https://www.iana.org/assignments/iana-ipv6-special-registry/).

The downloader connects to an IP literal from that validated DNS snapshot. The original
host remains the HTTP Host header and TLS/SNI authentication name. Unity's Mono
[`MonoTlsStream`](https://github.com/mono/mono/blob/main/mcs/class/System/Mono.Net.Security/MonoTlsStream.cs)
uses `request.Host` for TLS authentication. Certificate name and chain checks remain
mandatory. The downloader disables proxies, automatic redirects, cookies, credentials
and connection reuse. A retry can use another address only from the same validated snapshot.

Redirects (301, 302, 303, 307, 308) are processed manually, with URL and DNS validation
at every hop, including redirects to the same hostname. At most five redirects are allowed.
Requests have a one-hour total deadline and honor job cancellation. Successful
artifacts stream into owned project-contained temporary files, up to 8 GiB, using
64 KiB buffers and incremental hashes. Only successful responses reach asset import;
cancellation, failure and reload dispose temporary file ownership. Rejected URL
messages omit the URL so signed query parameters do not appear in logs.

Provider API requests also disable automatic redirects. They remain on their existing
UnityWebRequest transport. This policy covers artifact downloads, not remote resources
that an independently installed model importer might choose to fetch.

Tripo uses fixed v3 API routes for file upload, generation and task polling. Uploading a
local image requires `allow_image_upload=true` for that invocation (CLI
`--allow-image-upload`); omission does not grant consent. The original PNG/JPEG bytes,
including embedded metadata, leave the project and may be used for billable generation.
The multipart part uses a constant filename and MIME type, never the source path or name.
Only existing, regular PNG/JPEG files inside `Assets` are accepted, up to 20,000,000 raw
bytes. URI paths, traversal, links, control characters and paths longer than 2,048
characters are refused. Extension and signature checks identify the supported file type;
they do not decode the image, remove metadata or prove that it is a valid image.

The uploader admits length before allocating one bounded multipart buffer, reads the
declared length with cancellation checks, then probes for truncation or growth. It
rechecks the existing safe-path resolver before opening and after reading. These checks
are not an atomic identity lock against hostile concurrent ancestor replacement.
Upload responses are limited to 64 KiB and depth 16; only a successful integer code and
a bounded ASCII token are accepted. Phase errors exclude raw provider bodies, tokens,
paths and credentials. The token stays transient and never becomes a recovery receipt.

After an upload returns, generation is admitted again against the original job deadline,
cancellation, runner epoch and durable submission intent. Cancellation or reload can
leave an uploaded remote file, or an unknown remote submission outcome. There is no
automatic retry, remote cancellation, refund or exactly-once guarantee. A recorded task
receipt resumes by polling v3; it never repeats the upload. Hosted image URLs take
precedence and do not read or upload the local path.

`AssetDownloadTests` exercises the policy and redirect/DNS boundary without provider
credentials. Before release, also run the provider flows in
[the manual verification checklist](asset-gen-manual-verification.md) with a licensed Editor.

## Metadata and local-copy admission

Encoded download limits and path policy remain in force. Before publication and
again before explicit image import, generated PNG/JPEG results pass bounded
metadata inspection. Archive admission inspects every allowed extracted texture,
even if the selected model does not reference it. glTF/GLB metadata and embedded
images pass the same work checks before the installed importer receives them.
These readers inspect headers, framing, offsets, dimensions, precision and
frames/pages/layers without decoding pixels.

| Image work limit | Default | Operator setting |
| --- | ---: | --- |
| Estimated work per image | 2 GiB | `MCPFORUNITY_ASSETGEN_IMAGE_WORK_GIB` |
| Estimated work per operation | 8 GiB | `MCPFORUNITY_ASSETGEN_IMAGE_TOTAL_GIB` |
| Maximum side | 32,768 pixels | Fixed admission limit |
| Frames/pages/layers | 4,096 | Fixed admission limit |

Set the two work variables to integer GiB values from 1 to 1024 in the Editor's
startup environment and restart the Editor. Invalid values retain the defaults;
provider responses and tool arguments cannot raise the limits. Estimates charge
two buffers with at least RGBA8 per pixel and account for higher source precision,
channels, mip levels, faces and layers where applicable. Normal RGBA8 4K, 8K and
16K images fit the per-image default; higher-precision/HDR assets or multiple
large textures may need an operator increase or a smaller/split asset. TIFF work
includes full padded tile grids and volume, sample precision, planar layout and
applicable YCbCr padding; a tiny canvas does not make a large working tile cheap.
Oversized rows-per-strip values are clamped to actual image height.

Metadata support covers PNG/APNG; baseline, extended sequential and progressive
JPEG; WebP with counted animation; BMP; TGA/RLE; Radiance RGBE/XYZE HDR; flat
single-part scanline/tiled EXR including mip/ripmap work; classic TIFF/BigTIFF
including bounded SubIFDs/pages; KTX1/KTX2; DDS; and self-contained Basis.
Generated WebP output remains unsupported. HDR requires Y-major orientation
(`+/-Y` followed by `+/-X`). Deep/multipart EXR, hierarchical/lossless/multi-picture
JPEG, BMP embedded JPEG/PNG compression, unknown header/pixel formats and Basis
external global codebooks are explicitly refused. Export a supported,
self-contained format when admission refuses a variant.

Admission does not certify compressed-pixel codec validity or guarantee peak
memory inside Unity or an optional importer. Opaque FBX embedded media is not
parsed by this estimator. Native import can still allocate memory or perform
synchronous work beyond these estimates.

### JSON metadata

All six provider adapters, both remote model catalogs, local glTF and Tripo GLB
apply structural admission before materializing a complete `JToken` graph.
Existing encoded-byte limits remain separate, including the 64 MiB model JSON
ceiling. JSON must contain one object; duplicate properties and unsupported
non-JSON syntax are rejected.

| Profile | Tokens | Total properties | Items per container | Depth |
| --- | ---: | ---: | ---: | ---: |
| Provider response | 262,144 | 65,536 | 65,536 | 64 |
| Model metadata | 2,000,000 | 1,000,000 | 1,000,000 | 64 |

Tripo's narrower response depth of 16 remains effective. Operators may set
`MCPFORUNITY_ASSETGEN_MODEL_JSON_TOKENS` to 10,000 through 16,000,000 before
starting the Editor. The model property and per-container item ceilings each
become half that token value; depth remains 64. Invalid values retain defaults.
This only provisions model metadata, not provider response authority or arbitrary
input bytes. Admitted model metadata still materializes in memory.

### Local model copies and recovery

Local FBX/ZIP copies enforce the 8 GiB artifact limit and use the same per-project,
per-Editor-process transient disk ledger and free-space headroom as downloads.
OBJ/glTF bundles reserve planned copy bytes in that ledger. Defaults are 32 GiB
of aggregate transient reservations and 1 GiB of disk headroom; set
`MCPFORUNITY_ASSETGEN_TRANSIENT_BUDGET_GIB` and
`MCPFORUNITY_ASSETGEN_DISK_HEADROOM_GIB` to integer GiB values from 1 to 1024 in
the Editor startup environment to provision a larger workload. These reservations
are not disk preallocation or coordination across separate Editor processes.

Copies stream into newly created owned files, check cancellation and actual
length, and verify planned metadata/texture hashes. Contained glTF resource paths
may normalize harmless dot/empty segments such as `./model.bin`; literal/encoded
roots, parent traversal and remote resources remain refused. Failed owned files
are removed; reservation credit is retained when cleanup cannot remove them.
User source files and pre-existing files are preserved. Completed retained
imports become ordinary project files, release transient credit, and continue to
consume filesystem free space. A crash can leave owned staging files: inspect
and recover them explicitly instead of deleting unknown files automatically.
The ledger cannot prevent another program from consuming disk concurrently.
