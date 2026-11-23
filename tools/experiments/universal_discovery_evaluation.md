# Multilingual Unity discovery evaluation

The catalog ranks a next Unity operation or readable resource. Each evaluation submits the same original requests to the core and full catalogs.

## Bundled-only search

The runtime at `b660777b3584696effdb7913137b675f4e83ffd3` combines the two encoders already shipped in the server package. The comparison used the previous bundled runtime at `9d3216d30226b84ee2a2e93f7a77d78eab4e0f36`. The MCP client submitted original queries without caller hints. Counts include queries whose expected operation was unavailable. A wrong effect is a suggested operation with a prespecified adverse effect.

| Corpus | Catalog | Available | First, previous → current | Within three, previous → current | Wrong first, previous → current | Wrong within three, previous → current |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| v1 + v2 | core | 108/120 | 48 → 61 | 63 → 69 | 2 → 1 | 17 → 12 |
| v1 + v2 | full | 120/120 | 57 → 70 | 75 → 91 | 3 → 2 | 29 → 19 |
| v3 | core | 40/40 | 9 → 14 | 21 → 24 | 6 → 7 | 15 → 16 |
| v3 | full | 40/40 | 11 → 15 | 24 → 26 | 5 → 6 | 13 → 14 |

An independent model author wrote five requests per language in Hindi, Indonesian, Swahili, Italian, Polish, Swedish, Greek and Vietnamese. The fixture was sealed before ranker changes. The first candidate replay used the frozen shipping runtime, and the original prompts have not received native-speaker review. The full catalog placed the intended action within three more often, but also placed an adverse action first on one additional request. All replays preserved the advertised catalog and recorded zero business calls or native dispatches. [v3 corpus, SHA-256 `e78c2897240a0b33735471c33760f58528ca4d0b9fcf903f4d01d5cd492934ff`](universal_discovery_queries_v3.json)

Across the separate Korean, Japanese, Spanish, Arabic and Turkish menu cases, first-place results increased from 56/60 to 60/60. These are ten distinct prompts across two catalogs and three search surfaces. Tool and combined searches expected `list_menu_declarations`. Resource searches expected `mcpforunity://menu-items`. Tools-only searches placed the lookup tool first in 20/20 cases in both versions. [Menu acceptance test](../tests/test_menu_discovery_probe.py)

CI checks the measured v1 and v2 rank and availability floors for each language and profile, wrong-effect ceilings and dispatch invariants. The independent absolute targets remain fixed. On this runtime, only v1 full met all absolute targets. v3 missed those targets in both profiles, including the maximum of five wrong first choices. [Bundled regression reference](universal_discovery_bundled_reference.json) · [CI regression test](../tests/test_universal_discovery_evaluation.py)

To replay v3 from this checkout in a locked Server environment, run the following PowerShell commands. The runner writes per-request results and returns exit code 1 if a profile misses an absolute target.

```powershell
$env:PYTHONPATH = (Resolve-Path 'Server/src').Path
$studyPython = 'Server/.venv/Scripts/python.exe'
$studyRunner = 'tools/experiments/universal_discovery_evaluation.py'
$studyFixture = 'tools/experiments/universal_discovery_queries_v3.json'
$wordPiece = 'sentence-transformers/static-similarity-mrl-multilingual-v1@b68f4122911bcffcd6e1f695f2d99cd6788972d8/256d-q8'
$unigram = 'minishlab/potion-multilingual-128M@6fea87d68483744bdb34f8ddab7f5c31a94dc0b8/256d-q2'
& $studyPython $studyRunner --fixture $studyFixture --preregistered --expect-model $wordPiece --expect-model $unigram --expect-model "$wordPiece+$unigram" --output "$env:TEMP/unity-discovery-v3.json"
```

The tested wheel had SHA-256 `8d8ccd6bf58fdaf1bbc48b9d36f2ca471b69c062440bfd37deffecfe5ac9eac1`. A fresh installation verified both packaged weight files with `search-model status --verify`. CLI `tool search`, MCP `search_unity_tools` and `manage_tools` search all returned the expected menu tool with external network access and child processes blocked. No external connection or process launch was attempted. Initial installation can fetch the server, Python and its standard package dependencies.

## Earlier evaluations

The first two corpora were authored independently. v1 was sealed before edits to the production ranker. v2 was sealed before the interim v1 aggregate rank counts were shared for further tuning. Both use Chinese (`zh`), Vietnamese (`vi`), Portuguese (`pt`), German (`de`), Finnish (`fi`), and Thai (`th`).

