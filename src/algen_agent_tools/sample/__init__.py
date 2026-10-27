"""A dependency-free reference toolkit.

It calls no external service. It exists so an install can be checked end to end (entry point,
plugin, ``describe``) and as the template a new connector copies.
"""

from algen_agent_tools.sample.toolkit import Echo, sample_toolkit

__all__ = ["Echo", "sample_toolkit"]
