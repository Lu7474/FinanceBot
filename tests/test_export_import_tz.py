"""Test _resolve_date_range: TZ-aware behaviour (review #7)."""

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parent.parent))


class _FakeDatetime(datetime):
    """Подмена datetime.now() — возвращает фиксированный момент в UTC."""

    _fixed = datetime(
        2026, 3, 31, 22, 0, 0, tzinfo=timezone.utc
    )  # 2026-04-01 01:00 МСК

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            # naive .now() — то, как делает _resolve_date_range. Возвращаем UTC-локальное.
            return cls._fixed.replace(tzinfo=None)
        return cls._fixed.astimezone(tz)


def test_resolve_date_range_uses_moscow_tz():
    """В 22:00 UTC = 01:00 МСК уже следующий день → _resolve_date_range('month') должен вернуть
    апрель 2026, а не март. Сейчас функция использует наивный datetime.now() — тест падает."""
    from core.handlers import export_import

    with patch.object(export_import, "datetime", _FakeDatetime):
        d_from, d_to = export_import._resolve_date_range("month")

    assert d_from == date(2026, 4, 1), (
        f"date_from должен быть 2026-04-01 (по МСК уже апрель), а получен {d_from}"
    )
    assert d_to == date(2026, 4, 1), (
        f"date_to должен быть 2026-04-01 (сегодня по МСК), а получен {d_to}"
    )


def test_resolve_date_range_all_returns_none():
    """Sanity check: 'all' возвращает (None, None) — не зависит от TZ."""
    from core.handlers import export_import

    d_from, d_to = export_import._resolve_date_range("all")
    assert d_from is None and d_to is None
