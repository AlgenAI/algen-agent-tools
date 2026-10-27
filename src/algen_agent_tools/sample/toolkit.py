from __future__ import annotations

from typing import Annotated

from algen_agent_runtime.tools.authoring import Toolkit, read
from pydantic import BaseModel, Field


class Echo(BaseModel):
    text: str
    length: int


def sample_toolkit(name: str = "sample") -> Toolkit:
    """A toolkit with one read tool, ``<name>.echo``."""
    toolkit = Toolkit(name)

    @toolkit.tool(read(timeout=5))
    def echo(text: Annotated[str, Field(min_length=1, max_length=1_000)]) -> Echo:
        """Return the text unchanged with its length. Use it to check that tools are wired up."""
        return Echo(text=text, length=len(text))

    return toolkit
