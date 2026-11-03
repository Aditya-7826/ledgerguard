"""Agent worker: claims OPEN cases and investigates them. Run with ``python -m app.agent.worker``.

Holds no ledger key at all: its only view of the ledger is the READONLY MCP server.
"""

import asyncio
import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agent.graph import Deps, build_graph, run_case
from app.agent.llm import make_llm
from app.agent.repo import AgentRepo
from app.config import Settings
from app.db import connect, migrate

log = logging.getLogger("agent.worker")


def claim(db_url: str, limit: int, settle_seconds: float) -> list:
    """OPEN → INVESTIGATING for cases whose activity has settled. SKIP LOCKED: safe with many workers."""
    with connect(db_url) as c:
        rows = c.execute(
            """
            update cases set status = 'INVESTIGATING', updated_at = now()
             where id in (select id from cases
                           where status = 'OPEN' and last_activity_at < now() - make_interval(secs => %s)
                           order by opened_at
                           limit %s
                           for update skip locked)
            returning id
            """, (settle_seconds, limit)).fetchall()
    return [r[0] for r in rows]


def release_stuck(db_url: str) -> int:
    """Cases left INVESTIGATING by a crashed worker go back to OPEN after 10 minutes."""
    with connect(db_url) as c:
        return c.execute("update cases set status = 'OPEN', updated_at = now() "
                         "where status = 'INVESTIGATING' and updated_at < now() - interval '10 minutes'").rowcount


async def load_tools(mcp_url: str):
    client = MultiServerMCPClient({"ledger": {"url": mcp_url, "transport": "streamable_http"}})
    for attempt in range(60):
        try:
            return await client.get_tools()
        except Exception as e:  # noqa: BLE001 - MCP server may still be starting
            log.info("waiting for MCP server at %s (%s)", mcp_url, type(e).__name__)
            await asyncio.sleep(min(2 + attempt, 10))
    raise RuntimeError("MCP server unreachable")


async def main() -> None:
    s = Settings()
    migrate(s.agent_database_url)
    tools = await load_tools(s.mcp_url)
    log.info("loaded %d MCP tools: %s", len(tools), sorted(t.name for t in tools))
    deps = Deps(settings=s, llm=make_llm(s), tools=tools, repo=AgentRepo(s.agent_database_url))
    graph = build_graph(deps)
    slots = asyncio.Semaphore(s.agent_concurrency)
    running: set[asyncio.Task] = set()
    while True:
        released = await asyncio.to_thread(release_stuck, s.agent_database_url)
        if released:
            log.warning("released %d stuck cases", released)
        free = s.agent_concurrency - len(running)
        ids = await asyncio.to_thread(claim, s.agent_database_url, free, s.agent_settle_seconds) if free else []
        for case_id in ids:
            async def one(cid=case_id):
                async with slots:
                    r = await run_case(deps, cid, graph)
                    log.info("case %s → %s (tools=%d, tokens=%d/%d%s)", cid, r.outcome, r.tool_calls,
                             r.input_tokens, r.output_tokens, f", error={r.error}" if r.error else "")
            task = asyncio.create_task(one())
            running.add(task)
            task.add_done_callback(running.discard)
        await asyncio.sleep(0.5 if ids else 2.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
