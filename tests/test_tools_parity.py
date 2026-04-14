"""
Parity test: assert src/tools.py TOOL_NAMES matches the tools registered
in src/qbo_mcp_server.py — no live QBO calls required.

Uses AST parsing to extract tool names from the MCP server so this test
never imports the mcp package (which requires Python 3.10+ and a venv).

NOTE: The four email invoice tools (scan_emails_for_invoices, get_invoice_queue,
approve_invoice, reject_invoice) are chat-agent-only tools that require Gmail
auth and SQLite state. They are intentionally NOT registered in qbo_mcp_server.py.
"""

import ast
import sys
from pathlib import Path

# Ensure src/ is on the path so tools.py can be imported directly.
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from tools import TOOL_NAMES  # noqa: E402


def _extract_mcp_tool_names() -> set[str]:
    """Parse qbo_mcp_server.py with AST and collect @mcp.tool() function names."""
    server_path = SRC_DIR / "qbo_mcp_server.py"
    tree = ast.parse(server_path.read_text())

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            # Match @mcp.tool() — an Attribute call
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                names.add(node.name)
    return names


# Tools that exist in tools.py but are intentionally absent from the MCP server.
# These require Gmail auth + SQLite state and are chat-agent-only.
_CHAT_ONLY_TOOLS = frozenset({
    "scan_emails_for_invoices",
    "get_invoice_queue",
    "approve_invoice",
    "reject_invoice",
})


def test_tool_names_match_mcp_server() -> None:
    """QBO tools in qbo_mcp_server.py must appear in tools.TOOL_NAMES and vice versa.

    Chat-agent-only tools (email invoice ingestion) are excluded from this check
    because they are intentionally not registered in the MCP server.
    """
    mcp_names = _extract_mcp_tool_names()
    qbo_tool_names = TOOL_NAMES - _CHAT_ONLY_TOOLS

    only_in_mcp = mcp_names - qbo_tool_names
    only_in_tools = qbo_tool_names - mcp_names

    assert not only_in_mcp, (
        f"Tools in MCP server but missing from tools.py: {only_in_mcp}"
    )
    assert not only_in_tools, (
        f"Tools in tools.py but missing from MCP server: {only_in_tools}"
    )


def test_tools_list_has_required_fields() -> None:
    """Every tool definition must have name, description, and input_schema."""
    from tools import TOOLS

    for tool in TOOLS:
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool {tool['name']} missing 'description'"
        assert "input_schema" in tool, f"Tool {tool['name']} missing 'input_schema'"


def test_tool_names_are_unique() -> None:
    """No duplicate tool names."""
    from tools import TOOLS

    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names)), f"Duplicate tool names found: {names}"


def test_total_tool_count() -> None:
    """Expect exactly 17 tools: 10 read-only + 3 bill payment + 4 email invoice tools."""
    from tools import TOOLS

    assert len(TOOLS) == 17, f"Expected 17 tools, got {len(TOOLS)}"
