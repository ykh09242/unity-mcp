# Reading large shader sources

`manage_shader(action="read")` returns a bounded source page. The MCP and CLI
entry points request text by default. Use `read_offset` and `read_limit` in MCP,
or `--offset` and `--limit` with `unity-mcp shader read`. The default page limit
is 65,536 bytes; an explicit page can contain 4 through 1,048,576 source bytes.
This is a per-page bound, not a maximum shader file size. Use the 1 MiB page
size for bulk traversal to reduce per-request overhead.

```json
{"action":"read","name":"Generated","path":"Assets/Shaders","read_offset":0,"read_limit":65536,"read_encoding":"text"}
```

When `data.hasMore` is true, pass `data.nextOffset` as the next `read_offset`.
Offsets count bytes in the original source encoding **after** its initial BOM.
Do not advance by the number of returned characters or Base64 bytes. Pages end
at complete Unicode code points and can return fewer bytes than the requested
limit. `sourceSizeBytes`, `sourceEncoding`, and `returnedBytes` describe the source.
At EOF, `nextOffset` is null and `hasMore` is false. An offset beyond EOF or in
the middle of a code point is rejected. Invalid encoding is rejected when its
page is read; an unread suffix has not been validated.

UTF-8 (with or without BOM), UTF-16 LE/BE with BOM, and UTF-32 LE/BE with BOM
are supported with strict decoding. A second U+FEFF remains source content.
`read_encoding="base64"` returns UTF-8 Base64 in `encodedContents` and a null
`contents` field, without a duplicate text payload. Text pages use `contents`.

Native callers use camelCase `readOffset`, `readLimit`, and `readEncoding`.
For compatibility, an omitted native encoding retains the legacy extra Base64
field above 10,000 characters, within the same bounded page. New native callers
should explicitly choose `text` or `base64`. Writes and small-source content are
unchanged. Consumers that previously assumed a whole-file read must follow the
continuation metadata for larger files.

```sh
unity-mcp shader read Assets/Shaders/Generated.shader --limit 65536
unity-mcp --format json shader read Assets/Shaders/Generated.shader --offset 65536
```

Human-readable CLI output reports a continuation notice on stderr. JSON output
retains the complete page metadata in one document. Pages are separate reads,
not an immutable snapshot; restart traversal if another tool edits the source.
Save the complete source only after assembling all pages. The reader does not
import the shader, refresh assets, or modify the source.

## Measured tradeoff

A warm isolated Windows Unity 6000.0.69f1 Editor read generated sources of
16,777,216 ASCII bytes and 16,834,560 UTF-8 Unicode bytes. First-read times below
are medians of five calls after one warm-up; traversal is one complete native
pass without retaining previous pages. Transport latency is not included.

| Measurement | ASCII | Unicode |
| --- | ---: | ---: |
| Previous whole-file read, text plus Base64 | 121.05 ms | 322.83 ms |
| First 64 KiB text page | 0.45 ms | 0.83 ms |
| Previous returned UTF-16 string storage | 78,293,680 bytes | 65,093,632 bytes |
| Returned UTF-16 string storage per first page | 131,072 bytes | 78,644 bytes |
| Complete traversal, 64 KiB pages | 158.90 ms / 256 pages | 250.93 ms / 257 pages |
| Complete traversal, 1 MiB pages | 116.15 ms / 16 pages | 170.46 ms / 17 pages |

The memory figures count returned string characters times two; they are not
process peak memory or total allocations. The runtime allocation counter failed
its known-allocation calibration, so no allocation-byte total is claimed.
Small pages add request overhead, especially over a transport; choose larger
pages when the whole source is needed. These measurements justify bounded
inspection and removal of duplicate output, not a universal throughput guarantee.
