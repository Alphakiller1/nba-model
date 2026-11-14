"""Official designations and each listed player's value (owner 2026-11-12)."""
from datetime import datetime
from pathlib import Path

from nbamodel import export, injury_value, live, official_report

PDF = Path(__file__).parent / "fixtures" / "official_injury_report_2026-11-12_0400PM.pdf"


def test_official_report_reads_every_designation_and_reason():
    rows = official_report.parse(PDF.read_bytes())
    by = {(r.team, r.name): r for r in rows}
    assert by[("PHI", "Joel Embiid")].status == "Questionable"
    assert by[("PHI", "Joel Embiid")].reason == "Injury/Illness - Right Foot Plantar Fascia Injury Management"
    assert by[("DAL", "Morez Johnson Jr.")].status == "Probable"
    assert by[("NO", "Trey Murphy III")].status == "Doubtful"
    assert by[("WSH", "Reece Beekman")].status == "Out", "the team label carries down its block"
    assert by[("DET", "Elijah Harkless")].reason == "G League - Two-Way"
    assert by[("DET", "Elijah Harkless")].category == "g-league"
    statuses = {r.status for r in rows}
    assert statuses <= set(official_report.STATUSES)
    assert {"Out", "Doubtful", "Questionable", "Probable", "Available"} <= statuses


def test_names_and_slots():
    assert official_report.display_name("Johnson Jr., Morez") == "Morez Johnson Jr."
    assert official_report.display_name("de Larrea, Sergio") == "Sergio de Larrea"
    assert official_report.name_key("Morez Johnson Jr.") == official_report.name_key("Morez Johnson")
    at = datetime(2026, 11, 12, 16, 37, tzinfo=official_report.ET)
    assert official_report.slot(at) == "04_30PM"
    assert official_report.url_for(at).endswith("Injury-Report_2026-11-12_04_30PM.pdf")


def test_public_list_lays_the_official_designation_over_espn():
    espn = [live.Injury("PHI", "1", "Joel Embiid", "Day-To-Day", "Right Foot", "note", "2026-11-12", ""),
            live.Injury("PHI", "2", "Kelly Oubre Jr.", "Out", "Knee", "", "", "")]
    official = {"1": official_report.Row("PHI", "TOR@PHI", "Joel Embiid", "Questionable", "Injury/Illness - Foot"),
                "3": official_report.Row("PHI", "TOR@PHI", "Tyrese Maxey", "Probable", "Injury/Illness - Ankle"),
                "4": official_report.Row("PHI", "TOR@PHI", "Two Way", "Available", "G League - Two-Way")}
    rows = export._public_injuries("PHI", espn, official, {"3": "Tyrese Maxey"}, {"1": 2.08, "3": 2.88})
    by = {r["player_id"]: r for r in rows}
    assert by["1"]["status"] == "Questionable" and by["1"]["source"] == "official" and by["1"]["value"] == 2.08
    assert by["2"]["status"] == "Out" and by["2"]["source"] == "espn"
    assert by["3"]["status"] == "Probable" and by["3"]["player_name"] == "Tyrese Maxey"
    assert "4" not in by, "Available is not a designation"
    assert [r["status"] for r in rows] == ["Out", "Questionable", "Probable"]


def test_value_is_role_plus_star_production():
    coef = {"role": 1.5, "star": 0.0334}
    role_player = injury_value.value(25.0, 8.0, coef)
    star = injury_value.value(34.0, 30.0, coef)
    assert round(role_player, 2) == round(1.5 * 25 / 48 + 0.0334 * 64 / 10, 2)
    assert star > 3 * role_player, "a star's absence moves a line far more than a role player's"
    assert injury_value.value(20.0, -3.0, coef) == 1.5 * 20 / 48, "negative production adds nothing"


def test_production_reads_recent_appearances_then_last_season():
    def line(eid, pid, minutes, pts):
        return {"event_id": eid, "athlete_id": pid, "minutes": str(minutes), "pts": str(pts)}
    now = [line(f"e{i}", "a", 30, 20) for i in range(12)]
    prev = [line("p1", "b", 30, 10)]
    prod = injury_value.Production(now, prev, {f"e{i}": float(i) for i in range(12)})
    assert prod.of("a") == (30.0, 20.0)
    assert prod.of("b") == (27.0, 10.0), "last season's minutes at 90%"
    assert prod.of("c") is None
