"""The drift check names a changed field in its action."""

import sys

from tools import generate_declared_action_contracts as generator


def test_check_refuses_stale_native_replan_alias(tmp_path, monkeypatch, capsys):
    target = tmp_path / generator.TARGET.name
    target.write_text(
        generator.render().replace('"previous_checkpoint"', '"previous_operation_id"'),
        encoding="utf-8",
    )
    monkeypatch.setattr(generator, "TARGET", target)
    monkeypatch.setattr(sys, "argv", ["generate_declared_action_contracts.py", "--check"])

    assert generator.main() == 1
    output = capsys.readouterr().out
    assert '"replan_prefab"' in output
    assert '"previous_operation_id"' in output
    assert '"previous_checkpoint"' in output


def test_check_refuses_stale_native_move_field(tmp_path, monkeypatch, capsys):
    target = tmp_path / generator.TARGET.name
    target.write_text(
        generator.render().replace(
            'AssetDestination = "destination"', 'AssetDestination = "target"'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(generator, "TARGET", target)
    monkeypatch.setattr(sys, "argv", ["generate_declared_action_contracts.py", "--check"])

    assert generator.main() == 1
    output = capsys.readouterr().out
    assert 'AssetDestination = "target"' in output
    assert 'AssetDestination = "destination"' in output
