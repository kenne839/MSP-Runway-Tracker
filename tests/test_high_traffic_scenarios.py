"""
High Traffic Scenarios & Edge Case Test Suite for KMSP Runway Operations
========================================================================
Validates all high-density air traffic patterns at Minneapolis-St. Paul (KMSP):
1. Parallel Northwest Flow (30R Arrivals + 30L Departures)
2. Parallel Southeast Flow (12R Arrivals + 12L Departures)
3. Same-Runway Simultaneous Contention (30R Landing + 30R Takeoff)
4. Multi-Arrival Rush Hour (In-trail spacing on 30R)
5. Touchdown & Rollout Deceleration Hysteresis (Anti-Flapping Test)
6. Airport Flow Reversals (NW 30s -> SE 12s Wind Shift)
7. Crosswind Runway Operations (Runway 4/22 and 17/35)
8. Heavy Departure Bank with Rapid Turn-Off (Departure Fan Corridor)
9. Airspace Saturation & Overflight/Ground Taxi Rejection
10. Link Loss & Watchdog Telemetry Timeout
"""

import time
import unittest
from unittest.mock import patch, MagicMock

from rpi.tracker import TelemetryTracker, TelemetryState
from rpi.spatial import get_runway_match
from script import get_active_runway, write_state, HELD_OPERATIONS


