"""The import page's one next step, per batch status."""

import pytest

from crm.leadui.import_steps import ALL_DONE, next_step

pytestmark = pytest.mark.unit


def step(status, blocks=False, conflicts=0, inout=False):
    return next_step(status=status, has_blocks=blocks, open_blocking=conflicts, is_inout=inout)


@pytest.mark.parametrize(
    ("status", "conflicts", "action", "stage"),
    [
        (None, 0, None, 0),
        ("PREVIEW", 0, "preview", 1),
        ("AWAITING_RESOLUTION", 3, "conflicts", 2),
        ("AWAITING_RESOLUTION", 0, "confirm", 3),
        ("COMMITTING", 0, "commit", 4),
        ("FAILED_RETRYABLE", 0, "commit", 4),
        ("PAUSED_QUOTA", 0, "commit", 4),
        ("COMMITTED", 0, None, ALL_DONE),
        ("CANCELLED", 0, None, 0),
        ("ROLLED_BACK", 0, None, 0),
    ],
)
def test_each_status_offers_one_action(status, conflicts, action, stage):
    result = step(status, conflicts=conflicts)
    assert (result["action"], result["stage"]) == (action, stage)
    assert result["title"] and result["help"]


def test_a_blocked_file_offers_no_action_only_the_way_out():
    assert step("PREVIEW", blocks=True)["action"] is None
    assert step("AWAITING_RESOLUTION", blocks=True, conflicts=2)["action"] is None


def test_the_conflict_count_is_in_the_title():
    assert "1,234" in step("AWAITING_RESOLUTION", conflicts=1234)["title"]


def test_a_finished_inout_points_at_the_next_file():
    assert step("COMMITTED", inout=True)["action"] == "next_file"
