"""Сборщик git на реальном `git`, без моков подпроцесса."""

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.collectors.git import CollectedGit, collect_git
from app.config import Config, GitConfig, NotesConfig, load_config
from app.summary.models import GitCommit, GitDirty, GitRepo, Window

_MOSCOW = ZoneInfo("Europe/Moscow")
_AUTHOR = "vvoronov@mwnts.ru"
_NOTE_DIFF = """\
diff --git a/note.txt b/note.txt
new file mode 100644
index 0000000..d0f56e1
--- /dev/null
+++ b/note.txt
@@ -0,0 +1 @@
+привет
"""
_COMMIT_STAT = " note.txt | 1 +\n 1 file changed, 1 insertion(+)"
_DIRTY_STAT = " note.txt | 2 ++\n 1 file changed, 2 insertions(+)"
_BIG_STAT = (
    " big.txt | 40 ++++++++++++++++++++++++++++++++++++++++\n 1 file changed, 40 insertions(+)"
)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    merged = os.environ.copy()
    if env is not None:
        merged.update(env)
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        env=merged,
    )
    return result.stdout


def _init_repo(repo: Path, email: str = _AUTHOR) -> None:
    subprocess.run(  # noqa: S603
        ["git", "init", "-b", "main", str(repo)],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "config", "user.email", email)
    _git(repo, "config", "user.name", "Vladimir Voronov")


def _commit(
    repo: Path,
    message: str,
    when: datetime,
    *,
    email: str = _AUTHOR,
    committer: datetime | None = None,
) -> str:
    _git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        message,
        env={
            "GIT_AUTHOR_DATE": when.isoformat(),
            "GIT_COMMITTER_DATE": (committer or when).isoformat(),
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_NAME": "Vladimir Voronov",
        },
    )
    return _git(repo, "rev-parse", "HEAD").strip()


def _config(root: Path, *, max_depth: int = 3, include_dirty: bool = True) -> Config:
    return Config(
        notes=NotesConfig(timezone="Europe/Moscow"),
        git=GitConfig(
            roots=[root],
            max_depth=max_depth,
            authors=[_AUTHOR],
            include_dirty=include_dirty,
        ),
    )


def _window(start: datetime, end: datetime) -> Window:
    return Window.model_validate({"from": start, "to": end})


def _day(year: int, month: int, day: int) -> Window:
    return _window(
        datetime(year, month, day, 0, 0, tzinfo=_MOSCOW),
        datetime(year, month, day, 23, 59, 59, tzinfo=_MOSCOW),
    )


def test_collects_commit_and_dirty_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _freeze(monkeypatch, datetime(2026, 10, 5, 18, 0, tzinfo=_MOSCOW))
    repo = tmp_path / "repo"
    _init_repo(repo)
    note = repo / "note.txt"
    note.write_text("привет\n")
    _git(repo, "add", "note.txt")
    sha = _commit(repo, "добавить приветствие", datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("UTC")))
    note.write_text("привет\nещё\nстрока\n")
    draft = repo / "scratch" / "a.txt"
    draft.parent.mkdir()
    draft.write_text("черновик\n")
    (repo / "scratch" / "b.txt").write_text("ещё\n")

    collected = collect_git(_config(tmp_path, max_depth=2, include_dirty=True), _day(2026, 10, 5))

    assert collected == CollectedGit(
        repos=[
            GitRepo(
                path=str(repo.resolve()),
                commits=[
                    GitCommit(
                        sha=sha,
                        committed_at=datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW),
                        message="добавить приветствие",
                        files=["note.txt"],
                        diffstat=_COMMIT_STAT,
                        diff=_NOTE_DIFF,
                    ),
                ],
                dirty=GitDirty(files=["note.txt", "scratch/"], diffstat=_DIRTY_STAT),
            ),
        ],
        truncations=[],
    )


def test_past_day_does_not_include_todays_dirty_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _freeze(monkeypatch, datetime(2026, 10, 6, 12, 0, tzinfo=_MOSCOW))
    repo = tmp_path / "repo"
    _init_repo(repo)
    note = repo / "note.txt"
    note.write_text("привет\n")
    _git(repo, "add", "note.txt")
    _commit(repo, "добавить приветствие", datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("UTC")))
    note.write_text("привет\nещё\n")

    collected = collect_git(_config(tmp_path), _day(2026, 10, 5))

    assert (collected.repos[0].dirty, [item.message for item in collected.repos[0].commits]) == (
        None,
        ["добавить приветствие"],
    )


def test_repo_without_commits_keeps_dirty_work_inside_the_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _freeze(monkeypatch, datetime(2026, 10, 5, 18, 0, tzinfo=_MOSCOW))
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "note.txt").write_text("черновик\n")

    collected = collect_git(_config(tmp_path), _day(2026, 10, 5))

    assert collected == CollectedGit(
        repos=[
            GitRepo(
                path=str(repo.resolve()),
                commits=[],
                dirty=GitDirty(files=["note.txt"], diffstat=""),
            ),
        ],
        truncations=[],
    )


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr("app.collectors.git._now", lambda _zone: moment)


def _limited(
    root: Path,
    *,
    per_file: int = 400,
    per_commit: int = 2000,
    per_day: int = 6000,
) -> Config:
    return Config(
        notes=NotesConfig(timezone="Europe/Moscow"),
        git=GitConfig(
            roots=[root],
            authors=[_AUTHOR],
            include_dirty=False,
            max_diff_lines_per_file=per_file,
            max_diff_lines_per_commit=per_commit,
            max_diff_lines_per_day=per_day,
        ),
    )