| Corpus | Requests | Business contracts | SHA-256 |
| --- | ---: | ---: | --- |
| [v1](universal_discovery_queries.json) | 72 | 12 | `645d3fcd1924820fd62fa9320bfde872c8a6af10793f974b4cbd70f93fa8d841` |
| [v2](universal_discovery_queries_v2.json) | 48 | 8 | `8c96401f5fb62cf3a385ec0e0b1683a53dc29187958e67f857fe308e49c9d797` |

The real MCP client submitted `search_unity_tools` with the original `query` and `limit=3`. Scoring used each returned group's representative. Available alternatives have a separate measure in the JSON reports. An independent MCP inventory identified advertised bindings, including actions behind permissive legacy selectors. The denominator includes targets unavailable in the current profile. An indicative wrong-effect count records whether a representative matched a prespecified adverse operation at the stated rank. Every replay separately checked that catalog visibility stayed unchanged and recorded attempts to call business tools, read live resources, or dispatch native commands.

The lexical baseline used `bd12b2195400d7718b90cdf74d1ddca0518d2fad`. The interim LaBSE-only run used `a0ad6c76283a9f4c476f03621968217874b35ac4`, with v1 measured before the second corpus was available. Bundled and LaBSE+BGE runs used the final frozen runtime `46f9f64b5845ff50a3117b0808d30108943c296b`. Each replay imported its archived server source and ran with the locked Python environment.

## Raw catalog ranking

The fixed preregistered gate requires at least 60% top-1, 80% top-3, 40% worst-language top-1, 55% worst-language top-3, and at most five indicative wrong first choices per profile. It also requires hybrid matching, complete model receipts, stable inventory, and zero dispatch. These targets apply to each corpus and profile. The gate remains available as `--preregistered`.

| Corpus | Retrieval | Profile | Advertised | Top 1 | Top 3 | Worst language top 1 / 3 | Wrong effect top 1 / 3 | Empty | Gate |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |
| v1 | Lexical baseline | core | 66/72 | 4/72 | 8/72 | 0.0% / 0.0% | 2 / 4 | 37 | Comparison |
| v1 | Lexical baseline | full | 72/72 | 5/72 | 6/72 | 0.0% / 0.0% | 1 / 2 | 37 | Comparison |
| v1 | Bundled | core | 66/72 | 31/72 | 40/72 | 33.3% / 41.7% | 1 / 8 | 0 | Below target |
| v1 | Bundled | full | 72/72 | 39/72 | 52/72 | 41.7% / 50.0% | 2 / 17 | 0 | Below target |
| v1 | LaBSE only | core | 66/72 | 38/72 | 43/72 | 41.7% / 50.0% | 0 / 18 | 0 | Below target |
| v1 | LaBSE only | full | 72/72 | 42/72 | 54/72 | 41.7% / 66.7% | 0 / 18 | 0 | Below target |
| v1 | LaBSE+BGE | core | 66/72 | 44/72 | 50/72 | 41.7% / 58.3% | 1 / 25 | 0 | Below target |
| v1 | LaBSE+BGE | full | 72/72 | 52/72 | 63/72 | 66.7% / 75.0% | 2 / 32 | 0 | Pass |
| v2 | Lexical baseline | core | 42/48 | 2/48 | 2/48 | 0.0% / 0.0% | 0 / 2 | 24 | Comparison |
| v2 | Lexical baseline | full | 48/48 | 2/48 | 2/48 | 0.0% / 0.0% | 0 / 3 | 23 | Comparison |
| v2 | Bundled | core | 42/48 | 17/48 | 23/48 | 25.0% / 37.5% | 1 / 10 | 0 | Below target |
| v2 | Bundled | full | 48/48 | 18/48 | 23/48 | 25.0% / 25.0% | 1 / 12 | 0 | Below target |
| v2 | LaBSE+BGE | core | 42/48 | 29/48 | 38/48 | 37.5% / 75.0% | 3 / 16 | 0 | Below target |
| v2 | LaBSE+BGE | full | 48/48 | 31/48 | 39/48 | 37.5% / 62.5% | 2 / 15 | 0 | Below target |

