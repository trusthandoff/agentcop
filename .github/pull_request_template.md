## Summary

<!-- What does this PR do? One paragraph is fine. -->

## Type of change

- [ ] Bug fix
- [ ] New adapter
- [ ] New built-in detector
- [ ] Documentation
- [ ] Refactor / internal improvement
- [ ] CI / tooling
- [ ] Other: <!-- describe -->

## Checklist

- [ ] All existing tests pass: `pytest tests/ -q`
- [ ] New code is covered by tests
- [ ] `ruff check src/ tests/` passes
- [ ] `ruff format --check src/ tests/` passes
- [ ] CHANGELOG.md updated under `[Unreleased]`

**If adding a new adapter:**
- [ ] `src/agentcop/adapters/<name>.py` with `_require_<name>()` guard
- [ ] `tests/test_<name>_adapter.py` — mocks the guard, no real package needed
- [ ] `docs/adapters/<name>.md` — quickstart, detector recipes, attributes reference
- [ ] Adapter row added to `README.md` adapter table
- [ ] Adapter row added to `docs/index.md`
- [ ] Optional dep added to `[project.optional-dependencies]` in `pyproject.toml`

**If changing the public API (`__init__.py`, `event.py`, `violations.py`):**
- [ ] `__all__` updated in `__init__.py`
- [ ] CHANGELOG entry notes the breaking/additive change

## Testing notes

<!-- How did you test this? Any edge cases worth calling out? -->

## Related issues

<!-- Closes #NNN -->
