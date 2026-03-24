# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SeeAgent is an open-source multi-agent AI assistant. Python 3.11+ package with a Tauri desktop app (Rust + React). The agent uses a "Ralph Wiggum" never-give-up execution loop, 89+ tools, 3-layer memory, and supports 6 IM platforms and 30+ LLM providers.

## Common Commands

### Install & Setup
```bash
pip install -e ".[dev]"          # Install with dev dependencies
pip install -e ".[all]"          # Install with all optional deps (IM channels, desktop automation)
```

### Linting & Type Checking
```bash
ruff check src/                  # Lint
mypy src/                        # Type check
```

### Testing (5-tier pyramid)
```bash
pytest tests/unit/ -x -v         # L1: Unit tests (<30s)
pytest tests/component/ -x -v    # L2: Component tests (<2min)
pytest tests/integration/ -v     # L3: Integration tests (<3min)
pytest tests/e2e/ -v             # L4: E2E tests (needs LLM_TEST_MODE=replay)
pytest tests/quality/ -v         # L5: Quality evaluation
pytest tests/ -v --cov=src/seeagent --cov-report=xml -k "not TestVectorStore"  # Full suite with coverage
```

### Run a Single Test
```bash
pytest tests/unit/test_config.py -x -v                    # Single file
pytest tests/unit/test_config.py::TestClassName::test_name -x -v  # Single test
```

### Desktop App (apps/setup-center)
```bash
cd apps/setup-center && npm ci && npm run build         # Build Tauri-embedded frontend
cd apps/setup-center && VITE_BUILD_TARGET=web npm run build:web  # Build standalone web UI (→ dist-web/)
cd apps/setup-center && npx tauri build                 # Build Tauri desktop app (requires Rust)
```

### Version Check
```bash
python scripts/version.py check   # Verify version consistency across files
```

## Architecture

### Source Layout
- `src/seeagent/` — Main Python package
- `apps/setup-center/` — Tauri desktop app (Rust backend + React/Vite frontend)
- `skills/` — Extensible skill definitions (70+ skills, bundled into wheel)
- `mcps/` — MCP (Model Context Protocol) server configs
- `identity/` — Agent persona files (AGENT.md, SOUL.md, USER.md, MEMORY.md, personas/)
- `tests/` — 5-level test hierarchy (unit → component → integration → e2e → quality)
- `specs/` — Technical specifications (tool-system, skill-system, core-agent)
- `docs/` — Architecture and deployment documentation

### Core Modules (`src/seeagent/`)

**Agent Core** (`core/`):
- `agent.py` — Main Agent class, orchestrates all modules (largest file)
- `brain.py` — LLM interaction and tool calling
- `reasoning_engine.py` — ReAct reasoning (Think → Act → Observe)
- `ralph.py` — "Never give up" retry loop
- `prompt_assembler.py` — Dynamic prompt construction
- `context_manager.py` — Context window management
- `tool_executor.py` — Tool execution engine
- `skill_manager.py` — Skill loading and management

**Tool System** (`tools/`):
- `catalog.py` — Tool registry with progressive disclosure (list → details → execute)
- `handlers/` — Modular tool handlers (filesystem, browser, MCP, web_search, desktop, etc.)

**Memory System** (`memory/`): 3-layer architecture
- Working memory (conversation context)
- Core memory (persistent, aiosqlite)
- Dynamic memory (semantic retrieval with vector embeddings)
- Key files: `manager.py`, `storage.py`, `extractor.py`, `retrieval.py`, `vector_store.py`

**LLM Abstraction** (`llm/`): Provider-agnostic client with adapters for Anthropic, OpenAI, and others.

**Multi-Agent** (`agents/`): `orchestrator.py` coordinates parallel agent teams, `presets.py` defines agent templates. `factory.py` creates agents dynamically, `task_queue.py` manages async task dispatching.

**Channels** (`channels/`): IM platform adapters (Telegram, Feishu, DingTalk, WeCom, QQ, OneBot) routed through `gateway.py`. Media handling (audio, images) in `media/`.

**Skills** (`skills/`): SKILL.md-based skill system with loader, registry, and i18n.

**REST API** (`api/`): FastAPI server (`server.py`) on port 18900 with SSE streaming chat, skills management, file upload, and WebSocket. `adapters/` contains seecrab-format converters for the frontend card UI.

**Evolution** (`evolution/`): Self-evolution subsystem — analyzes failure logs (`log_analyzer.py`, `failure_analysis.py`), generates new skills (`generator.py`), and installs them (`installer.py`).

**Evaluation** (`evaluation/`): Quality evaluation pipeline with LLM-as-judge scoring (`judge.py`), metrics tracking, and optimization feedback loop.

### Entry Point
- CLI: `seeagent` command → `src/seeagent/main.py` (Typer app)
- `seeagent serve` — starts FastAPI HTTP server (port 18900) + optional IM channels simultaneously
- `seeagent run` — CLI interactive mode

### Configuration
- `src/seeagent/config.py` — Pydantic settings class
- Environment variables: `ANTHROPIC_API_KEY`, `DEFAULT_MODEL`, `THINKING_MODE`, `MAX_ITERATIONS`, `TOOL_MAX_PARALLEL`, etc.

## Code Standards

- **Line length**: 100 chars (ruff)
- **Python target**: 3.11
- **Async**: `asyncio_mode = "auto"` in pytest; uses `nest-asyncio` for nested event loops
- **Ruff rules**: E, F, I, N, W, UP, B, C4, SIM (with E501, B904, N806, SIM102/103/105 ignored)
- **Test fixtures**: `conftest.py` provides `mock_llm_client`, `mock_brain`, `test_session`, `tmp_workspace`, `test_settings`, `mock_response_factory`

## Build & Packaging
- Build system: hatchling
- Wheel bundles: `skills/system` → `builtin_skills/system/`, external skills → `builtin_skills/external/`, `mcps` → `builtin_mcps/`, web frontend from `apps/setup-center/dist-web` → `seeagent/web`
- Desktop app uses PyInstaller to package Python backend, then Tauri bundles everything