Both bundled commands exited 1 and reproduced the archived `0572b3e6f` reference reports byte for byte. Primary static / Potion receipt counts were v1 core 60 / 12, v1 full 60 / 12, v2 core 41 / 7, v2 full 41 / 7. All final optional-model requests recorded the pinned LaBSE+BGE receipt.

LaBSE+BGE v1 full passed every preregistered criterion. v1 core missed aggregate top-3. v2 core missed aggregate top-3 and worst-language top-1. v2 full missed worst-language top-1. Both optional-model corpus commands exited 1. Every raw replay preserved catalog visibility and recorded zero business calls or native dispatch attempts.

### Counts by language

Each cell is correct first / correct within three / indicative wrong first. The denominators are twelve per language in v1 and eight per language in v2.

#### v1

| Retrieval | Profile | zh | vi | pt | de | fi | th |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Lexical baseline | core | 1 / 1 / 0 | 0 / 1 / 1 | 2 / 5 / 1 | 1 / 1 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Lexical baseline | full | 1 / 1 / 0 | 0 / 1 / 1 | 3 / 3 / 0 | 1 / 1 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Bundled | core | 5 / 8 / 1 | 4 / 6 / 0 | 6 / 7 / 0 | 5 / 6 / 0 | 5 / 5 / 0 | 6 / 8 / 0 |
| Bundled | full | 7 / 11 / 1 | 5 / 7 / 0 | 8 / 11 / 1 | 8 / 10 / 0 | 6 / 6 / 0 | 5 / 7 / 0 |
| LaBSE only | core | 6 / 8 / 0 | 6 / 7 / 0 | 6 / 6 / 0 | 8 / 8 / 0 | 7 / 7 / 0 | 5 / 7 / 0 |
| LaBSE only | full | 5 / 9 / 0 | 6 / 8 / 0 | 8 / 9 / 0 | 9 / 11 / 0 | 9 / 9 / 0 | 5 / 8 / 0 |
| LaBSE+BGE | core | 10 / 10 / 0 | 7 / 8 / 0 | 7 / 7 / 0 | 8 / 9 / 1 | 5 / 7 / 0 | 7 / 9 / 0 |
| LaBSE+BGE | full | 10 / 11 / 0 | 9 / 11 / 0 | 9 / 11 / 0 | 8 / 10 / 1 | 8 / 9 / 1 | 8 / 11 / 0 |
| Caller hints | core | 6 / 6 / 0 | 6 / 6 / 0 | 6 / 6 / 0 | 6 / 6 / 0 | 6 / 6 / 0 | 6 / 6 / 0 |
| Caller hints | full | 8 / 8 / 0 | 8 / 8 / 0 | 8 / 8 / 0 | 8 / 8 / 0 | 8 / 8 / 0 | 8 / 8 / 0 |

#### v2

| Retrieval | Profile | zh | vi | pt | de | fi | th |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Lexical baseline | core | 0 / 0 / 0 | 0 / 0 / 0 | 2 / 2 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Lexical baseline | full | 0 / 0 / 0 | 0 / 0 / 0 | 2 / 2 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Bundled | core | 3 / 5 / 0 | 2 / 3 / 1 | 4 / 5 / 0 | 3 / 4 / 0 | 3 / 3 / 0 | 2 / 3 / 0 |
| Bundled | full | 4 / 5 / 0 | 2 / 2 / 1 | 4 / 6 / 0 | 3 / 4 / 0 | 3 / 3 / 0 | 2 / 3 / 0 |
| LaBSE+BGE | core | 6 / 6 / 0 | 3 / 6 / 2 | 4 / 7 / 0 | 6 / 6 / 0 | 5 / 6 / 1 | 5 / 7 / 0 |
| LaBSE+BGE | full | 6 / 6 / 0 | 3 / 7 / 1 | 6 / 8 / 0 | 6 / 7 / 0 | 4 / 5 / 0 | 6 / 6 / 1 |
| Caller hints | core | 3 / 3 / 0 | 3 / 3 / 0 | 3 / 3 / 0 | 3 / 3 / 0 | 3 / 3 / 0 | 3 / 3 / 0 |
| Caller hints | full | 4 / 4 / 0 | 4 / 4 / 0 | 4 / 4 / 0 | 4 / 4 / 0 | 4 / 4 / 0 | 4 / 4 / 0 |

### Exploratory packaged CLI query

