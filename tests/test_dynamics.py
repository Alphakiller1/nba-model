"""Statistical direction, sample accounting and out-of-time model contracts."""

import copy
import math
import unittest
from datetime import datetime, timedelta, timezone

from nbamodel import dynamics as d
from nbamodel import dynamics_validation as validation


def observation(index=0, season=2027, three=10, allowed_three=10, duration=48, possessions=100):
    stamp = (datetime(season - 1, 11, 1, tzinfo=timezone.utc) + timedelta(days=index * 5)).isoformat()
    row = {
        "game_id": str(index),
        "season": season,
        "date": stamp,
        "available_at": stamp,
        "team": "A",
        "opponent": "B",
        "possessions": possessions,
        "duration_minutes": duration,
    }
    for prefix, threes in (("", three), ("d_", allowed_three)):
        row.update(
            {
                prefix + k: v
                for k, v in {
                    "fgm": 30 + threes,
                    "fga": 80,
                    "tpm": threes,
                    "tpa": 30,
                    "ftm": 10,
                    "fta": 12,
                    "tov": 10,
                    "orb": 10,
                    "drb": 30,
                    "pts": 70 + 3 * threes,
                }.items()
            }
        )
    return row


class DynamicsTests(unittest.TestCase):
    def test_hot_and_cold_shooting_have_opposite_net_directions(self):
        prior = [observation(season=2026)] * 20
        hot = d.shooting([observation(three=20)], prior, prior, {"B": prior})
        cold = d.shooting([observation(three=0)], prior, prior, {"B": prior})
        hot_three = next(r for r in hot["components"] if (r["side"], r["shot_kind"]) == ("offense", "three"))
        cold_three = next(r for r in cold["components"] if (r["side"], r["shot_kind"]) == ("offense", "three"))
        self.assertLess(hot_three["points_per100_shift"], 0)
        self.assertGreater(cold_three["points_per100_shift"], 0)
        self.assertEqual(hot["forecast_weight"], 0)

    def test_allowed_hot_shooting_is_progression_pressure(self):
        prior = [observation(season=2026)] * 20
        result = d.shooting([observation(allowed_three=20)], prior, prior, {"B": prior})
        component = next(r for r in result["components"] if (r["side"], r["shot_kind"]) == ("allowed", "three"))
        self.assertGreater(component["points_per100_shift"], 0)
        self.assertEqual(component["baseline_policy"], "opponents' prior offense")
        self.assertEqual(component["tone"], "unknown")  # only 30 attempts

    def test_beta_handles_zero_perfect_and_empty_samples(self):
        for makes, attempts in ((0, 0), (0, 1), (1, 1), (0, 100), (100, 100)):
            posterior = d.posterior(makes, attempts, 0.4, 100)
            self.assertTrue(0 < posterior["posterior_low95"] < posterior["posterior_high95"] < 1)
        with self.assertRaises(ValueError):
            d.posterior(2, 1, 0.4, 100)
        self.assertIsNone(d.rates([])["pp100"])

    def test_roster_continuity_reduces_departed_roster_reference(self):
        own = [observation(three=20, season=2026)] * 10
        league = [observation(season=2026)] * 100
        a = d.shooting([observation()], own, league, {}, continuity=1)
        b = d.shooting([observation()], own, league, {}, continuity=0)

        def get(payload):
            return next(
                r["baseline"] for r in payload["components"] if r["side"] == "offense" and r["shot_kind"] == "three"
            )

        self.assertGreater(get(a), get(b))

    def test_available_at_and_strict_cutoff_override_game_timestamp(self):
        a, b = observation(), observation(1)
        a["available_at"] = b["date"]
        self.assertEqual(d.before([a, b], b["date"]), [])
        bank = d.build([a, b], 2027, "2026-11-06T00:00:00Z", 48, ["A", "missing"])
        self.assertEqual(bank["A"]["games"], 0)
        self.assertEqual(bank["missing"]["sample_state"], "thin")

    def test_overtime_does_not_inflate_regulation_pace(self):
        for regulation in (40, 48):
            a = observation(duration=regulation, possessions=70)
            b = observation(1, duration=regulation + 5, possessions=70 * (regulation + 5) / regulation)
            missing = observation(2, duration=None)
            pace = d.pace([a, b, missing], [a], [a], regulation)
            self.assertAlmostEqual(pace["posterior_per_regulation"], 70)
            self.assertEqual(pace["duration_known_games"], 2)
            self.assertTrue(math.isfinite(pace["mean_se"]))

    def test_recent_process_uses_disjoint_comparison_and_style_is_neutral(self):
        rows = [observation(i) for i in range(8)]
        for row in rows[-5:]:
            row["tov"] = 2
        result = d.process(rows)
        self.assertEqual((result["recent_games"], result["earlier_games"]), (5, 3))
        turnover = next(r for r in result["rows"] if r["key"] == "turnover_rate" and r["side"] == "offense")
        self.assertAlmostEqual(turnover["change"], -0.08)
        self.assertEqual(turnover["tone"], "favorable")
        self.assertTrue(all(r["tone"] == "neutral" for r in result["rows"] if r["key"] == "three_attempt_share"))
        self.assertEqual(d.process(rows[:7])["state"], "thin")

    def test_personnel_denominators_and_direction_do_not_mix(self):
        rows = [
            {**observation(i), "own_personnel": "2G-2F-1C", "opponent_personnel": "3G-1F-1C", "points": 110}
            for i in range(5)
        ]
        result = d.personnel(rows, "2027-01-01T00:00Z")
        for side, team in (("offense", "A"), ("defense", "B")):
            for basis in ("own", "opponent"):
                cells = [r for r in result[team] if r["side"] == side and r["basis"] == basis]
                self.assertAlmostEqual(sum(r["usage"] for r in cells), 1)
                self.assertEqual(cells[0]["possessions"], 500)
                self.assertAlmostEqual(cells[0]["ppp"], 1.1)
                self.assertTrue(cells[0]["sample_qualified"])
        matchup = d.personnel_matchup({"personnel_formations": result["A"]}, {"personnel_formations": result["B"]})
        self.assertEqual(matchup[0]["personnel"], "3G-1F-1C")
        self.assertEqual(matchup[0]["forecast_weight"], 0)

    def test_validation_freezes_calibration_and_never_reuses_team_targets(self):
        rows = [observation(i, year, three=10 + i % 4) for year in (2026, 2027) for i in range(28)]
        report, predictions = validation.evaluate(rows, 2027, "2027-06-01T00:00Z", 48)
        changed = copy.deepcopy(rows)
        for row in changed:
            if row["season"] == 2027:
                row["tpm"] = 1
                row["fgm"] = 31
        later_report, _ = validation.evaluate(changed, 2027, "2027-06-01T00:00Z", 48)
        self.assertEqual(report["calibrated_shooting_weight"], later_report["calibrated_shooting_weight"])
        used = set()
        for row in predictions:
            self.assertLess(d.utc(row["train_through"]), d.utc(row["cutoff"]))
            for game in row["target_ids"].split("|"):
                key = row["season"], row["team"], game
                self.assertNotIn(key, used)
                used.add(key)
        _, earlier = validation.evaluate(rows, 2027, "2026-01-01T00:00Z", 48)
        self.assertTrue(all(d.utc(r["target_through"]) < d.utc("2026-01-01T00:00Z") for r in earlier))


if __name__ == "__main__":
    unittest.main()
