# FinanceBot Configuration & Rules

## System Behavior
- **Conciseness First**: Respond with minimal necessary text.
- **Language Policy**: Use English for all code, internal logic, and architecture discussions. 
- **User-Facing Content**: Use Russian ONLY for bot strings (UI/Messages) and high-level logic summaries if requested.
- **Token Saving**: Avoid verbose explanations. Use technical slang (middleware, handler, etc.).
- If a task is small, implement it immediately without asking for permission or explaining the plan.

## Code Style / Formatting
- **PEP8 & Type Hints**: Mandatory.
- **Formatting**: Do NOT run `ruff format` — formatting is disabled in this project. Only apply targeted edits; never reformat unrelated files.
- **Documentation**: Use short, concise Docstrings. Use English for technical docstrings to save tokens. Use Russian only for complex business-logic explanations.
- **Bot Strings**: Keep all `f-strings` for Telegram UI in Russian as per requirements.

## Bug Fixing Workflow
- When fixing bugs from a review/audit report: validate each issue against the real code first, apply minimal fixes, add/run tests, and commit each fix separately.
- Honestly flag non-issues and skip them.
- **Prod "broken" feature**: before debugging code, SSH to prod and compare the deployed commit SHA with local `main` HEAD, and check deploy pipeline health (`git status` on prod — watch for CRLF-modified files blocking pull). Report deployed SHA vs local HEAD first. Most past "bugs" were a stale deploy, not code.

## Testing
- Always run the full test suite and verify it passes before committing, especially after multi-file changes or auth/timezone/DB-related edits.

## Git / Commits
- Use the Bash tool with bash syntax only — do NOT use PowerShell here-string syntax in Bash. For multi-line commit messages use a heredoc or `-m` flags.

## Tool Usage / Verification
- Avoid excessive parallel/background tool calls; run review and commit sequentially when asked.
- Never claim a file was committed without verifying with `git log`/`git status`.

## Tech Stack
- Python 3.14, aiogram 3.28
- SQLAlchemy 2.0 (Async), aiosqlite
- Matplotlib, Pytest (asyncio)

## Project Structure & Commands
- Entry: `bot.py` | Config: `config.py`
- Core: `core/` (handlers, keyboards, utils)
- Tests: `tests/`
- Run: `python bot.py`
- Test: `./env/Scripts/python.exe -m pytest`
- Lint: `ruff check . --fix` (do NOT run `ruff format` — see Code Style / Formatting)

## Constraints
- **Security**: NEVER read `.env` or `.sqlite3` files (already in permissions).
- **Environment**: Always use the virtual env in `./env/Scripts/`.
- **Git**: NEVER add `Co-Authored-By` lines to commit messages. Do not list Claude as a co-author.