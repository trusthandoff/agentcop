# Contributing to agentcop

Thank you for taking the time to contribute. agentcop is a focused library —
every addition should be deliberate, well-tested, and consistent with the
existing design.

---

## Table of contents

- [Quick start for contributors](#quick-start-for-contributors)
- [Architecture overview](#architecture-overview)
- [Running the test suite](#running-the-test-suite)
- [Adding a new adapter](#adding-a-new-adapter)
- [Adding a new built-in detector](#adding-a-new-built-in-detector)
- [Code style](#code-style)
- [Pull request checklist](#pull-request-checklist)
- [Reporting bugs](#reporting-bugs)
- [Proposing features](#proposing-features)

---

## Quick start for contributors

```bash
git clone https://github.com/trusthandoff/agentcop.git
cd agentcop
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q          # all 1 000+ tests should pass
```

---

## Architecture overview

```
src/agentcop/
├── __init__.py          ← public API re-exports only
├── event.py             ← SentinelEvent, ViolationRecord — Pydantic models
├── violations.py        ← built-in detector functions + DEFAULT_DETECTORS
├── sentinel.py          ← Sentinel class — orchestrator, owns the lock
├── otel.py              ← optional OTel export integration
└── adapters/
    ├── __init__.py      ← re-exports SentinelAdapter only
    ├── base.py          ← SentinelAdapter @runtime_checkable Protocol
    └── <name>.py        ← one file per optional adapter
```

Key invariants (see `CLAUDE.md` for the full list):

- **Detectors are pure functions.** No I/O, no state, no side effects.
- `Sentinel._lock` guards all mutations to `_events` and `_detectors`.
- Optional dependencies are **never** imported at module top level — each
  adapter has a `_require_<name>()` guard called inside `__init__`.
- `ViolationRecord.severity` cannot be `INFO`.
- `SentinelEvent` and `ViolationRecord` are immutable after construction.

---

## Running the test suite

```bash
# Full suite
pytest tests/ -v

# Single adapter
pytest tests/test_langgraph_adapter.py -v

# Thread-safety only
pytest tests/test_sentinel.py::TestThreadSafety -v

# Quick smoke check
pytest tests/ -q
```

All tests mock optional dependencies — you do **not** need to install
LangGraph, OpenAI, or any other optional package to run the suite.

---

## Adding a new adapter

Follow these steps exactly. Deviations will be caught in review.

1. **Create** `src/agentcop/adapters/<name>.py`.
2. Add a `_require_<name>()` guard at the top that tries `import <package>`
   and raises `ImportError` with install instructions on failure.
3. Call `_require_<name>()` inside `__init__`, **not** at module level.
4. Set `source_system = "<name>"` as a class attribute.
5. Implement `to_sentinel_event(self, raw: dict) -> SentinelEvent`.
6. Add `<name> = ["<package>>=<version>"]` to `[project.optional-dependencies]`
   in `pyproject.toml`.
7. Write `tests/test_<name>_adapter.py`. Tests must:
   - Mock the import guard with `patch("agentcop.adapters.<name>._require_<name>")`.
   - Never require the real package to be installed.
   - Cover all event types, error paths, drain/flush, thread safety, and
     protocol conformance.
8. Write `docs/adapters/<name>.md` with quickstart, detector recipes,
   attributes reference, and API reference.
9. Add the adapter to the table in `README.md` and `docs/index.md`.
10. Do **not** import the adapter from `agentcop/__init__.py`.

See `src/agentcop/adapters/langsmith.py` and `tests/test_langsmith_adapter.py`
as a reference implementation.

---

## Adding a new built-in detector

1. Add `detect_<name>(event: SentinelEvent) -> Optional[ViolationRecord]` to
   `violations.py`.
2. Append it to `DEFAULT_DETECTORS` at the bottom of that file.
3. Re-export from `__init__.py` and add to `__all__`.
4. The `violation_type` string in the returned `ViolationRecord` is **public
   API** — choose it carefully.
5. Write tests in `tests/test_violations.py`.

---

## Code style

agentcop uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
pip install ruff
ruff check src/ tests/      # lint
ruff format src/ tests/     # format
```

The configuration lives in `pyproject.toml` under `[tool.ruff]`.

Additional style rules enforced in review (not yet automated):

- No type annotations required, but welcome on public API.
- Docstrings on all public classes and methods.
- Keep line length ≤ 99 characters.
- Prefer explicit over implicit — no magic, no clever one-liners.

---

## Pull request checklist

Before opening a PR, verify:

- [ ] All existing tests pass: `pytest tests/ -q`
- [ ] New code is covered by tests
- [ ] `ruff check src/ tests/` passes
- [ ] `ruff format --check src/ tests/` passes
- [ ] CHANGELOG.md has an entry under `[Unreleased]`
- [ ] If adding an adapter: docs, README table, and `docs/index.md` are updated
- [ ] If bumping version: both `pyproject.toml` and `src/agentcop/__init__.py`
      are updated together

---

## Reporting bugs

Use the [Bug Report](.github/ISSUE_TEMPLATE/bug_report.yml) template. Include:

- agentcop version (`python -c "import agentcop; print(agentcop.__version__)"`)
- Python version
- Minimal reproducible example
- Full traceback

---

## Proposing features

Use the [Feature Request](.github/ISSUE_TEMPLATE/feature_request.yml) template.
For large changes — new adapters, schema changes, new core primitives — open an
issue first to discuss the design before writing code.

---

## Commit message format

```
type: short description (≤ 72 chars)

Optional longer body.

Co-Authored-By: Your Name <email>
```

Types: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`.