A separate full-profile, tools-only check used the Thai request `ขอเส้นทางเมนูสำหรับหน้าต่าง Frame Debugger ของ Unity Editor`. The bundled encoders placed `list_menu_declarations` outside the top three. LaBSE+BGE placed it first. This was a new query after the two corpora had been frozen.

## Blind caller hints

One `gpt-6.2-sol` caller at medium reasoning effort saw a single shuffled batch of 120 unique opaque IDs and the original requests. It received the public `search_unity_tools` schema. The batch supplied no catalog contents, expected answers, or case labels. The caller proposed `catalog_query`, `intent`, and any optional observation constraints without executing tools. The replay verified exact ID coverage and original-query equality before forwarding the proposals. The normalized plan contains only the resulting hints for each published corpus key and the source digests. [Normalized caller hints](universal_discovery_assisted_hints.json)

| Corpus | Profile | Top 1 | Top 3 | Worst language top 1 / 3 | Wrong effect top 1 / 3 | Observation / lexical |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| v1 | core | 36/72 | 36/72 | 50.0% / 50.0% | 0 / 0 | 42 / 30 |
| v1 | full | 48/72 | 48/72 | 66.7% / 66.7% | 0 / 0 | 42 / 30 |
| v2 | core | 18/48 | 18/48 | 37.5% / 37.5% | 0 / 6 | 30 / 18 |
| v2 | full | 24/48 | 24/48 | 50.0% / 50.0% | 0 / 12 | 30 / 18 |

Each assisted replay used the final frozen source. All replies reported observation or lexical matching. The original text and the caller's constraints entered the same public MCP endpoint. Inventory stayed stable and both dispatch counters remained zero. Public normalized replays matched every scored field in the private replay.

