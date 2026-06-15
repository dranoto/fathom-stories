# app/mcp_tools.py
import logging
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI
from langchain_core.tools.base import BaseTool

from . import config as app_config

logger = logging.getLogger(__name__)


async def init_mcp_tools(app: FastAPI) -> None:
    app.state.chat_mcp_client = None
    app.state.chat_mcp_server_names: List[str] = []
    app.state.chat_mcp_tools: List[BaseTool] = []

    servers = app_config.CHAT_MCP_SERVERS
    if not servers:
        logger.info("MCP_TOOLS: no CHAT_MCP_SERVERS configured; chat tools disabled.")
        return

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except Exception as e:
        logger.error(f"MCP_TOOLS: langchain-mcp-adapters import failed: {e}", exc_info=True)
        return

    connections: Dict[str, Dict[str, Any]] = {}
    for name, cfg in servers:
        connections[name] = cfg
    try:
        client = MultiServerMCPClient(
            connections=connections,
            tool_name_prefix=True,
            handle_tool_errors=True,
        )
    except Exception as e:
        logger.error(f"MCP_TOOLS: client construction failed: {e}", exc_info=True)
        return

    app.state.chat_mcp_client = client
    app.state.chat_mcp_server_names = [name for name, _ in servers]
    logger.info(
        f"MCP_TOOLS: client ready for servers {app.state.chat_mcp_server_names}"
    )

    try:
        tools = await client.get_tools()
    except Exception as e:
        logger.error(f"MCP_TOOLS: get_tools failed: {e}", exc_info=True)
        return

    app.state.chat_mcp_tools = tools
    names = [getattr(t, "name", "<unnamed>") for t in tools]
    logger.info(f"MCP_TOOLS: loaded {len(tools)} tools: {names}")


async def shutdown_mcp_tools(app: FastAPI) -> None:
    app.state.chat_mcp_tools = []
    app.state.chat_mcp_server_names = []
    app.state.chat_mcp_client = None


def get_chat_tools(app: FastAPI) -> List[BaseTool]:
    return list(getattr(app.state, "chat_mcp_tools", []) or [])
