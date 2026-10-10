"""Схема дампа источника: round-trip значений, не пересчёт той же функцией."""

import json
from datetime import date, datetime

from app.summary.models import GitDump, SourceStatus, Window, parse_git_dump, render_dump

# Литерал схемы 2. Пробелы не значимы, значения — да.
EXAMPLE = """
{
  "schema_version": 2,
  "date": "2026-10-05",
  "window": {
    "from": "2026-10-05T00:00:00+03:00",
    "to": "2026-10-05T19:40:00+03:00"
  },
  "generated_at": "2026-10-05T19:40:12+03:00",
  "status": "ok",
  "bytes": 0,
  "truncations": [],
  "repos": []
}
"""


def test_git_dump_round_trip() -> None:
    dump = parse_git_dump(EXAMPLE)
    rendered = json.loads(render_dump(dump))

    kept = (
        "schema_version",
        "date",
        "window",
        "generated_at",
        "status",
        "bytes",
        "truncations",
        "repos",
    )
    assert (
        dump,
        {key: rendered[key] for key in kept},
        [key for key in ("code", "reason") if key in rendered],
        parse_git_dump(render_dump(dump)),
    ) == (
        GitDump(
            date=date(2026, 10, 5),
            window=Window.model_validate(
                {
                    "from": "2026-10-05T00:00:00+03:00",
                    "to": "2026-10-05T19:40:00+03:00",
                },
            ),
            generated_at=datetime.fromisoformat("2026-10-05T19:40:12+03:00"),
            status=SourceStatus.OK,
            bytes=0,
        ),
        {
            "schema_version": 2,
            "date": "2026-10-05",
            "window": {
                "from": "2026-10-05T00:00:00+03:00",
                "to": "2026-10-05T19:40:00+03:00",
            },
            "generated_at": "2026-10-05T19:40:12+03:00",
            "status": "ok",
            "bytes": 0,
            "truncations": [],
            "repos": [],
        },
        [],
        dump,
    )