The live-editor study used a read-only subset of v1. Eight of twelve actors chose a suitable first business tool on `a0ad6c762`, and eleven answered as requested on manual review. On the final `46f9f64b5` runtime, nine of twelve fresh actors chose a suitable first business tool. All twelve obtained matching live evidence and answered as requested on manual review. The strict scorer accepted eleven. Five actors searched with structured observation constraints. [Native actor study](universal_discovery_actors.md#replay-on-the-final-runtime)

## Reproduce

Use the checkout's locked Server Python and a fresh scratch directory. The PowerShell example imports a frozen source archive while reusing the current evaluation runner and fixtures. Choose a fresh scratch directory for each source commit.

```powershell
$discoveryRoot = Join-Path $PWD '.work/universal-discovery-replay'
New-Item -ItemType Directory -Path $discoveryRoot
git archive --format=zip --output "$discoveryRoot/server-src.zip" 46f9f64b5845ff50a3117b0808d30108943c296b Server/src
Expand-Archive -LiteralPath "$discoveryRoot/server-src.zip" -DestinationPath "$discoveryRoot/source"
$env:PYTHONPATH = "$discoveryRoot/source/Server/src"
$discoveryPython = 'Server/.venv/Scripts/python.exe'
$discoveryRunner = 'tools/experiments/universal_discovery_evaluation.py'
$discoveryV1 = 'tools/experiments/universal_discovery_queries.json'
$discoveryV2 = 'tools/experiments/universal_discovery_queries_v2.json'
```

For the bundled replay, point the model directory at a fresh unprovisioned location. Both IDs are allowed and the report counts how many requests each handled.

```powershell
$env:UNITY_MCP_SEARCH_MODEL_DIR = "$discoveryRoot/unprovisioned"
$discoveryPrimary = 'sentence-transformers/static-similarity-mrl-multilingual-v1@b68f4122911bcffcd6e1f695f2d99cd6788972d8/256d-q8'
$discoveryCoverage = 'minishlab/potion-multilingual-128M@6fea87d68483744bdb34f8ddab7f5c31a94dc0b8/256d-q2'
& $discoveryPython $discoveryRunner --fixture $discoveryV1 --preregistered --expect-model $discoveryPrimary --expect-model $discoveryCoverage --output "$discoveryRoot/bundled-v1.json"
& $discoveryPython $discoveryRunner --fixture $discoveryV2 --preregistered --expect-model $discoveryPrimary --expect-model $discoveryCoverage --output "$discoveryRoot/bundled-v2.json"
```

The historical combined replay used LaBSE+BGE on source `46f9f64b5`. Point the archived runtime at the previously provisioned directory and run its CLI through the archive's `PYTHONPATH`. Its status response reports `encoder-reranker` in `quality_profile`. [Archived setup and diagnostics](https://github.com/ykh09242/unity-mcp/blob/46f9f64b5845ff50a3117b0808d30108943c296b/Server/src/cli/CLI_USAGE_GUIDE.md#optional-local-search-model)

```powershell
$env:UNITY_MCP_SEARCH_MODEL_DIR = 'C:/path/to/verified-encoder-reranker-profile'
& $discoveryPython -m cli.main --format json search-model status --verify
$discoveryReranked = 'labse-onnx-int8:147002d501fc76ecd313c631999ea796520fea74:836121a0533e5664b21c7aacc5d22951f2b8b25b+bge-reranker-v2-m3-onnx-int8:6f5ff65298512715a1e669753bc754d2bc8f367b'
& $discoveryPython $discoveryRunner --fixture $discoveryV1 --preregistered --expect-model $discoveryReranked --output "$discoveryRoot/reranked-v1.json"
& $discoveryPython $discoveryRunner --fixture $discoveryV2 --preregistered --expect-model $discoveryReranked --output "$discoveryRoot/reranked-v2.json"
```

For the earlier lexical comparison, archive `bd12b2195400d7718b90cdf74d1ddca0518d2fad` and run each fixture in report mode by omitting `--preregistered` and `--expect-model`. For the interim v1 LaBSE-only score, archive `a0ad6c76283a9f4c476f03621968217874b35ac4`, select an encoder-only profile, and expect `labse-onnx-int8:147002d501fc76ecd313c631999ea796520fea74:836121a0533e5664b21c7aacc5d22951f2b8b25b`.

For the assisted replay, use the final archive and the normalized plan. The plan is keyed by fixture digest, language, and task. Inspect that file to audit the exact forwarded hints. The original `query` comes from the fixture. The recorded response file digest is `f370b5cfbcaf8192241b0a14223be3afb196a9df3e6c94977efe0347c4d5d428`. A rerun can use the published normalized plan and inspect each ranked result without retrieving private actor logs.

```powershell
$discoveryHints = 'tools/experiments/universal_discovery_assisted_hints.json'
& $discoveryPython $discoveryRunner --fixture $discoveryV1 --assisted-hints $discoveryHints --output "$discoveryRoot/assisted-v1.json"
& $discoveryPython $discoveryRunner --fixture $discoveryV2 --assisted-hints $discoveryHints --output "$discoveryRoot/assisted-v2.json"
```

At publication, CI checked both frozen corpora with the bundled encoders. It checked recorded rank and availability counts for each language and profile, effect ceilings, returned model IDs, and the dispatch and inventory invariants. [Archived bundled regression reference](https://github.com/ykh09242/unity-mcp/blob/fa076d6df2c00d0a6d8a40dc82467efa5bee2177/tools/experiments/universal_discovery_bundled_reference.json)

## Receipt digests

| Evidence | SHA-256 |
| --- | --- |
| Lexical baseline v1 | `2197cef29eba4cce09843cda71f103bfb89ee90f1397ab9414360568c716f94a` |
| Lexical baseline v2 | `84b3bfcb6102e640cc3a3b7bed0162ae63afd1f91204c805d71624409e5e51c5` |
| Interim LaBSE only v1 | `e53099930a573658b657af17d01572d84c1ba8a6a8c452a7e438b51bba315719` |
| Final bundled v1 | `f9f4751021a5c92e2d908b8f35f424ec73e35b0db9504d72548c3414cf70759b` |
| Final bundled v2 | `763fa76b097fd762c4aa36ce35a8882359917642b810aee51c9e95c5ddcc9f34` |
| Final LaBSE+BGE v1 | `0a19621d87abfc3799b1b9ee5898e388df155311b41692acbead10d67bec6526` |
| Final LaBSE+BGE v2 | `598e98ec776fa10a873d187c6aea36bf640613d9fb46e017d3699f78045475ec` |
| Private assisted summary | `0163b639dcabf9c815333d0b45617f61a1c337284b024c76cab3dcc8eb6e2147` |
| Public assisted replay v1 | `cfde8dd87c56b4719c27fed33b6f6c19820d8231c894d530bc983707875d7570` |
| Public assisted replay v2 | `b3948e0e1c76e1c9d1a7af1721f11973ebb1d4f93687dbda44bda5d85c6aac13` |
| Published normalized hints | `1cb249745ac8da44deea17555c690109b0222e708afd0a032f196b7e7b71047b` |

## Post-publication replay after CI

CI exposed a regression in an older discovery suite. The corrective runtime `a4b0b0bb487d1ae5b2f182fe039fdef157ca1a2d` revised bundled scoring and shortened the startup guidance. This check replayed all six invocations from that exact Git archive against the already published corpora and caller hints. The preceding tables retain the original independent measurements at `46f9f64b5845ff50a3117b0808d30108943c296b`.

| Retrieval | Corpus | Profile | Advertised | Top 1 / 3 | Lowest top 1 | Lowest top 3 | Wrong effect 1 / 3 | Empty | Absolute gate |
| --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | --- |
| Bundled | v1 | core | 66/72 | 31/72 / 40/72 | 33.3% (vi) | 41.7% (fi) | 1 / 8 | 0 | Missed top1, top3, worst language top1, worst language top3 |
| Bundled | v1 | full | 72/72 | 39/72 / 52/72 | 41.7% (th, vi) | 50.0% (fi) | 2 / 17 | 0 | Missed top1, top3, worst language top3 |
| Bundled | v2 | core | 42/48 | 17/48 / 23/48 | 25.0% (th, vi) | 37.5% (fi, th, vi) | 1 / 9 | 0 | Missed top1, top3, worst language top1, worst language top3 |
| Bundled | v2 | full | 48/48 | 18/48 / 23/48 | 25.0% (th, vi) | 25.0% (vi) | 1 / 12 | 0 | Missed top1, top3, worst language top1, worst language top3 |
| LaBSE+BGE | v1 | core | 66/72 | 44/72 / 50/72 | 41.7% (fi) | 58.3% (fi, pt) | 1 / 25 | 0 | Missed top3 |
| LaBSE+BGE | v1 | full | 72/72 | 52/72 / 63/72 | 66.7% (de, fi, th) | 75.0% (fi) | 2 / 32 | 0 | Pass |
| LaBSE+BGE | v2 | core | 42/48 | 29/48 / 38/48 | 37.5% (vi) | 75.0% (de, fi, vi, zh) | 3 / 16 | 0 | Missed top3, worst language top1 |
| LaBSE+BGE | v2 | full | 48/48 | 31/48 / 39/48 | 37.5% (vi) | 62.5% (fi) | 2 / 15 | 0 | Missed worst language top1 |
| Caller hints | v1 | core | 66/72 | 36/72 / 36/72 | 50.0% (all six) | 50.0% (all six) | 0 / 0 | 6 | Replay only |
| Caller hints | v1 | full | 72/72 | 48/72 / 48/72 | 66.7% (all six) | 66.7% (all six) | 0 / 0 | 0 | Replay only |
| Caller hints | v2 | core | 42/48 | 18/48 / 18/48 | 37.5% (all six) | 37.5% (all six) | 0 / 6 | 6 | Replay only |
| Caller hints | v2 | full | 48/48 | 24/48 / 24/48 | 50.0% (all six) | 50.0% (all six) | 0 / 12 | 0 | Replay only |

The published bundled CI floors for top 1 / top 3 were 31 / 40 and 39 / 52 in v1 core / full, and 17 / 23 and 18 / 23 in v2 core / full. The independent bundled measurement and this replay agreed at each of those floors. All six language-specific availability and rank floors held in all four corpus and profile combinations. Indicative wrong effects in the top three fell from 10 to 9 in bundled v2 core. The other bundled wrong-effect totals held. The reference pytest passed on the replayed archive in 31.86 seconds with the same reference and effect ceilings. [Archived bundled reference](https://github.com/ykh09242/unity-mcp/blob/fa076d6df2c00d0a6d8a40dc82467efa5bee2177/tools/experiments/universal_discovery_bundled_reference.json)

Both bundled result files differ from their published counterparts. Both LaBSE+BGE outputs and both assisted outputs reproduce their published JSON byte for byte. The model receipt counts and identities are the same as in the original report. All four raw corpus commands retained exit 1 under the fixed preregistered gate. At the profile level, LaBSE+BGE v1 full retained its pass. Both assisted invocations exited 0. The six invocations preserved catalog visibility and recorded zero business calls or native dispatch attempts.

To reproduce this follow-up, use `a4b0b0bb487d1ae5b2f182fe039fdef157ca1a2d` as the Git archive ref in the preceding commands. All scoring options and fixture bytes are the same.

| Replay | SHA-256 |
| --- | --- |
| quality-v1 | `0a19621d87abfc3799b1b9ee5898e388df155311b41692acbead10d67bec6526` |
| quality-v2 | `598e98ec776fa10a873d187c6aea36bf640613d9fb46e017d3699f78045475ec` |
| bundled-v1 | `cb6cb23685b8f422fccdac363aa08fed0dc8d9f841afc67ebfc189f4d511902b` |
| bundled-v2 | `53219104f660eb3523398ab1f15b08be66518af90602714739fba111de95bb49` |
| assisted-v1 | `cfde8dd87c56b4719c27fed33b6f6c19820d8231c894d530bc983707875d7570` |
| assisted-v2 | `b3948e0e1c76e1c9d1a7af1721f11973ebb1d4f93687dbda44bda5d85c6aac13` |
| Comparison summary | `55ace864405823cdcc9226251918579338a43620904ba585478b29ed237f3c01` |

## Shipping token-boundary replay

The shipping runtime `9d3216d30226b84ee2a2e93f7a77d78eab4e0f36` adjusts token boundaries around punctuation in literal names. The exact archive was used for the same six replays. All aggregate scores, worst-language results, model receipts, and preregistered outcomes match the after-CI table. In bundled v1, one offered-candidate list changed in each profile and the scored fields held.

Every published bundled language floor held. The archived reference pytest passed both tests in 35.49 seconds. All six invocations preserved catalog visibility with zero business calls and native dispatch attempts. LaBSE+BGE v1 full remains the sole profile-level absolute pass. Both LaBSE+BGE and both assisted outputs also match their original publication byte for byte.

| Replay | Exit | Byte match to `a4b0b0bb` | SHA-256 |
| --- | ---: | --- | --- |
| quality-v1 | 1 | Yes | `0a19621d87abfc3799b1b9ee5898e388df155311b41692acbead10d67bec6526` |
| quality-v2 | 1 | Yes | `598e98ec776fa10a873d187c6aea36bf640613d9fb46e017d3699f78045475ec` |
| bundled-v1 | 1 | Different candidate lists | `7c3d924c21585d2f45446c3a733e3d638c2a34847b0447c8fef3701cc5f803c7` |
| bundled-v2 | 1 | Yes | `53219104f660eb3523398ab1f15b08be66518af90602714739fba111de95bb49` |
| assisted-v1 | 0 | Yes | `cfde8dd87c56b4719c27fed33b6f6c19820d8231c894d530bc983707875d7570` |
| assisted-v2 | 0 | Yes | `b3948e0e1c76e1c9d1a7af1721f11973ebb1d4f93687dbda44bda5d85c6aac13` |

The shipping replay comparison summary has SHA-256 `f181f1dea705e40058f3c2de6f0185ceda72eae174780766ff267b283bb9fdfa`. Use the shipping commit as the archive ref in the reproduction steps.

## Five-language menu lookup on the shipping runtime

A separate public acceptance suite asks for the path to an Editor menu entry in Korean, Japanese, Spanish, Arabic and Turkish. Each language has two original requests. Each request ran against core and full catalogs on three invocation surfaces (tools, resources, and both). That gives twelve observations per language and sixty per model profile. The MCP client sent the original query with a limit of three. A tool or combined search expected `list_menu_declarations`, and a resource search expected `mcpforunity://menu-items`. Each returned representative was scored at its actual rank. [First prompt set](blind_tool_tasks.json) · [Second prompt set](blind_tool_tasks_natural.json) · [Public MCP acceptance test](../tests/test_menu_discovery_probe.py)

| Ranking on the shipping source | Korean | Japanese | Spanish | Arabic | Turkish | Total first | Within top three |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Bundled encoders | 12/12 | 8/12 | 12/12 | 12/12 | 12/12 | 56/60 | 60/60 |
| LaBSE+BGE | 12/12 | 12/12 | 12/12 | 8/12 | 12/12 | 56/60 | 60/60 |

The five language columns count first-place results. Every other result was second. On the tools-only surface, the lookup tool was first in 20/20 bundled searches and 18/20 LaBSE+BGE searches. A separate earlier suite replayed seventeen original menu, Console and camera requests in ninety combinations of catalog and eligible invocation surface. The shipping bundled encoders placed the expected representative first in all ninety checks. [Earlier observation regression](../tests/test_localized_discovery_holdout.py)
