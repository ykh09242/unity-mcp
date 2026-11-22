# Bundled Unigram coverage model

The server also ships a quantized derivative of MinishLab's `potion-multilingual-128M`.
The upstream model card declares the MIT license and credits the Model2Vec team,
Stephan Tulkens and Thomas van Dongen. [Pinned model card](https://huggingface.co/minishlab/potion-multilingual-128M/blob/6fea87d68483744bdb34f8ddab7f5c31a94dc0b8/README.md)
[MinishLab license](POTION-LICENSE.txt)

The coverage model is loaded on demand when the primary WordPiece tokenizer
recognizes less than 80% of a query's normalized letters and digits, or splits
whole words into individual characters. All languages share the same selector,
Unigram tokenizer and learned weights. All 500,353 source vocabulary entries,
their scores, the precompiled normalization table and all 256 embedding dimensions
are present. The converter represents each weight by a two-bit code and stores
one binary16 RMS scale per row. The encoder pools and L2-normalizes the token
vectors for both the query and each registered document.

| File | SHA-256 |
| --- | --- |
| [Upstream weights](https://huggingface.co/minishlab/potion-multilingual-128M/resolve/6fea87d68483744bdb34f8ddab7f5c31a94dc0b8/model.safetensors) | `14b5eb39cb4ce5666da8ad1f3dc6be4346e9b2d601c073302fa0a31bf7943397` |
| [Upstream tokenizer](https://huggingface.co/minishlab/potion-multilingual-128M/resolve/6fea87d68483744bdb34f8ddab7f5c31a94dc0b8/tokenizer.json) | `19f1909063da3cfe3bd83a782381f040dccea475f4816de11116444a73e1b6a1` |
| Packaged `potion-256.q2` (41,617,999 bytes) | `f74128b8d2e7a18623eb0c1441c4ad478a296ec013eae1ab5861f6c15518c221` |

Rebuild from the verified upstream files with Python's standard library. Add
`--check` to independently regenerate and compare against the existing artifact.

```sh
python tools/build_semantic_coverage.py --weights /path/to/model.safetensors --tokenizer /path/to/tokenizer.json --check
```
