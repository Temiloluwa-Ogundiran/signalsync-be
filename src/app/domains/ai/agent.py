"""
LangGraph ReAct agent compiled ONCE at startup.

account_ids and context are injected per-turn via RunnableConfig.configurable,
not baked into the graph. This avoids recompiling the graph on every request —
a prototype bug that added ~200ms per call (§9.1 fix).
"""
import logging
from typing import Dict, List

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode, tools_condition

from app.core.config import settings
from app.domains.ai.prompts import SYSTEM_PROMPT
from app.domains.ai.tools import ALL_TOOLS

logger = logging.getLogger("synctrades.ai.agent")

_llm_with_tools: ChatOpenAI | None = None
_compiled = None


def _ids_block(account_ids: List[str], account_map: Dict[str, str]) -> str:
    """
    Render the account roster injected into the system prompt.
    Format: "  - <uuid>  →  <label>"
    The label is what the trader sees in the UI and may use in conversation.
    """
    lines = "\n".join(
        f"  - {aid}  →  {account_map.get(aid, 'unknown')}"
        for aid in account_ids
    )
    if len(account_ids) == 1:
        aid = account_ids[0]
        label = account_map.get(aid, "unknown")
        return (
            f"This trader's accounts (UUID → label):\n{lines}\n\n"
            f"This conversation is scoped to '{label}' (ID: {aid}). "
            f"Always pass that UUID when calling tools. "
            f"Always refer to it as '{label}' in your responses — never show the raw UUID to the trader."
        )
    return (
        f"This trader's accounts (UUID → label):\n{lines}\n\n"
        f"Pass ALL UUIDs when calling tools unless the trader names a specific account. "
        f"When the trader refers to an account by label (e.g. 'demo1'), map it to its UUID for tool calls. "
        f"Always use the label, not the UUID, when referring to accounts in your responses."
    )


def _copilot_node(state: MessagesState, config: RunnableConfig):
    cfg = config.get("configurable", {})
    account_ids: List[str] = cfg.get("account_ids", [])
    account_map: Dict[str, str] = cfg.get("account_map", {})
    context_block: str = cfg.get("context_block", "")
    system_content = f"{SYSTEM_PROMPT}\n{_ids_block(account_ids, account_map)}"
    if context_block:
        system_content += f"\n\n{context_block}"
    system = SystemMessage(content=system_content)
    return {"messages": [_llm_with_tools.invoke([system] + state["messages"])]}


def build_compiled(checkpointer=None):
    """Build and compile the graph. Call once at startup after checkpointer is ready."""
    global _llm_with_tools, _compiled

    _llm_with_tools = ChatOpenAI(
        model=settings.AI_MODEL,
        temperature=settings.AI_TEMPERATURE,
        openai_api_key=settings.OPENAI_API_KEY,
    ).bind_tools(ALL_TOOLS)

    g = StateGraph(MessagesState)
    g.add_node("copilot", _copilot_node)
    g.add_node("tools", ToolNode(ALL_TOOLS))
    g.add_edge(START, "copilot")
    g.add_conditional_edges("copilot", tools_condition)
    g.add_edge("tools", "copilot")

    _compiled = g.compile(checkpointer=checkpointer)
    logger.info("LangGraph agent compiled (model=%s, tools=%d)", settings.AI_MODEL, len(ALL_TOOLS))
    return _compiled


def get_compiled():
    if _compiled is None:
        raise RuntimeError("AI agent not initialised — call build_compiled() in app lifespan")
    return _compiled
