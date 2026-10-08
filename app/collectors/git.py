"""Сбор коммитов и незакоммиченной работы из локальных репозиториев."""

import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from app.config import Config
from app.summary.models import GitCommit, GitDirty, GitRepo, Window

# Стабильный diff: не зависим от abbrev, цвета и внешних diff-драйверов пользователя.
_GIT = (
    "git",
    "-c",
    "core.abbrev=7",
    "-c",
    "color.ui=false",
    "-c",
    "diff.mnemonicPrefix=false",
    "-c",
    "diff.noprefix=false",
)


class _CommitRef(NamedTuple):
    """Коммит до чтения diff: хеш, момент и автор."""

    sha: str
    moment: datetime
    email: str


class CollectedGit(BaseModel):
    """Результат сборщика. Запись дампа и маскирование — не его работа."""

    model_config = ConfigDict(extra="forbid")

    repos: list[GitRepo]
    truncations: list[str]


def collect_git(
    config: Config,
    window: Window,
    *,
    include_uncommitted: bool | None = None,
) -> CollectedGit:
    """Собрать коммиты авторов из конфига и незакоммиченные изменения.

    Время коммита переводится в `notes.timezone`, чтобы граница дня совпала
    с заметкой. Незакоммиченное берётся, когда `include_uncommitted` истинно:
    команда решает это в момент захвата окна. `None` оставляет прежнюю
    проверку по текущим часам — для прямого вызова сборщика. Битый репозиторий
    без таких правок пропускается. Усечения — человекочитаемые строки, не
    признак пропуска репозитория.

    Args:
        config: Загруженные настройки. Лимиты и корни берутся отсюда.
        window: Закрытый интервал сбора.
        include_uncommitted: Включать незакоммиченные файлы. `None` — если окно
            содержит текущий момент.

    Returns:
        Репозитории, где нашлась работа, и записи об усечении diff.
    """
    zone = ZoneInfo(config.notes.timezone)
    repos: list[GitRepo] = []
    truncations: list[str] = []
    for path in _discover(config.git.roots, config.git.max_depth):
        collected = _collect_repo(
            path,
            config,
            window,
            zone=zone,
            include_uncommitted=include_uncommitted,
        )
        if collected is None:
            continue
        repo, repo_truncations = collected
        repos.append(repo)
        truncations.extend(repo_truncations)
    truncations.extend(_limit_day(repos, config.git.max_diff_lines_per_day))
    return CollectedGit(repos=repos, truncations=truncations)


def _limit_day(repos: list[GitRepo], limit: int) -> list[str]:
    notes: list[str] = []
    while _day_lines(repos) > limit:
        largest = _largest_commit(repos)
        if largest is None:
            break
        repo_name, commit = largest
        commit.diff = None
        notes.append(
            f"{repo_name}: diff коммита {commit.sha} снят из-за лимита "
            f"{limit} строк на день: оставлен только diffstat",
        )
    return notes


def _day_lines(repos: list[GitRepo]) -> int:
    return sum(_diff_lines(commit) for repo in repos for commit in repo.commits)


def _largest_commit(repos: list[GitRepo]) -> tuple[str, GitCommit] | None:
    candidates = [
        (repo.path, commit) for repo in repos for commit in repo.commits if _diff_lines(commit) > 0
    ]
    if not candidates:
        return None
    path, commit = max(
        candidates,
        key=lambda item: (_diff_lines(item[1]), item[1].committed_at),
    )
    return Path(path).name, commit


def _diff_lines(commit: GitCommit) -> int:
    if commit.diff is None:
        return 0
    return len(commit.diff.splitlines())


def _discover(roots: list[Path], max_depth: int) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        _walk(root, max_depth, 0, seen=seen, found=found)
    return found


def _walk(
    directory: Path,
    max_depth: int,
    depth: int,
    *,
    seen: set[Path],
    found: list[Path],
) -> None:
    resolved = _resolve_dir(directory)
    if resolved is None or not _should_enter(resolved, depth, max_depth, seen=seen):
        return
    seen.add(resolved)
    _record_repo(resolved, found)
    if depth < max_depth:
        for child in _child_dirs(resolved):
            _walk(child, max_depth, depth + 1, seen=seen, found=found)