def test_skips_commit_by_other_author(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    when = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("UTC"))
    (repo / "theirs.txt").write_text("чужое\n")
    _git(repo, "add", "theirs.txt")
    _commit(repo, "чужая правка", when, email="other@example.com")
    (repo / "mine.txt").write_text("своё\n")
    _git(repo, "add", "mine.txt")
    sha = _commit(repo, "своя правка", when)

    collected = collect_git(_config(tmp_path, include_dirty=False), _day(2026, 10, 5))

    assert [(item.sha, item.message) for item in collected.repos[0].commits] == [
        (sha, "своя правка"),
    ]


def test_commit_is_taken_on_the_committer_day(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "note.txt").write_text("привет\n")
    _git(repo, "add", "note.txt")
    sha = _commit(
        repo,
        "вчера написал, сегодня закоммитил",
        datetime(2026, 10, 4, 9, 0, tzinfo=ZoneInfo("UTC")),
        committer=datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("UTC")),
    )

    collected = collect_git(_config(tmp_path, include_dirty=False), _day(2026, 10, 5))

    assert [(item.sha, item.committed_at) for item in collected.repos[0].commits] == [
        (sha, datetime(2026, 10, 5, 12, 0, tzinfo=_MOSCOW)),
    ]


def test_commit_over_line_budget_keeps_diffstat_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "big.txt").write_text("".join(f"line {index}\n" for index in range(40)))
    _git(repo, "add", "big.txt")
    sha = _commit(repo, "большой файл", datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("UTC")))

    collected = collect_git(_limited(tmp_path, per_commit=15), _day(2026, 10, 5))
    commit = collected.repos[0].commits[0]

    assert (commit.diff, commit.files, commit.diffstat, collected.truncations) == (
        None,
        ["big.txt"],
        _BIG_STAT,
        [f"repo: diff коммита {sha} длиннее 15 строк: оставлен только diffstat"],
    )


def test_excluded_and_binary_files_do_not_spend_the_commit_budget(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("keep\n")
    (repo / "data.bin").write_bytes(b"\x00\x01binary")
    bundled = repo / "docs" / "js" / "mermaid.min.js"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("".join(f"MERMAIDPAYLOAD {index}\n" for index in range(80)))
    vendored = repo / "pkg" / "vendor" / "sub" / "lib.go"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("".join(f"VENDORPAYLOAD {index}\n" for index in range(80)))
    _git(repo, "add", "app.py", "data.bin", "docs/js/mermaid.min.js", "pkg/vendor/sub/lib.go")
    _commit(repo, "шаблон", datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("UTC")))

    collected = collect_git(_limited(tmp_path, per_commit=30), _day(2026, 10, 5))
    commit = collected.repos[0].commits[0]
    added = [
        line
        for line in (commit.diff or "").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]

    assert (commit.files, added, collected.truncations) == (
        ["app.py", "data.bin", "docs/js/mermaid.min.js", "pkg/vendor/sub/lib.go"],
        ["+keep"],
        [],
    )


def test_long_file_diff_is_truncated_to_the_configured_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "big.txt").write_text("".join(f"line {index}\n" for index in range(40)))
    _git(repo, "add", "big.txt")
    _commit(repo, "длинный файл", datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("UTC")))

    collected = collect_git(_limited(tmp_path, per_file=10, per_commit=1000), _day(2026, 10, 5))
    commit = collected.repos[0].commits[0]

    assert (None if commit.diff is None else commit.diff.splitlines(), collected.truncations) == (
        [
            "diff --git a/big.txt b/big.txt",
            "new file mode 100644",
            "index 0000000..7fc45da",
            "--- /dev/null",
            "+++ b/big.txt",
            "@@ -0,0 +1,40 @@",
            "+line 0",
            "+line 1",
            "+line 2",
            "+line 3",
        ],
        ["repo: diff файла big.txt усечён до 10 строк"],
    )


def test_day_budget_drops_the_largest_commit_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "note.txt").write_text("привет\n")
    _git(repo, "add", "note.txt")
    small = _commit(repo, "короткий", datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("UTC")))
    (repo / "big.txt").write_text("".join(f"line {index}\n" for index in range(40)))
    _git(repo, "add", "big.txt")
    large = _commit(repo, "длинный", datetime(2026, 10, 5, 10, 0, tzinfo=ZoneInfo("UTC")))

    collected = collect_git(
        _limited(tmp_path, per_file=1000, per_commit=1000, per_day=20),
        _day(2026, 10, 5),
    )

    assert (
        [(item.sha, item.diff, item.diffstat) for item in collected.repos[0].commits],
        collected.truncations,
    ) == (
        [
            (small, _NOTE_DIFF, _COMMIT_STAT),
            (large, None, _BIG_STAT),
        ],
        [
            (
                f"repo: diff коммита {large} снят из-за лимита 20 строк на день: "
                "оставлен только diffstat"
            ),
        ],
    )


def test_real_projects_week_fits_the_day_budget() -> None:
    config = load_config()
    zone = ZoneInfo(config.notes.timezone)
    end = datetime.now(zone)
    collected = collect_git(config, _window(end - timedelta(days=7), end))
    total = sum(
        len(commit.diff.splitlines())
        for repo in collected.repos
        for commit in repo.commits
        if commit.diff is not None
    )

    assert total <= config.git.max_diff_lines_per_day
