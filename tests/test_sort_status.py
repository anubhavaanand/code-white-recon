from unittest.mock import MagicMock

import pytest
from textual.widgets import Static

from core.models import PatentRecord
from tui.screens import SearchScreen
from tui.widgets.result_list import ResultList


@pytest.mark.parametrize("old_sort", ["", "  │  sort: date", "  │  sort: date sort: assignee"])
def test_changing_sort_replaces_the_previous_status(old_sort: str) -> None:
    screen = SearchScreen()
    screen._results = [PatentRecord(
        id="US123", title="Test", assignee="Test", dates={}, abstract="",
        claims=[], image_urls=[], status="active", family_id="FAM",
    )]
    prefix = "RECON  │  [bold]sort: date[/]  │  1 result  │  25ms"
    status = Static(prefix + old_sort)
    result_list = MagicMock()
    screen.query_one = MagicMock(
        side_effect=lambda selector, *args: result_list if selector is ResultList else status
    )
    screen._load_record = MagicMock()

    for mode in ("date", "assignee", "citation count", "custom (+30 citations)", "relevance", "relevance"):
        screen._sort_mode = mode
        screen._apply_sort()
        assert str(status.content) == f"{prefix}  │  sort: {mode}"
