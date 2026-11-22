# Bundled catalog search model

The server performs local CPU inference with a derivative of the Sentence
Transformers multilingual static similarity model. The wheel and source archive
include the vocabulary and learned weights. A normal installation can search
offline without downloading a model, starting a service, or selecting a language.
[Upstream model card](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1/blob/b68f4122911bcffcd6e1f695f2d99cd6788972d8/README.md)

The upstream model is licensed under Apache License 2.0. A copy of the license
accompanies the derived weights. The Unity MCP project truncated each of the
105,879 learned token embeddings from 1,024 to its first 256 Matryoshka dimensions
and quantized each row to signed 8-bit integers with a little-endian float32
scale. All vocabulary entries retain their original token IDs. This conversion
uses no task examples or language-specific search terms. [License](LICENSE.txt)

| File | SHA-256 |
| --- | --- |
| [Upstream weights](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1/resolve/b68f4122911bcffcd6e1f695f2d99cd6788972d8/0_StaticEmbedding/model.safetensors) | `8245ab78ee71dded845a82d2270fcb9e785b29dad0e1619f69d5390c47d9ba00` |
| [Upstream tokenizer](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1/resolve/b68f4122911bcffcd6e1f695f2d99cd6788972d8/0_StaticEmbedding/tokenizer.json) | `11aaf894a4ccf3d95e8830e27c0f8152791fbbff2b988e29a265580b86edd216` |
| Bundled `static-multilingual-v1.q8` (28,400,434 bytes) | `f2d9069c23e858304ee4aa95be7b5f8b441d8e660e6a052c3d13ac4ea3faeec2` |

To reproduce the conversion, save the two upstream files in one directory
under their original filenames. From the repository root, run
`python tools/build_catalog_encoder.py <source-directory>`. The script verifies
both source digests before reading their contents. Python's standard library is
sufficient for conversion and inference.

Search uses the pinned tokenizer's uncased, accent-stripping BERT WordPiece
configuration, ignores unknown words and special tokens, pools token vectors,
and compares L2-normalized vectors using cosine similarity. For the closest 32
descriptions, the server also compares the best match of each query word to a
description word and vice versa, up to 48 words on each side. Both scores are
insensitive to word order. Search scores measure textual similarity and vary
with the language and wording. Tool availability and action permissions are
determined separately.

## Optional LaBSE profile

`unity-mcp search-model install` provisions an isolated local CPU runtime and
the immutable, checksum-verified LaBSE model files. Search automatically uses a
verified, functioning installation. Each result records the model that supplied
its scores. `unity-mcp search-model status --verify` verifies the installation.
The server can run on a different supported CPython version than the isolated
worker. The model files total 487,522,396 bytes, in addition to the worker
environment. The quantized Transformer is combined with the original trained
dense layer, tanh activation and L2 normalization. Both the query and the current
eligible English declarations are embedded on the local CPU.
[Quantized Transformer and tokenizer](https://huggingface.co/Xenova/LaBSE/tree/147002d501fc76ecd313c631999ea796520fea74)
[Apache-2.0 upstream model and dense layer](https://huggingface.co/sentence-transformers/LaBSE/tree/836121a0533e5664b21c7aacc5d22951f2b8b25b)

`labse-catalog.f16` contains float16 embeddings for public English descriptions
in the built-in core and full catalogs. Rows are keyed by SHA-256 of the exact
UTF-8 description. Callers still select eligible declarations from their current
catalog. New or edited descriptions are encoded in the worker and retained only
in memory. User queries are never added to the index.

From the repository root, run the following commands with the server's locked
Python environment and a provisioned LaBSE profile.

```sh
python tools/build_catalog_search_index.py
python tools/build_catalog_search_index.py --check
```

On an intentional regeneration, update `_INDEX_SHA256` in
`semantic_labse_worker.py` to the generator's verified digest. An index with a
different digest is ignored and the worker computes fresh local vectors.
`--texts-only <path.json>` exports just the registered English descriptions
without loading the optional model.