def _resolve_dir(directory: Path) -> Path | None:
    try:
        resolved = directory.resolve()
    except OSError:
        return None
    if resolved.is_dir():
        return resolved
    return None


def _should_enter(resolved: Path, depth: int, max_depth: int, *, seen: set[Path]) -> bool:
    return depth <= max_depth and resolved not in seen


def _record_repo(resolved: Path, found: list[Path]) -> None:
    if (resolved / ".git").exists():
        found.append(resolved)


def _child_dirs(directory: Path) -> list[Path]:
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    return [entry for entry in entries if entry.name != ".git" and entry.is_dir()]


def _collect_repo(
    path: Path,
    config: Config,
    window: Window,
    *,
    zone: ZoneInfo,
    include_uncommitted: bool | None,
) -> tuple[GitRepo, list[str]] | None:
    records = _commits_in_window(path, window)
    dirty = _dirty(
        path,
        config,
        window,
        zone=zone,
        include_uncommitted=include_uncommitted,
    )
    if records is None:
        if dirty is None:
            return None
        return GitRepo(path=str(path), commits=[], dirty=dirty), []
    commits, notes = _read_commits(path, records, config, zone=zone)
    if not commits and dirty is None:
        return None
    return GitRepo(path=str(path), commits=commits, dirty=dirty), notes


def _dirty(
    path: Path,
    config: Config,
    window: Window,
    *,
    zone: ZoneInfo,
    include_uncommitted: bool | None,
) -> GitDirty | None:
    if include_uncommitted is None:
        include_uncommitted = _covers_now(window, zone)
    if not config.git.include_dirty or not include_uncommitted:
        return None
    return _read_dirty(path)


def _covers_now(window: Window, zone: ZoneInfo) -> bool:
    moment = _now(zone)
    return window.from_ <= moment <= window.to


def _now(zone: ZoneInfo) -> datetime:
    return datetime.now(zone)


def _commits_in_window(repo: Path, window: Window) -> list[_CommitRef] | None:
    since = (window.from_ - timedelta(seconds=1)).isoformat()
    until = (window.to + timedelta(seconds=1)).isoformat()
    text = _git(
        repo,
        "log",
        "HEAD",
        "--branches",
        "--remotes",
        "--tags",
        "--reverse",
        f"--since={since}",
        f"--until={until}",
        "--format=%H%x1f%cI%x1f%ae%x1e",
    )
    if text is None:
        return None
    return [parsed for chunk in text.split("\x1e") if (parsed := _parse_commit(chunk, window))]


def _parse_commit(chunk: str, window: Window) -> _CommitRef | None:
    cleaned = chunk.strip()
    if not cleaned:
        return None
    sha, authored, email = cleaned.split("\x1f")
    moment = datetime.fromisoformat(authored)
    if moment.tzinfo is None:
        return None
    if window.from_ <= moment <= window.to:
        return _CommitRef(sha, moment, email)
    return None


def _read_commits(
    repo: Path,
    records: list[_CommitRef],
    config: Config,
    *,
    zone: ZoneInfo,
) -> tuple[list[GitCommit], list[str]]:
    commits: list[GitCommit] = []
    notes: list[str] = []
    for sha, moment, email in records:
        limited, commit_notes = _one_commit(repo, _CommitRef(sha, moment, email), config, zone=zone)
        if limited is None:
            continue
        commits.append(limited)
        notes.extend(commit_notes)
    return commits, notes


def _one_commit(
    repo: Path,
    record: _CommitRef,
    config: Config,
    *,
    zone: ZoneInfo,
) -> tuple[GitCommit | None, list[str]]:
    sha, moment, email = record
    if email not in config.git.authors:
        return None, []
    commit = _read_commit(repo, sha, moment, zone=zone)
    if commit is None:
        return None, []
    filtered, notes = _limit_files(
        commit,
        config.git.exclude_globs,
        config.git.max_diff_lines_per_file,
        repo_name=repo.name,
    )
    limited, note = _limit_commit(filtered, config.git.max_diff_lines_per_commit, repo.name)
    if note is not None:
        notes.append(note)
    return limited, notes


