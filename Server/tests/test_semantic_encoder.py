"""Offline model, tokenization, deployment failure and private worker protocol regressions."""

import os
from pathlib import Path
import socket
import sys

import pytest

from services.registry import semantic_encoder as encoder
from services.registry import semantic_profile, semantic_worker_client


def test_bundled_weights_rank_unseen_domain_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv(semantic_profile.PROFILE_ENV, str(tmp_path))

    def no_network(*args, **kwargs):
        pytest.fail("Catalog inference opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    docs = ("The train arrives at the station.", "Bake a chocolate cake in the oven.")
    model, scores = encoder.semantic_scores("Faire cuire un gâteau au chocolat.", docs)
    assert model == encoder.MODEL_ID
    assert scores[1] > scores[0]
    assert encoder.semantic_similarities("🔥", docs) == (0, 0)
    assert list(tmp_path.iterdir()) == []


def test_pinned_uncased_wordpiece_preserves_boundaries_and_ignores_unknown_words():
    vocab, _ = encoder._model()
    # Token strings from the upstream tokenizer.json with add_special_tokens=False.
    text = "Café! 콘솔에서 오류. [CLS]अन्दर 深度 coöperateƛ"
    expected = [
        "cafe",
        "!",
        "ᄏ",
        "##ᅩᆫ",
        "##소",
        "##ᆯ에",
        "##서",
        "ᄋ",
        "##ᅩ",
        "##류",
        ".",
        "अ",
        "##नद",
        "##र",
        "深",
        "度",
    ]
    actual = [
        offset for word in encoder._words(text) for offset in encoder._piece_offsets(word, vocab)
    ]
    assert actual == [vocab[token] for token in expected]


def test_damaged_ready_profile_falls_back_without_repairing_it(tmp_path, monkeypatch):
    monkeypatch.setenv(semantic_profile.PROFILE_ENV, str(tmp_path))
    # A published marker and interpreter path exist, but all weight files are missing.
    (tmp_path / semantic_profile.READY_FILE).write_bytes(semantic_profile.READY_BYTES)
    interpreter = semantic_profile.runtime_python(tmp_path)
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    model, scores = encoder.semantic_scores("Find cats", ("domestic cats", "open the windows"))
    assert model == encoder.MODEL_ID and scores[0] > scores[1]
    assert not any((tmp_path / asset.name).exists() for asset in semantic_profile.MODEL_FILES)


def test_server_without_a_home_directory_can_use_the_bundled_model(monkeypatch):
    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(semantic_profile, "profile_dir", no_home)
    model, scores = encoder.semantic_scores("Find trains", ("railway train", "bake bread"))
    assert model == encoder.MODEL_ID and scores[0] > scores[1]


def test_binary_worker_keeps_one_catalog_and_preserves_score_order(tmp_path, monkeypatch):
    worker_script = tmp_path / "echo_worker.py"
    worker_script.write_text(
        "import json, struct, sys\n"
        "count = 0\n"
        "for line in sys.stdin.buffer:\n"
        "    req = json.loads(line)\n"
        "    if 'documents' in req:\n"
        "        count = len(req['documents'])\n"
        "    scores = [float(i) for i in range(count)]\n"
        "    sys.stdout.buffer.write(struct.pack(f'<I{count}f', count, *scores))\n"
        "    sys.stdout.buffer.flush()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(semantic_worker_client, "_WORKER_PATH", worker_script)
    monkeypatch.setattr(semantic_profile, "verify_weights", lambda profile: None)
    monkeypatch.setattr(semantic_profile, "runtime_python", lambda profile: Path(sys.executable))
    worker = semantic_worker_client.LocalWorker(tmp_path)
    try:
        assert worker.query("first", ("b", "a")) == (0.0, 1.0)
        assert worker.query("second", ("b", "a")) == (0.0, 1.0)
        assert worker.query("third", ("new",)) == (0.0,)
    finally:
        worker.stop()
    assert worker.process.poll() is not None


@pytest.mark.skipif(
    not os.environ.get("UNITY_MCP_TEST_LABSE_PROFILE"), reason="requires a provisioned CPU model"
)
def test_provisioned_profile_runs_real_offline_inference(monkeypatch):
    monkeypatch.setenv(semantic_profile.PROFILE_ENV, os.environ["UNITY_MCP_TEST_LABSE_PROFILE"])
    model, scores = encoder.semantic_scores(
        "¿A qué hora pasa el tren?", ("What time does the train leave?", "Bring an umbrella.")
    )
    assert model == semantic_profile.MODEL_ID
    assert scores[0] > scores[1]
