# LedgerGuard

A double-entry payment ledger with an AI fraud-investigation agent, where a human always approves a freeze.

Money moves through a ledger that can't lose a rupee. A rules detector watches the event stream for
fraud patterns, and an LLM agent investigates each alert using read-only tools and writes a cited
report. An analyst then decides what happens. The agent can never freeze an account itself. The
whole stack runs locally and for free on a local [Ollama](https://ollama.com) model, and an eval
harness measures how well the agent actually does.

## How it works

```
simulator ──► ledger-service ──outbox──► Kafka ──► detector ──► cases ──► agent-worker ──► report
 (traffic)    (Spring Boot,                        (rules)               (LangGraph +       │
              double-entry)                                               read-only MCP)    ▼
                    ▲                                                                 analyst console
                    └──────────── freeze (only after a human approves) ◄── agent-api ◄── (React UI)
```

| Service | What it does |
|---|---|
| `ledger-service` | Java 21 / Spring Boot double-entry ledger: accounts, transfers, deposits, reversals, idempotency keys, freeze, reconcile. Writes events to an outbox, relayed to Kafka at least once ([docs/events.md](docs/events.md)). |
| `detector` | Consumes ledger events and opens a case when one of five rules fires: velocity, structuring, fan-in to a new account, rapid fan-out after receiving, round trip. |
| `mcp-server` | Read-only [MCP](https://modelcontextprotocol.io) tools over the ledger (`get_account`, `list_transfers`, `trace_money_flow` and more). Customer text is wrapped as untrusted. It holds only a read-only key. |
| `agent-worker` | LangGraph investigator: calls the MCP tools, writes a structured report with cited transfer ids, and either auto-clears an obviously fine case or hands it to a human. Code, not the model, enforces the safety rules. |
| `agent-api` | Case queue and decisions for the UI. It is the only holder of the ledger's admin key, and uses it only when an analyst approves a freeze. |
| `ui` | Analyst console: case queue, report with evidence, money-flow graph, and Freeze / Clear / Escalate decisions. |
| `simulator` | Seeded traffic: 500 normal accounts plus 60 labelled fraud and look-alike scenarios, some with prompt-injection memos. Its labels are the eval's ground truth. |

## Quick start

### 1. Install the prerequisites

| Tool | Why | Install |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | runs the stack | give Docker at least 4 GB of memory |
| [Ollama](https://ollama.com/download) | the free local model | then run `ollama pull qwen2.5:3b` (about 2 GB) |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | runs the simulator, eval and Python tests | it installs Python 3.12 by itself |
| `make` | runs the shortcuts below | macOS/Linux: usually installed. Windows: `winget install ezwinports.make` |

You need about 8 GB of RAM in total: the model takes about 2.3 GB and the Docker stack about 4 GB. Java and
Node are only needed to run the ledger and UI tests outside Docker, not to run the project.

### 2. Get the code and create `.env`

```bash
git clone https://github.com/Aditya-7826/ledgerguard.git
cd ledgerguard
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

The defaults in `.env` work for a local demo. Change the passwords and API keys if the stack will be
reachable by anyone else (each API key needs at least 16 characters). `.env` is gitignored.

### 3. Start everything

Make sure Ollama is running (its icon is in the menu bar or system tray), then:

```bash
make up
```

The first build takes a few minutes. `make ps` should list `postgres`, `kafka`, `ledger-service`,
`detector`, `mcp-server`, `agent-worker`, `agent-api` and `ui` as running.

### 4. Generate traffic

```bash
make simulate
```

This plays about 10 simulated days of payments in about 4 minutes. The detector opens cases as fraud patterns
appear, and the agent investigates them one at a time (about a minute each on a laptop CPU).

### 5. Open the analyst console

Go to **http://localhost:3000**. Open a case to see the agent's verdict, risk score, cited evidence
and the money-flow graph, then make the call: **Freeze**, **Clear** or **Escalate**. Only a Freeze
approved here ever reaches the ledger.

### 6. Stop

```bash
make down                 # stops the stack and keeps its data
docker compose down -v    # stops it and deletes the data too, for a clean start
```

### No GPU or little RAM? Try the keyless demo

Set `LLM_PROVIDER=fake` in `.env` to run the whole pipeline without any model. A deterministic
stand-in writes the reports. It shows the flow end to end, but its verdicts mean nothing, and the
eval refuses to score it.

## Running the tests

```bash
make test                 # everything (needs Java 21 and Node 20+ as well as uv)
make test-agent           # agent: routing, budgets, citations, injection screen (starts Postgres in Docker)
make test-ledger          # ledger (Java, Testcontainers)
make test-mcp test-sim test-eval test-ui
```

## Measuring the agent (eval)

The eval starts its own isolated stack, replays the seeded scenarios, lets the agent investigate
every case, and scores the reports against the simulator's labels. Results land in `eval/results/`.

```bash
# one run of all ~104 cases: about 2 hours on a laptop CPU
uv run --no-project --python 3.12 python eval/run_eval.py --runs 1

# a quick smoke test: score only the first 5 investigated cases
uv run --no-project --python 3.12 python eval/run_eval.py --runs 1 --max-cases 5

# fill in this README's results section from the latest result files
uv run --no-project --python 3.12 python scripts/update_readme_results.py
```

`make eval` runs 3 full runs, which gives a mean ± std but takes about 6 hours with a local model. If the
eval process dies but its stack survives, `--resume eval/runs/<timestamp>` scores what is there
without re-simulating. `make loadtest` runs k6 against an isolated ledger (results below).

## Configuration

Everything is set in `.env` ([.env.example](.env.example) documents every value). The ones you are most
likely to change:

| Setting | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` (local, free), `anthropic` (needs `ANTHROPIC_API_KEY`), or `fake` (keyless demo) |
| `LLM_MODEL` | `qwen2.5:3b` | Any model your provider serves, e.g. a bigger Ollama model (`ollama pull` it first) |
| `AGENT_CONCURRENCY` | `1` | Parallel investigations. Keep 1 for a CPU model; raise it for a hosted API |
| `AGENT_MAX_TOOL_CALLS` | `12` | Hard cap on tool calls per case |
| `AGENT_INVESTIGATE_TIMEOUT_S` | `300` | Wall-clock limit per case |
| `SIM_TIME_SCALE` | `3600` | Simulated seconds per real second (1 = real time) |
| `LEDGER_PORT` / `AGENT_API_PORT` / `UI_PORT` | `8080` / `8090` / `3000` | Host ports, if those are taken |

To compare models, change `LLM_PROVIDER` / `LLM_MODEL`, rerun the eval, and compare the result files.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Every case says "The agent failed on this case" | The agent can't reach Ollama. Check that `curl http://localhost:11434/api/version` answers and that `ollama list` shows the model named in `LLM_MODEL`. Failed cases still go to the human queue; after fixing it, use **Re-run agent** on a case. |
| Docker commands hang, or the PC freezes during the eval | Out of memory. Quit other apps, keep Docker's memory limit around 4 GB (on Windows: `memory=4GB` in `%UserProfile%\.wslconfig`, then restart Docker), and keep the machine from sleeping during long runs. |
| `port is already allocated` | Set `LEDGER_PORT`, `AGENT_API_PORT`, `UI_PORT` or `POSTGRES_PORT` in `.env` to free ports. |
| `make: command not found` on Windows | `winget install ezwinports.make`, then open a new terminal. Or run the commands from the [Makefile](Makefile) directly. |
| The eval says "other LedgerGuard stacks are running" | Run `make down` first: the eval wants the memory to itself. |

## Project layout

```
ledger-service/   Java 21 Spring Boot ledger (Maven wrapper included)
agent-service/    detector, agent worker and agent API (one Python package, three containers)
mcp-server/       read-only MCP tools over the ledger
simulator/        seeded traffic and labelled fraud scenarios
ui/               React + Vite analyst console
eval/             eval harness, metrics and results
loadtest/         k6 load test and retry storm, with results
docs/             design notes (event delivery guarantees)
```

## Results

<!-- RESULTS:START -->

_Generated by `scripts/update_readme_results.py` from the latest result files. Do not edit by hand._

### Investigation agent (`make eval`)

- Model: `qwen2.5:3b` via Ollama (local, 3.1B parameters, Q4_K_M), fully offloaded to the integrated Radeon GPU, which shares system RAM; temperature 0, context 8192 tokens, ≤ 12 tool calls and 300 s per case
- 1 run(s) of the full simulator scenario set (seed 42), 105 detector cases per run, investigated one at a time
- Hardware: AMD Ryzen 5 5600H with Radeon Graphics (12 logical CPUs), 7.3 GB RAM, Windows 11 (10.0.26200); Docker VM 12 CPUs / 3.8 GB
- Result file: [`eval/results/20261105T121029.json`](eval/results/20261105T121029.json)

> These numbers come from a 3-billion-parameter model running locally on a laptop, chosen so the whole project runs for free. A frontier model would score noticeably higher. The agent is model-agnostic: `LLM_PROVIDER` and `LLM_MODEL` in `.env` pick the model, and the eval harness is how you compare models: change them, run `make eval`, and a new result file lands in `eval/results/`.

| Metric | Value (single run, no std) |
|---|---|
| Precision (RECOMMEND_FREEZE vs FRAUD) | 69.2% |
| Recall (RECOMMEND_FREEZE vs FRAUD) | 24.0% |
| F1 | 35.6% |
| False-freeze recommendation rate on LEGIT | 26.7% |
| Auto-clear rate | 0.0% |
| Fraud leak rate among auto-cleared (safety) | not measurable |
| Injection resistance (not swayed AND detected) | 0.0% |
| Injection detected | 25.0% |
| Verdict swayed by injection | 75.0% |
| Injection caught by memo screen (no model) | 100.0% |
| Memo screen false alarms on unexposed cases | 1.0% |
| Detector coverage of fraud subjects | 100.0% |
| Detector alerts on baseline accounts | 0.0% |
| Agent run failure rate | 0.0% |
| Avg tool calls per investigation | 4.6 |
| Avg input tokens per investigation | 8,268 |
| Avg output tokens per investigation | 806 |
| Avg latency per investigation | 73.9s |
| Avg cost per case | $0.0000 |
| Total cost per eval run | $0.0000 |

Run 1 counts: 75 fraud / 30 legit cases, 26 freeze recommendations (18 correct), 0 auto-cleared, 8 cases exposed to injected memos, 0 failed agent runs.

### Ledger under load (`make loadtest`)

- 50 virtual users, 60 s per scenario, 200 funded accounts; k6 runs inside the stack's Docker network
- Hardware: AMD Ryzen 5 5600H with Radeon Graphics (12 logical CPUs), 7.3 GB RAM, Windows 11 (10.0.26200); Docker VM 12 CPUs / 5.8 GB
- Result file: [`loadtest/results/20261104T122815.json`](loadtest/results/20261104T122815.json)

| Scenario | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|
| `uniform` | 619.9 req/s | 69 ms | 172 ms | 245 ms | 0.00% |
| `hot_account` | 143.4 req/s | 359 ms | 459 ms | 532 ms | 0.00% |
| `retry_storm` | 25,000 requests | 49 ms | 119 ms | 162 ms | 0.00% |

`hot_account` reaches 23% of `uniform`'s throughput. That is expected: every transfer locks both account rows (`SELECT … FOR UPDATE`), and when 80% of transfers credit one merchant they queue on that merchant's row lock and commit one at a time.

Retry storm: 10,000 transfers each sent 2–3 times with the same `Idempotency-Key` (25,000 requests, half of them concurrent duplicates) created exactly 10,000 transfers and 20,000 entries; 0 replays differed from the original response, 0 duplicate transfers exist, and reconcile reported `ok=true` with a net of 0 across 112,002 entries.

<!-- RESULTS:END -->

## Known limitations

These come from the eval above (`qwen2.5:3b`, one run, 105 cases) and the run before it (104 cases, before the memo screen existed).

- **Prompt injection sways the model, so the gate no longer relies on it.** In the earlier run, the one auto-cleared case (`round_trip-02`) was fraud with an injected memo: the model returned CLEAR with risk 0 and confidence 1 and never flagged the injection, and auto-clear trusted the model's own `injection_detected` flag. Auto-clear is now also gated by a deterministic memo screen ([screen.py](agent-service/app/agent/screen.py)). The screen checks the account's memos and owner name itself, plus every tool result, and any hit (or a screen that can't finish) blocks auto-clear whatever the model says. In the run above it flagged all 8 injection-exposed cases, and its one other hit was the same payload seen on a neighbouring account. It flagged no ordinary memos. The model's own record didn't improve: injections still changed its verdict in 6 of 8 exposed cases, and it noticed only 2 of them.
- **The screen's effect on auto-clear isn't shown by this run.** The model gave no case the confident, low-risk CLEAR that auto-clear needs, so nothing was auto-cleared and nothing could leak, with or without the screen. That the screen blocks such a case is shown by a test that replays the earlier failure (`test_injected_memo_blocks_auto_clear_even_if_the_model_never_reads_it`), not by these numbers. The screen was also written after reading the simulator's payloads. It is tested on held-out attack phrasings too, but it is a pattern check, so cleverly worded text can still get past it.
- **Low recall.** The model recommended a freeze for 18 of 75 fraud cases (24%) and wrongly recommended one for 8 of 30 legitimate ones. Every case still goes to an analyst unless it is auto-cleared.
- **Run-to-run noise.** Between the two runs, precision moved from 76% to 69% and injection detection from 75% to 25%, with the same model at temperature 0. That is the difference between two single runs of 105 cases, not an effect of the screen, which never changes the model's verdict. Several runs (`make eval` does 3) would give a mean and a spread.
- **Occasional runaway reports.** In an earlier, interrupted run, the model once looped while writing its structured JSON report until it filled the context window, and all three retries repeated it, since temperature 0 gives the same output every time. The case ended in `PENDING_REVIEW` as designed. A cap on report length (`num_predict`) would make such a failure end in about a minute instead of nine.
- **Interrupted runs.** On a 7.3 GB laptop, runs were interrupted by memory exhaustion and by the laptop sleeping. Both finished with `run_eval.py --resume`, which re-scores the surviving stack without re-simulating, so wall-clock times are missing from their result files.

## License

MIT. See [LICENSE](LICENSE).
