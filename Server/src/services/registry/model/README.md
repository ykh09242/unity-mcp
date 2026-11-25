# Bundled catalog search model

Standalone ranked search performs local CPU inference with a derivative of the Sentence
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
both source digests before reading their contents. Conversion uses Python's
standard library. The server uses its existing Unicode property support to
isolate ideographs during WordPiece tokenization.

Search uses the pinned tokenizer's uncased, accent-stripping BERT WordPiece
configuration and ignores unknown words and special tokens. It combines pooled
WordPiece and Unigram cosine similarities with weights of 60% and 40%. For the
64 nearest passages it compares the closest matching terms in both directions,
using up to 48 terms per text in each encoder. It also checks the strongest
WordPiece alternative over that encoder's own nearest 64 passages. Agreement
widens the lead over alternatives. A stronger independent word match can
resolve a close joint ranking.

If WordPiece recognizes too little of the query, the bundled Unigram model
scores it directly. If either artifact is unavailable, the other can score the
current declarations. The response identifies the model or pair that supplied
the scores. Ranking varies with wording and vocabulary coverage. Tool availability
and permissions are determined separately. [Unigram model and reproduction steps](POTION.md)

Run `unity-mcp search-model status --verify` to check both packaged model files locally.

Agent selection reads the live public catalog and never opens either encoder. The calling agent interprets the original request and compares every advertised operation in its chosen families. The project-fitted operation and observation weights were retired. Their inputs, implementation and historical measurements remain in the [archived evaluation](https://github.com/ykh09242/unity-mcp/blob/46ea3b433b90a001695903391b699f7a5569e965/tools/experiments/universal_discovery_evaluation.md).
