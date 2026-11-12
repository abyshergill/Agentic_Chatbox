"""A tiny MCP server over stdio, used by the tests (and handy for trying Connectors): two tools, one read-only."""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("demo", instructions="Demo server for tests.")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def add(a: int, b: int) -> str:
    """Adds two numbers."""
    return str(a + b)


@server.tool()
def shout(text: str) -> str:
    """Returns the text in capitals."""
    return text.upper()


if __name__ == "__main__":
    server.run()
