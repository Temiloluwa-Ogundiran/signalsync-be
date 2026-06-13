"""
LangGraph ReAct agent compiled ONCE at startup.

account_ids and context are injected per-turn via RunnableConfig.configurable,
not baked into the graph. This avoids recompiling the graph on every request —
a prototype bug that added ~200ms per call (§9.1 fix).
"""
import logging
from typing import List

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


def _ids_block(account_ids: List[str]) -> str:
    lines = "\n".join(f"  - {aid}" for aid in account_ids)
    if len(account_ids) == 1:
        return (
            f"This trader's account IDs:\n{lines}\n\n"
            f"This conversation is scoped to the single account above. "
            f"Always pass that account ID when calling tools. Do not broaden to other accounts."
        )
    return (
        f"This trader's account IDs:\n{lines}\n\n"
        f"Pass ALL of these when calling tools unless the trader explicitly names a specific account."
    )


def _copilot_node(state: MessagesState, config: RunnableConfig):
    cfg = config.get("configurable", {})
    account_ids: List[str] = cfg.get("account_ids", [])
    context_block: str = cfg.get("context_block", "")
    system_content = f"{SYSTEM_PROMPT}\n{_ids_block(account_ids)}"
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
