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

## Shared operation metric

`operation-metric-v2.q8` contains 13,016 bytes of project-trained weights. Its
SHA-256 is `fcb6cd5179fb9b9fb9d87f25e14d78b982aa911512d04fcfcba8ce5381a98eb7`.
The server verifies the file before using it. If it is absent or damaged, the
two encoders can still search the declarations directly.

Each encoder has one pair of rank-12 projections for comparing requests with
declarations. Every visible binding uses the same weights. The declaration
vector pools its selector, purpose, examples if provided, and selector subject.
The request combines its original text with a second view that masks literals.
The projection can recover an additional candidate when the ordinary field
similarity supports it or it clears the catalog's retrieval margin. Among
sibling operations its advantage must also overcome the existing score gap.
Resource-only searches and a single observation exposed by two equivalent
bindings keep their original ranking. The file contains numerical coefficients only.

Training used the default catalog's 223 declarations and the 190 requests with
an advertised target across the published v1, v2, v3 and initial read/edit
corpora. The fitting script pins those four inputs by digest. Language tags and
task names are excluded from the features. Target bindings identify positive
examples during fitting. The two confirmation corpora and the later edit
holdout are excluded from fitting. The wheel already contains the projections
and both encoders. Search and the following rebuild use local files.

Each encoder also has a 256-coefficient read/action classifier with an intercept.
It learns from raw training requests and from the declarations that remain in the
calibration catalog. Each population gives equal mass to reads and actions, and
to the distinct labelled operations within each class. The two classifiers add
528 bytes to the artifact. Inference uses the encoders selected for the request.

When all contributing encoders predict an action, the ranker looks for a read
among its leading results and an advertised action on the same named property
of the leading tool family. A single encoder must also prefer the action's
description. A matching action can overtake a read or enter the alternatives
behind an existing action. Competing writes on the same property keep their
original ranking.

From the repository root, run `capture` with the server environment. Run `fit`
with a maintainer environment containing NumPy (the reference build used
2.5.5). Supply a temporary location for the intermediate vectors. `--check`
compares the regenerated bytes to the packaged artifact.

```sh
python tools/experiments/fit_discovery_metric.py capture training.json.gz
python tools/experiments/fit_discovery_metric.py fit training.json.gz Server/src/services/registry/model/operation-metric-v2.q8 --check
```

## Observation scope

`observation-scope-v1.q8` contains 1,064 bytes. Its SHA-256 is
`88fd6c7d7fb040b7db79224041b98f2533feec054d80654d07594f9b807b9d70`.
Each encoder has two linear contrasts. One estimates whether the original
request seeks an individual state or a collection. The other estimates the
scope of an operation's declared purpose. They are fitted on paired requests
and descriptions about everyday subjects. The training input contains no
Unity routes, language labels, or Unity evaluation queries.
[Calibration texts](../../../../../tools/experiments/observation_scope_pairs.json)

The ranker compares visible reads from the same registered family. It can
recover a narrower read when an inventory mentions the requested property,
or an inventory when the request asks for multiple results. A separate clause
can supply topical evidence for a question about one item in an open set.
Conflicting interpretations across sentences retain the existing rank when
the encoders disagree. The same inference runs for each supported encoder
configuration. A detail read that requires an identifying argument in addition
to the inventory's arguments can enter the alternatives when the incoming
request also supports its declared subject. Missing or damaged scope weights
preserve ordinary retrieval.

Build the snapshot with the server environment and fit it with the existing
NumPy maintainer environment. Both commands use local files.

```sh
python tools/experiments/fit_observation_scope.py capture scope.json.gz
python tools/experiments/fit_observation_scope.py fit scope.json.gz Server/src/services/registry/model/observation-scope-v1.q8 --check
```
