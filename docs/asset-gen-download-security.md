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
