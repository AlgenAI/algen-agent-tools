"""A sample MCP server for the overlay tests: a small ticket desk, run over stdio."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("ticket-desk")
TICKETS: dict[str, str] = {"T-1": "Printer is on fire"}


@server.tool(annotations={"readOnlyHint": True})
def get_ticket(ticket_id: str) -> dict[str, str]:
    """Read one ticket."""
    return {"id": ticket_id, "title": TICKETS[ticket_id]}


@server.tool()
def create_ticket(title: str) -> dict[str, str]:
    """Open a ticket."""
    ticket_id = f"T-{len(TICKETS) + 1}"
    TICKETS[ticket_id] = title
    return {"id": ticket_id, "title": title}


@server.tool(annotations={"destructiveHint": True})
def delete_ticket(ticket_id: str) -> str:
    """Delete a ticket."""
    del TICKETS[ticket_id]
    return f"deleted {ticket_id}"


@server.tool()
def run_shell(command: str) -> str:
    """Never declared in the overlay; it must never be exposed."""
    raise AssertionError("the overlay exposed an undeclared tool")


if __name__ == "__main__":
    server.run("stdio")
