"""Register the evidence commands provided by the full server distribution."""

from cli.commands.verification_causal import register_causal_commands
from cli.commands.verification_evidence import verification_evidence
from cli.commands.verification_reuse import register_reuse_commands
from cli.commands.verification_test_budgets import budget_assess, budget_plan, mutation

register_causal_commands(verification_evidence)
register_reuse_commands(verification_evidence)
verification_evidence.add_command(budget_plan)
verification_evidence.add_command(budget_assess)
verification_evidence.add_command(mutation)
