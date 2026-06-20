"""
Ad-hoc AI copilot eval harness + model A/B.

Runs the LangGraph agent directly against the STAGING DB so we can read real
responses, tweak prompts/tools, and compare models without the HTTP layer.

Usage:
  # single question, default model (gpt-4.1-mini)
  uv run python scripts/ai_eval.py "What's hurting my performance the most?"

  # full canned suite
  uv run python scripts/ai_eval.py --all

  # A/B a model on the suite
  uv run python scripts/ai_eval.py --all --model gpt-5.4-mini

  # compare several models on the suite, write markdown to runs/
  uv run python scripts/ai_eval.py --all --compare gpt-4.1-mini,gpt-5.4-mini,gpt-5.4-nano

OPENAI_API_KEY must be exported before running.
"""
import os
import sys
import time
import asyncio
import uuid
import argparse

# ── Override config BEFORE app imports so settings picks these up ──────────────
# Point at whichever DB holds demo data. Export EVAL_DATABASE_URL (or the normal
# DATABASE_URL) before running — never hardcode credentials in a committed file.
_db_url = os.environ.get("EVAL_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not _db_url:
    sys.exit(
        "Set EVAL_DATABASE_URL (or DATABASE_URL) to a DB with demo data, e.g.\n"
        "  EVAL_DATABASE_URL=postgresql://... OPENAI_API_KEY=sk-... \\\n"
        "    uv run python scripts/ai_eval.py --all --model gpt-5.4-mini"
    )
os.environ["DATABASE_URL"] = _db_url
os.environ.setdefault("SECRET_KEY", "eval-secret")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from langchain_openai import ChatOpenAI  # noqa: E402
from langgraph.graph import START, StateGraph  # noqa: E402
from langgraph.graph.message import MessagesState  # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition  # noqa: E402
from langchain_core.messages import SystemMessage  # noqa: E402
from langchain_core.runnables import RunnableConfig  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.domains.ai.prompts import SYSTEM_PROMPT  # noqa: E402
from app.domains.ai.tools import ALL_TOOLS  # noqa: E402
from app.domains.ai.agent import _ids_block  # noqa: E402

# Demo user with rich data (139 trades on this account, 5 named setups).
ACCOUNT_ID = "82af628b-2e00-4c50-b1ba-78d618c1794c"
ACCOUNT_LABEL = "Demo Main"

CANNED = [
    "What's hurting my performance the most?",
    "Which setup is making me the most money?",
    "Show me my 5 worst trades.",
    "Am I revenge trading or overtrading?",
    "What's my overall profit factor?",
    "What are my best performing trades?",
]

# GPT-5.x reasoning models reject `temperature`; only send it to models that
# accept it (the gpt-4.x / 4o family).
def _supports_temperature(model: str) -> bool:
    return not model.startswith("gpt-5")


def build_for_model(model: str):
    """Compile a fresh graph bound to `model` (no checkpointer)."""
    kwargs = dict(
        model=model,
        openai_api_key=settings.OPENAI_API_KEY,
        stream_usage=True,
    )
    if _supports_temperature(model):
        kwargs["temperature"] = settings.AI_TEMPERATURE
    llm = ChatOpenAI(**kwargs).bind_tools(ALL_TOOLS)

    def copilot_node(state: MessagesState, config: RunnableConfig):
        cfg = config.get("configurable", {})
        sys_content = f"{SYSTEM_PROMPT}\n{_ids_block(cfg.get('account_ids', []), cfg.get('account_map', {}))}"
        return {"messages": [llm.invoke([SystemMessage(content=sys_content)] + state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("copilot", copilot_node)
    g.add_node("tools", ToolNode(ALL_TOOLS))
    g.add_edge(START, "copilot")
    g.add_conditional_edges("copilot", tools_condition)
    g.add_edge("tools", "copilot")
    return g.compile()


async def run_one(compiled, question: str, echo: bool = True):
    session_id = uuid.uuid4()
    config = {
        "configurable": {
            "thread_id": f"eval_{session_id}",
            "account_ids": [ACCOUNT_ID],
            "account_map": {ACCOUNT_ID: ACCOUNT_LABEL},
            "context_block": "",
        }
    }
    tools_used, chunks = [], []
    t0 = time.monotonic()
    async for ev in compiled.astream_events(
        {"messages": [("human", question)]}, config=config, version="v2"
    ):
        k = ev["event"]
        if k == "on_tool_start":
            tools_used.append(ev["name"])
        elif k == "on_chat_model_stream":
            tok = ev["data"]["chunk"].content
            if tok:
                chunks.append(tok)
    elapsed = time.monotonic() - t0
    answer = "".join(chunks)
    if echo:
        print("\n" + "=" * 78)
        print("Q:", question)
        print(f"TOOLS: {tools_used}  |  {elapsed:.1f}s")
        print("-" * 78)
        print(answer)
    return {"q": question, "tools": tools_used, "elapsed": elapsed, "answer": answer}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default="gpt-4.1-mini")
    ap.add_argument("--compare", default=None, help="comma-sep model list")
    args = ap.parse_args()

    questions = CANNED if (args.all or not args.question) else [" ".join(args.question)]

    if args.compare:
        models = [m.strip() for m in args.compare.split(",")]
        lines = ["# AI model comparison\n"]
        for q in questions:
            lines.append(f"\n## Q: {q}\n")
            for m in models:
                print(f"\n### [{m}] {q}")
                compiled = build_for_model(m)
                r = await run_one(compiled, q, echo=False)
                print(f"  tools={r['tools']} {r['elapsed']:.1f}s")
                print(r["answer"])
                lines.append(f"\n### {m}  ({r['elapsed']:.1f}s, tools: {', '.join(r['tools']) or 'none'})\n")
                lines.append(r["answer"] + "\n")
        out = os.path.join(os.path.dirname(__file__), "ai_eval_compare.md")
        with open(out, "w") as f:
            f.write("\n".join(lines))
        print(f"\n\nWrote {out}")
        return

    compiled = build_for_model(args.model)
    print(f"[model={args.model}]")
    for q in questions:
        await run_one(compiled, q)


if __name__ == "__main__":
    asyncio.run(main())
