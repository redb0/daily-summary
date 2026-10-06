"""Схема сырого дампа: round-trip значений из спеки, не пересчёт той же функцией."""

import json
from datetime import date, datetime

from app.summary.models import parse_raw_dump, render_raw_dump

# Литерал из спеки. Пробелы не значимы, значения — да.
EXAMPLE = """
{
  "schema_version": 1,
  "date": "2026-10-05",
  "window": {
    "from": "2026-10-05T00:00:00+03:00",
    "to": "2026-10-05T19:40:00+03:00"
  },
  "generated_at": "2026-10-05T19:40:12+03:00",
  "sources": {
    "git": {"status": "ok", "repos": []},
    "transcripts": {"status": "ok", "sessions": []},
    "telegram": {
      "status": "unavailable",
      "code": "FLOOD_WAIT",
      "reason": "…",
      "chats": []
    }
  },
  "stats": {"commits": 0, "messages": 0, "sessions": 0, "bytes": 0},
  "truncations": []
}
"""


def test_raw_dump_round_trip() -> None:
    dump = parse_raw_dump(EXAMPLE)

    assert dump.schema_version == 1
    assert dump.date == date(2026, 10, 5)
    assert dump.window.from_ == datetime.fromisoformat("2026-10-05T00:00:00+03:00")
    assert dump.window.to == datetime.fromisoformat("2026-10-05T19:40:00+03:00")
    assert dump.generated_at == datetime.fromisoformat("2026-10-05T19:40:12+03:00")
    assert dump.sources.git.status == "ok"
    assert dump.sources.git.repos == []
    assert dump.sources.transcripts.status == "ok"
    assert dump.sources.transcripts.sessions == []
    assert dump.sources.opencode.status == "disabled"
    assert dump.sources.opencode.sessions == []
    assert dump.sources.telegram.status == "unavailable"
    assert dump.sources.telegram.code == "FLOOD_WAIT"
    assert dump.sources.telegram.reason == "…"
    assert dump.sources.telegram.chats == []
    assert dump.stats.commits == 0
    assert dump.stats.messages == 0
    assert dump.stats.sessions == 0
    assert dump.stats.bytes == 0
    assert dump.truncations == []

    rendered = json.loads(render_raw_dump(dump))
    assert rendered["window"]["from"] == "2026-10-05T00:00:00+03:00"
    assert rendered["window"]["to"] == "2026-10-05T19:40:00+03:00"
    assert rendered["generated_at"] == "2026-10-05T19:40:12+03:00"
    assert rendered["stats"]["bytes"] == 0
    assert "code" not in rendered["sources"]["git"]
    assert "reason" not in rendered["sources"]["transcripts"]
    assert rendered["sources"]["telegram"]["code"] == "FLOOD_WAIT"
    assert rendered["sources"]["telegram"]["reason"] == "…"
    assert parse_raw_dump(render_raw_dump(dump)) == dump