def _limit_files(
    commit: GitCommit,
    patterns: list[str],
    per_file: int,
    *,
    repo_name: str,
) -> tuple[GitCommit, list[str]]:
    if commit.diff is None:
        return commit, []
    kept: list[str] = []
    notes: list[str] = []
    for path, chunk in _split_patches(commit.diff):
        if not _keep_patch(path, chunk, patterns):
            continue
        shortened, note = _truncate_file(chunk, path, per_file, repo_name=repo_name)
        kept.append(shortened)
        if note is not None:
            notes.append(note)
    return commit.model_copy(update={"diff": "".join(kept)}), notes


def _truncate_file(
    chunk: str,
    path: str,
    limit: int,
    *,
    repo_name: str,
) -> tuple[str, str | None]:
    lines = chunk.splitlines()
    if len(lines) <= limit:
        text = chunk if chunk.endswith("\n") else f"{chunk}\n"
        return text, None
    note = f"{repo_name}: diff файла {path} усечён до {limit} строк"
    return "\n".join(lines[:limit]) + "\n", note


def _keep_patch(path: str, chunk: str, patterns: list[str]) -> bool:
    return not _excluded(path, patterns) and not _binary_patch(chunk)


def _split_patches(patch: str) -> list[tuple[str, str]]:
    return [
        (_patch_path(chunk), chunk if chunk.endswith("\n") else f"{chunk}\n")
        for chunk in re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
        if chunk.strip()
    ]


def _patch_path(chunk: str) -> str:
    rest = chunk.splitlines()[0].removeprefix("diff --git ")
    if rest.startswith('"'):
        destination = rest.split(" ")[-1].strip('"')
        return destination.removeprefix("b/")
    marker = " b/"
    index = rest.rfind(marker)
    if index == -1:
        return rest
    return rest[index + len(marker) :]


def _excluded(path: str, patterns: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(_glob_match(candidate, pattern) for pattern in patterns)


def _glob_match(candidate: PurePosixPath, pattern: str) -> bool:
    if candidate.match(pattern):
        return True
    # `vendor/*` и `dist/*` — всё дерево, в том числе не в корне репозитория.
    if not pattern.endswith("/*"):
        return False
    return pattern[:-2] in candidate.parts[:-1]


def _binary_patch(chunk: str) -> bool:
    markers = ("Binary files ", "GIT binary patch")
    return any(line.startswith(markers) for line in chunk.splitlines())


def _limit_commit(commit: GitCommit, limit: int, repo_name: str) -> tuple[GitCommit, str | None]:
    if _diff_lines(commit) <= limit:
        return commit, None
    note = f"{repo_name}: diff коммита {commit.sha} длиннее {limit} строк: оставлен только diffstat"
    return commit.model_copy(update={"diff": None}), note


def _read_commit(repo: Path, sha: str, moment: datetime, *, zone: ZoneInfo) -> GitCommit | None:
    message = _git(repo, "log", "-1", "--format=%B", sha)
    names = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "--root", "-r", "-z", sha)
    stat = _git(repo, "show", "--no-ext-diff", "--no-textconv", "--format=", "--stat", sha)
    patch = _git(repo, "show", "--no-ext-diff", "--no-textconv", "--format=", "--patch", sha)
    if message is None or names is None or stat is None or patch is None:
        return None
    return GitCommit(
        sha=sha,
        committed_at=moment.astimezone(zone),
        message=message.rstrip("\n"),
        files=[name for name in names.split("\0") if name],
        diffstat=stat.strip("\n"),
        diff=patch,
    )


def _read_dirty(repo: Path) -> GitDirty | None:
    status = _git(repo, "status", "--porcelain")
    if status is None or not status.strip():
        return None
    stat = _git(repo, "diff", "--stat", "HEAD")
    return GitDirty(
        files=[_porcelain_path(line) for line in status.splitlines() if line.strip()],
        diffstat="" if stat is None else stat.strip("\n"),
    )


def _porcelain_path(line: str) -> str:
    payload = line[3:]
    if " -> " in payload:
        payload = payload.rsplit(" -> ", 1)[1]
    if payload.startswith('"') and payload.endswith('"'):
        return payload[1:-1]
    return payload


def _git(repo: Path, *args: str) -> str | None:
    # argv списком, без shell: путь репозитория не интерпретируется оболочкой.
    # GIT_TERMINAL_PROMPT=0: недоступный remote не должен ждать пароль.
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(  # noqa: S603
        [*_GIT, "-C", str(repo), "--no-pager", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.returncode != 0:
        return None
    return result.stdout
