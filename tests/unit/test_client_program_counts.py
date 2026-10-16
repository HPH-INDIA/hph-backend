"""Serialization regressions; run without the database integration fixtures."""
from datetime import date

import pytest

from app.reports.client_dashboard import restrict_client_cards
from app.reports.schemas import ClientDashboardSchema


@pytest.mark.parametrize("counts", [(13, 2, 11, 3), (0, 0, 0, 0)])
def test_client_program_counts_survive_all_dashboard_and_restricted_card_paths(counts):
    keys = ("kairon_pvp_count", "kairon_foundation_count", "manual_pvp_count", "manual_foundation_count")
    public_keys = ("kaironPvpCount", "kaironFoundationCount", "manualPvpCount", "manualFoundationCount")
    expected = dict(zip(public_keys, counts))
    day = dict(zip(keys, counts), date=date(2026, 10, 12), inside_minutes=480,
               productive_minutes=450, stage="M1", login_days=1)
    efficiency = dict(day, from_date=day["date"], to_date=day["date"], daily=[day.copy()])
    section = {"efficiency": efficiency}
    member = {"user_id": 1, "name": "Test Coder", "role_type": "employee", "efficiency": efficiency}
    result = ClientDashboardSchema().dump({"overview": section, "qa": section, "coders": section, "members": [member]})
    for row in [result[key]["efficiency"] for key in ("overview", "qa", "coders")] + [result["members"][0]["efficiency"]]:
        for payload in [row, row["daily"][0]]:
            assert {key: payload[key] for key in public_keys} == expected
            assert not {"insideMinutes", "productiveMinutes", "stage", "loginDays"}.intersection(payload)

    # The client coding/project cards also use a destructive allowlist before dumping.
    cards = [{"email": "private@example.test", "lead_id": 7, "last_working_day": None,
              "efficiency": {**efficiency, "daily": [day.copy()]}}]
    restrict_client_cards(cards)
    for payload in [cards[0]["efficiency"], cards[0]["efficiency"]["daily"][0]]:
        assert {key: payload[key] for key in keys} == dict(zip(keys, counts))
        assert not {"inside_minutes", "productive_minutes", "stage", "login_days"}.intersection(payload)
    assert not {"email", "lead_id", "last_working_day"}.intersection(cards[0])
