"""Native policy is generated from canonical partitions and reviewed effect rules."""

from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from generate_operation_policy import TARGET, render  # noqa: E402
from services.operation_policy import POLICY_RULES, RULE_INDEX  # noqa: E402
from services.operation_permissions import required_permissions  # noqa: E402


def test_generated_native_policy_is_current():
    # Given / When
    expected = render()
    # Then
    assert TARGET.read_text(encoding="utf-8") == expected


def test_reviewed_rule_keys_are_unique_and_canonical_operations_exist():
    # Given
    count = sum(len(rule.actions) or 1 for rule in POLICY_RULES)
    # When / Then: authorization is checked only as an inventory guard, never as effects authority.
    assert len(RULE_INDEX) == count
    for rule in POLICY_RULES:
        for action in rule.actions or (None,):
            required_permissions(rule.command, {} if action is None else {"action": action})
