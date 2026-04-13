"""
Parity test: assert src/tools.py TOOL_NAMES matches the tools registered
in src/qbo_mcp_server.py — no live QBO calls required.

Uses AST parsing to extract tool names from the MCP server so this test
never imports the mcp package (which requires Python 3.10+ and a venv).
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


def test_tool_names_match_mcp_server() -> None:
    """Every tool in qbo_mcp_server.py must appear in tools.TOOL_NAMES and vice versa."""
    mcp_names = _extract_mcp_tool_names()

    only_in_mcp = mcp_names - TOOL_NAMES
    only_in_tools = TOOL_NAMES - mcp_names

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
    """Expect exactly 13 tools: 10 read-only + 3 bill payment write tools."""
    from tools import TOOLS

    assert len(TOOLS) == 13, f"Expected 13 tools, got {len(TOOLS)}"
