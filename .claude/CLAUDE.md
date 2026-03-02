# FinanceBot Configuration & Rules

## System Behavior
- **Conciseness First**: Respond with minimal necessary text.
- **Language Policy**: Use English for all code, internal logic, and architecture discussions. 
- **User-Facing Content**: Use Russian ONLY for bot strings (UI/Messages) and high-level logic summaries if requested.
- **Token Saving**: Avoid verbose explanations. Use technical slang (middleware, handler, etc.).
- If a task is small, implement it immediately without asking for permission or explaining the plan.

## Tech Stack
- Python 3.11+, aiogram 3.20
- SQLAlchemy 2.0 (Async), aiosqlite
- Matplotlib, Pytest (asyncio)

## Project Structure & Commands
- Entry: `bot.py` | Config: `config.py`
- Core: `core/` (handlers, keyboards, utils)
- Tests: `tests/`
- Run: `python bot.py`
- Test: `./env/Scripts/python.exe -m pytest`
- Lint: `ruff check . --fix` | Format: `black .`

## Code Style
- **PEP8 & Type Hints**: Mandatory.
- **Documentation**: Use short, concise Docstrings. Use English for technical docstrings to save tokens. Use Russian only for complex business-logic explanations.
- **Bot Strings**: Keep all `f-strings` for Telegram UI in Russian as per requirements.

## Constraints
- **Security**: NEVER read `.env` or `.sqlite3` files (already in permissions).
- **Environment**: Always use the virtual env in `./env/Scripts/`.