"""Register the evidence commands provided by the full server distribution."""

from cli.commands.verification_causal import register_causal_commands
from cli.commands.verification_evidence import verification_evidence

register_causal_commands(verification_evidence)