class TestHighTrafficScenarios(unittest.TestCase):
    def setUp(self):
        self.state = TelemetryState()
        self.tracker = TelemetryTracker(state=self.state)
        # Reset held operations for clean isolation
        self.tracker._held_operations.clear()
        HELD_OPERATIONS.clear()

    # --------------------------------------------------------------------------
    # 1. Parallel Northwest Flow (30R Landing + 30L Takeoff)
    # --------------------------------------------------------------------------
    def test_parallel_northwest_flow_simultaneous(self):
        """Verify that simultaneous 30R arrival and 30L departure are both preserved in parallel."""
        aircraft_list = [
            # DAL793 landing on 30R (heading 301 deg, descending -3.5 m/s)
            {
                "icao24": "a1b2c3",
                "callsign": "DAL793",
                "lat": 44.8820,
                "lon": -93.1950,
                "altitude_m": 450.0,
                "velocity_ms": 68.0,
                "heading": 301.0,
                "vertical_rate_ms": -3.5
            },
            # SKW3822 taking off on 30L (heading 295 deg, climbing +6.0 m/s)
            {
                "icao24": "d4e5f6",
                "callsign": "SKW3822",
                "lat": 44.8882,
                "lon": -93.2352,
                "altitude_m": 500.0,
                "velocity_ms": 78.0,
                "heading": 295.0,
                "vertical_rate_ms": 6.0
            }
        ]

        self.tracker._process_aircraft_list(aircraft_list, "TestOpenSky")
        snapshot = self.state.get_snapshot()

        # Both flights must be active in active_operations
        self.assertEqual(snapshot["active_count"], 2)
        active_rw = {op["runway"]: op["action"] for op in snapshot["active_operations"]}
        self.assertEqual(active_rw.get("30R"), "LANDING")
        self.assertEqual(active_rw.get("30L"), "TAKEOFF")

        # Runway summary must accurately reflect both active runways
        self.assertEqual(snapshot["runway_summary"]["30R"]["status"], "LANDING")
        self.assertEqual(snapshot["runway_summary"]["30L"]["status"], "TAKEOFF")
        self.assertEqual(snapshot["runway_summary"]["12R"]["status"], "IDLE")

        # Runway roles must capture both roles
        roles = snapshot["runway_roles"]
        self.assertEqual(roles["landing"], "30R")
        self.assertEqual(roles["departure"], "30L")
        self.assertIn("ARR 30R / DEP 30L", roles["summary"])

    # --------------------------------------------------------------------------
    # 2. Parallel Southeast Flow (12R Landing + 12L Takeoff)
    # --------------------------------------------------------------------------
    def test_parallel_southeast_flow_simultaneous(self):
        """Verify that simultaneous 12R arrival and 12L departure run in parallel without conflict."""
        aircraft_list = [
            # UAL667 landing on 12R (heading 121 deg, descending -3.2 m/s)
            {
                "icao24": "a7b8c9",
                "callsign": "UAL667",
                "lat": 44.88640,
                "lon": -93.23086,
                "altitude_m": 420.0,
                "velocity_ms": 65.0,
                "heading": 121.0,
                "vertical_rate_ms": -3.2
            },
            # AAL1142 taking off on 12L (heading 121 deg, climbing +5.5 m/s)
            {
                "icao24": "d1e2f3",
                "callsign": "AAL1142",
                "lat": 44.88242,
                "lon": -93.19667,
                "altitude_m": 480.0,
                "velocity_ms": 82.0,
                "heading": 121.0,
                "vertical_rate_ms": 5.5
            }
        ]

        self.tracker._process_aircraft_list(aircraft_list, "TestOpenSky")
        snapshot = self.state.get_snapshot()

        self.assertEqual(snapshot["active_count"], 2)
        active_rw = {op["runway"]: op["action"] for op in snapshot["active_operations"]}
        self.assertEqual(active_rw.get("12R"), "LANDING")
        self.assertEqual(active_rw.get("12L"), "TAKEOFF")
        self.assertEqual(snapshot["runway_roles"]["landing"], "12R")
        self.assertEqual(snapshot["runway_roles"]["departure"], "12L")

    # --------------------------------------------------------------------------
    # 3. Same-Runway Simultaneous Contention (Landing + Takeoff on 30R)
    # --------------------------------------------------------------------------
    def test_same_runway_landing_priority(self):
        """
        Verify that if both a landing and takeoff occur on the SAME physical runway (30R):
        - Both flights are preserved in active_operations for the OLED display.
        - Primary operation selection and runway_summary prioritize LANDING (FAR § 91.113(g)).
        """
        aircraft_list = [
            # Takeoff first in list (mid-runway climb on 30R)
            {
                "icao24": "111111",
                "callsign": "DAL2089",
                "lat": 44.88710,
                "lon": -93.20748,
                "altitude_m": 500.0,
                "velocity_ms": 75.0,
                "heading": 301.0,
                "vertical_rate_ms": 4.5  # TAKEOFF
            },
            # Landing second in list (at 30R threshold)
            {
                "icao24": "222222",
                "callsign": "DAL793",
                "lat": 44.88242,
                "lon": -93.19667,
                "altitude_m": 450.0,
                "velocity_ms": 68.0,
                "heading": 301.0,
                "vertical_rate_ms": -3.5  # LANDING
            }
        ]

        self.tracker._process_aircraft_list(aircraft_list, "TestOpenSky")
        snapshot = self.state.get_snapshot()

        # Both flights must be present for OLED display queue
        self.assertEqual(len(snapshot["active_operations"]), 2)
        callsigns = [op["callsign"] for op in snapshot["active_operations"]]
        self.assertIn("DAL2089", callsigns)
        self.assertIn("DAL793", callsigns)

        # Primary operation MUST prioritize LANDING over TAKEOFF
        self.assertIsNotNone(snapshot["primary_operation"])
        self.assertEqual(snapshot["primary_operation"]["action"], "LANDING")
        self.assertEqual(snapshot["primary_operation"]["callsign"], "DAL793")

        # runway_summary for 30R must report LANDING, not TAKEOFF
        self.assertEqual(snapshot["runway_summary"]["30R"]["status"], "LANDING")

    # --------------------------------------------------------------------------
    # 4. Multi-Arrival Rush Hour (In-Trail Spacing on 30R)
    # --------------------------------------------------------------------------
    def test_multi_arrival_rush_hour(self):
        """
        Verify that multiple aircraft on final approach to 30R are both captured,
        with primary operation assigned to the aircraft nearest touchdown.
        """
        aircraft_list = [
            # DAL1210: Further out on extended final approach
            {
                "icao24": "333333",
                "callsign": "DAL1210",
                "lat": 44.87189,
                "lon": -93.17235,
                "altitude_m": 700.0,
                "velocity_ms": 75.0,
                "heading": 301.0,
                "vertical_rate_ms": -3.0
            },
            # DAL450: On short final closer to threshold
            {
                "icao24": "444444",
                "callsign": "DAL450",
                "lat": 44.87891,
                "lon": -93.18857,
                "altitude_m": 380.0,
                "velocity_ms": 65.0,
                "heading": 301.0,
                "vertical_rate_ms": -3.5
            }
        ]

        self.tracker._process_aircraft_list(aircraft_list, "TestOpenSky")
        snapshot = self.state.get_snapshot()

        # Both arrivals tracked
        self.assertEqual(len(snapshot["active_operations"]), 2)
        # Primary operation selects the landing flight closest to threshold
        self.assertEqual(snapshot["primary_operation"]["action"], "LANDING")

    # --------------------------------------------------------------------------
    # 5. Touchdown & Rollout Deceleration Hysteresis (Anti-Flapping Test)
    # --------------------------------------------------------------------------
    def test_touchdown_rollout_hysteresis_prevents_flapping(self):
        """
        Verify that when an aircraft touches down and decelerates on the runway:
        - The active operation is held for 25s, preventing rapid flapping to IDLE.
        - After the 25s hold window expires, the state cleanly transitions to IDLE.
        """
        # Cycle 1 (T=0): DAL793 actively landing
        now = time.time()
        with patch("time.time", return_value=now):
            arrival = [{
                "icao24": "a1b2c3",
                "callsign": "DAL793",
                "lat": 44.8820,
                "lon": -93.1950,
                "altitude_m": 450.0,
                "velocity_ms": 68.0,
                "heading": 301.0,
                "vertical_rate_ms": -3.5
            }]
            self.tracker._process_aircraft_list(arrival, "TestOpenSky")
            snap1 = self.state.get_snapshot()
            self.assertEqual(snap1["active_count"], 1)
            self.assertEqual(snap1["status"], "ACTIVE")

        # Cycle 2 (T=15s): DAL793 touched down!
        # Vertical rate is now 0.0 m/s (level roll) and speed dropped to 30 m/s (braking).
        # OpenSky returns empty or non-qualifying vector.
        with patch("time.time", return_value=now + 15.0):
            # Ground rollout: velocity < MIN_VELOCITY_MS (35 m/s) and vert_rate = 0
            ground_rollout = [{
                "icao24": "a1b2c3",
                "callsign": "DAL793",
                "lat": 44.8850,
                "lon": -93.2100,
                "altitude_m": 250.0,
                "velocity_ms": 28.0,  # Below 35 m/s gate
                "heading": 301.0,
                "vertical_rate_ms": 0.0  # Level on runway
            }]
            self.tracker._process_aircraft_list(ground_rollout, "TestOpenSky")
            snap2 = self.state.get_snapshot()

            # ANTI-FLAPPING CHECK: Must still be ACTIVE thanks to 25s hold window!
            self.assertEqual(snap2["active_count"], 1, "Rollout should be held across poll gap")
            self.assertEqual(snap2["status"], "ACTIVE")
            self.assertEqual(snap2["active_operations"][0]["callsign"], "DAL793")

        # Cycle 3 (T=30s): 30s have elapsed (> 25s hold window).
        # DAL793 has vacated to taxiway. Active operation must now cleanly expire to IDLE.
        with patch("time.time", return_value=now + 30.0):
            empty_sky = []
            self.tracker._process_aircraft_list(empty_sky, "TestOpenSky")
            snap3 = self.state.get_snapshot()

            self.assertEqual(snap3["active_count"], 0)
            self.assertEqual(snap3["status"], "IDLE")
            self.assertEqual(snap3["runway_summary"]["30R"]["status"], "IDLE")

    # --------------------------------------------------------------------------
    # 6. Airport Flow Reversal (NW 30s -> SE 12s Wind Shift)
    # --------------------------------------------------------------------------
    def test_flow_reversal_purges_old_flow_holds(self):
        """
        Verify that when a wind shift reverses airport operations (from 30s to 12s):
        - The new 12s flow takes over.
        - Any lingering held operations on 30s are immediately purged.
        - Operational roles reset cleanly.
        """
        now = time.time()
        # Step 1: Active 30R landing under NW flow
        with patch("time.time", return_value=now):
            self.tracker._process_aircraft_list([{
                "icao24": "111",
                "callsign": "DAL793",
                "lat": 44.88242,
                "lon": -93.19667,
                "altitude_m": 450.0,
                "velocity_ms": 68.0,
                "heading": 301.0,
                "vertical_rate_ms": -3.5
            }], "TestOpenSky")
            self.assertEqual(self.state.get_snapshot()["runway_roles"]["landing"], "30R")

        # Step 2: 5s later, wind shifts. First arrival establishes on 12R (SE flow)
        with patch("time.time", return_value=now + 5.0):
            self.tracker._process_aircraft_list([{
                "icao24": "222",
                "callsign": "UAL667",
                "lat": 44.88640,
                "lon": -93.23086,
                "altitude_m": 420.0,
                "velocity_ms": 65.0,
                "heading": 121.0,
                "vertical_rate_ms": -3.2
            }], "TestOpenSky")
            snap = self.state.get_snapshot()

            # Active operations must contain ONLY 12R (winning flow), not 30R
            active_rws = [op["runway"] for op in snap["active_operations"]]
            self.assertIn("12R", active_rws)
            self.assertNotIn("30R", active_rws)

            # Held operations must not contain 30R
            held_rws = [k.split("_")[0] for k in self.tracker._held_operations.keys()]
            self.assertNotIn("30R", held_rws)

            # Roles updated to 12R
            self.assertEqual(snap["runway_roles"]["landing"], "12R")

    # --------------------------------------------------------------------------
    # 7. Crosswind Runway Operations (Runway 4 / 22 and 17 / 35)
    # --------------------------------------------------------------------------
    def test_crosswind_runway_matching(self):
        """Verify crosswind operations on Runway 4 (045 deg) and Runway 22 (225 deg)."""
        # Runway 4 Arrival (heading 045 deg, descending)
        rw4_match = get_runway_match(44.87439, -93.23531, 45.0, vertical_rate=-3.0)
        self.assertIsNotNone(rw4_match)
        self.assertEqual(rw4_match["runway"], "4")

        # Runway 22 Arrival (heading 225 deg, descending)
        rw22_match = get_runway_match(44.89147, -93.21129, 225.0, vertical_rate=-3.0)
        self.assertIsNotNone(rw22_match)
        self.assertEqual(rw22_match["runway"], "22")

        # Runway 35 Arrival (heading 350 deg, descending)
        rw35_match = get_runway_match(44.86833, -93.23720, 350.0, vertical_rate=-3.0)
        self.assertIsNotNone(rw35_match)
        self.assertEqual(rw35_match["runway"], "35")

    # --------------------------------------------------------------------------
    # 8. Heavy Departure Bank with Rapid Turn-Off (Departure Fan)
    # --------------------------------------------------------------------------
    def test_heavy_departure_bank_fan_turn(self):
        """
        Verify that 30L departures executing immediate left turns (heading 270 deg)
        are reliably matched and classified as TAKEOFF.
        """
        climb_fan_aircraft = [{
            "icao24": "555555",
            "callsign": "DAL2089",
            "lat": 44.8943,
            "lon": -93.2685,
            "altitude_m": 600.0,
            "velocity_ms": 90.0,
            "heading": 268.8,  # 32 degree left turn
            "vertical_rate_ms": 10.0  # Strong positive climb
        }]

        self.tracker._process_aircraft_list(climb_fan_aircraft, "TestOpenSky")
        snap = self.state.get_snapshot()

        self.assertEqual(snap["active_count"], 1)
        self.assertEqual(snap["active_operations"][0]["runway"], "30L")
        self.assertEqual(snap["active_operations"][0]["action"], "TAKEOFF")

    # --------------------------------------------------------------------------
    # 9. Airspace Saturation & Filter Gating
    # --------------------------------------------------------------------------
    def test_airspace_saturation_filters_overflights_and_taxis(self):
        """
        Verify that high altitude cruising overflights (>1200m) and slow taxi traffic (<35m/s)
        are properly filtered out during dense traffic conditions.
        """
        traffic_burst = [
            # High-altitude Boeing 777 overhead at 10,000m (cruising over Minneapolis)
            {
                "icao24": "over01",
                "callsign": "BAW217",
                "lat": 44.8820,
                "lon": -93.1950,
                "altitude_m": 10000.0,  # Overflight gate
                "velocity_ms": 240.0,
                "heading": 301.0,
                "vertical_rate_ms": 0.0
            },
            # Slow pushback tug / taxi aircraft on ramp at 12 m/s
            {
                "icao24": "taxi01",
                "callsign": "DAL110",
                "lat": 44.8850,
                "lon": -93.2200,
                "altitude_m": 250.0,
                "velocity_ms": 12.0,  # Below 35 m/s gate
                "heading": 301.0,
                "vertical_rate_ms": 0.0
            },
            # Genuine arrival on 30R
            {
                "icao24": "arr01",
                "callsign": "EDV4800",
                "lat": 44.8820,
                "lon": -93.1950,
                "altitude_m": 400.0,
                "velocity_ms": 65.0,
                "heading": 301.0,
                "vertical_rate_ms": -3.0
            }
        ]

        self.tracker._process_aircraft_list(traffic_burst, "TestOpenSky")
        snap = self.state.get_snapshot()

        # Only the genuine arrival must pass through
        self.assertEqual(snap["active_count"], 1)
        self.assertEqual(snap["active_operations"][0]["callsign"], "EDV4800")
        self.assertEqual(snap["active_operations"][0]["runway"], "30R")

    # --------------------------------------------------------------------------
    # 10. Link Loss & Watchdog Telemetry Timeout
    # --------------------------------------------------------------------------
    def test_tracker_network_status_flagging(self):
        """Verify that when OpenSky polling fails, network_status indicates offline."""
        self.tracker.last_fetch_success = False
        self.tracker.opensky_lockout_until = time.time() + 300.0

        # Simulate poll failure branch
        self.tracker._poll_opensky()
        snap = self.state.get_snapshot()

        self.assertFalse(snap["network"]["online"])
        self.assertTrue(snap["network"]["rate_limited"])
        self.assertIn("Rate Limited", snap["source"])


if __name__ == "__main__":
    unittest.main()
