"""Render the candidate table and collect the user's selection."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from .models import Candidate


def format_timestamp(seconds: float) -> str:
    """Format seconds as ``m:ss`` or ``h:mm:ss``."""
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def show_candidates(candidates: list[Candidate], console: Console) -> None:
    """Print a rich table of clip candidates."""
    table = Table(title="Clip candidates", show_lines=True)
    table.add_column("#", justify="right", style="bold cyan", no_wrap=True)
    table.add_column("Title", style="bold")
    table.add_column("Dur", justify="right", no_wrap=True)
    table.add_column("Range", justify="right", style="dim", no_wrap=True)
    table.add_column("Category", no_wrap=True)
    table.add_column("Hook")
    for index, candidate in enumerate(candidates, 1):
        table.add_row(
            str(index),
            candidate.title,
            f"{candidate.duration:.0f}s",
            f"{format_timestamp(candidate.start)}-{format_timestamp(candidate.end)}",
            candidate.category.value,
            candidate.hook,
        )
    console.print(table)


def parse_selection(raw: str, count: int) -> list[int]:
    """Parse a selection string into 0-based indices.

    Accepts comma-separated indices, ranges (``1-3``) and ``all``.
    Raises :class:`ValueError` on anything malformed or out of range.
    """
    text = raw.strip().lower()
    if text == "all":
        return list(range(count))

    numbers: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, _, right = part.partition("-")
            try:
                lo, hi = int(left), int(right)
            except ValueError:
                raise ValueError(f"invalid range: '{part}'") from None
            if lo > hi:
                lo, hi = hi, lo
            numbers.extend(range(lo, hi + 1))
        else:
            try:
                numbers.append(int(part))
            except ValueError:
                raise ValueError(f"not a number: '{part}'") from None

    if not numbers:
        raise ValueError("empty selection")

    result: list[int] = []
    seen: set[int] = set()
    for n in numbers:
        if n < 1 or n > count:
            raise ValueError(f"index {n} is out of range 1-{count}")
        if n not in seen:
            seen.add(n)
            result.append(n - 1)
    return result


def select_candidates(
    candidates: list[Candidate], console: Console
) -> list[Candidate] | None:
    """Show the table and prompt the user.

    Returns the chosen candidates, or ``None`` if the user quit.
    """
    show_candidates(candidates, console)
    while True:
        raw = Prompt.ask(
            "Select clips ([cyan]1,3[/], [cyan]1-3[/], [cyan]all[/], "
            "[cyan]q[/] to quit)",
            default="all",
            console=console,
        )
        if raw.strip().lower() == "q":
            return None
        try:
            indices = parse_selection(raw, len(candidates))
        except ValueError as exc:
            console.print(f"[red]Invalid selection:[/] {exc}")
            continue
        return [candidates[i] for i in indices]
