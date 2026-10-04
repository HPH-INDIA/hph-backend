from types import SimpleNamespace
from decimal import Decimal
import pytest

from app.manual_daily_records import productivity as module

@pytest.mark.parametrize('target,overrides,expected', [
    (30, {}, '30.00'),
    (30, {'tech_issues_downtime_hours': 1, 'no_inventory_idle_time_hours': 1, 'leave_hours': 1, 'meetings': [{'type': 'Meeting', 'hours': '1'}, {'type': 'Huddle', 'hours': '0.5'}]}, '15.00'),
    (14, {'no_inventory_idle_time_hours': 2}, '10.50'),
    (30, {'meeting_type': 'Huddle', 'meeting_engagement_hours': 1}, '30.00'),
    (30, {'meeting_type': 'Training', 'meeting_engagement_hours': 1}, '26.25'),
    (30, {'meeting_type': 'One-O-One', 'meeting_engagement_hours': 1}, '26.25'),
    (30, {'meeting_engagement_hours': 1}, '26.25'),
    (30, {'leave_hours': 8}, '0.00'),
    (30, {'leave_hours': 10}, '0.00'),
    (None, {}, None),
    (0, {}, '0.00'),
])
def test_adjusted(target, overrides, expected):
    values = dict(tech_issues_downtime_hours=0, no_inventory_idle_time_hours=0, leave_hours=0, meeting_type=None, meeting_engagement_hours=0, meetings=None)
    values.update(overrides)
    actual = module.adjusted_cpd(SimpleNamespace(**values), target)
    assert actual == (Decimal(expected) if expected is not None else None)
