# Contributing

## Development workflow

1. Create a focused branch.
2. Install the development extras with python -m pip install -e ".[dev]".
3. Add a failing test for behavioural changes.
4. Run ruff check, ruff format, mypy src and pytest.
5. Include a migration for model changes and verify upgrade plus downgrade.
6. Update the architecture or threat model when a trust boundary or reliability guarantee changes.

Commits should explain why a change is needed. Pull requests should describe the failure mode being
addressed, verification evidence and any operational trade-off.

