"""F1 25 / F1 25 2026 Season Pack UDP packet parsing.

Layouts derived from the official EA UDP specs. Two wire formats are
supported, selected by the packetFormat field in every header:

  2025 -> base F1 25 layout, 22 car slots
  2026 -> "2026 Season Pack" layout, 24 car slots, some structs changed
          (g-forces are int16, engine temp is uint8, Team ids are uint16,
          new CarTelemetry2 packet id 16)

All packets are little-endian and packed (no padding).
"""

import struct

HEADER = struct.Struct("<HBBBBBQfIIBB")  # 29 bytes

# Packet ids
MOTION = 0
SESSION = 1
LAP_DATA = 2
EVENT = 3
PARTICIPANTS = 4
CAR_SETUPS = 5
CAR_TELEMETRY = 6
CAR_STATUS = 7
SESSION_HISTORY = 11
TIME_TRIAL = 14
CAR_TELEMETRY2 = 16


class Header:
    __slots__ = ("packet_format", "game_year", "packet_id", "session_uid",
                 "session_time", "frame", "overall_frame", "player_car_index")

    def __init__(self, data):
        (self.packet_format, self.game_year, _maj, _min, _pver,
         self.packet_id, self.session_uid, self.session_time,
         self.frame, self.overall_frame,
         self.player_car_index, _secondary) = HEADER.unpack_from(data, 0)


def num_cars(fmt):
    return 24 if fmt >= 2026 else 22


# ---------------------------------------------------------------- motion

_MOTION_CAR_2026 = struct.Struct("<ffffffhhhhhhhhhfff")  # 54 B, g-forces int16
_MOTION_CAR_2025 = struct.Struct("<ffffffhhhhhhffffff")  # 60 B, g-forces float


def parse_motion(data, fmt, wanted):
    """Return {car_idx: (x, y, z, g_lat, g_long)} for wanted car indices."""
    st = _MOTION_CAR_2026 if fmt >= 2026 else _MOTION_CAR_2025
    out = {}
    for idx in wanted:
        off = HEADER.size + idx * st.size
        if off + st.size > len(data):
            continue
        v = st.unpack_from(data, off)
        if fmt >= 2026:
            g_lat, g_long = v[12] / 100.0, v[13] / 100.0
        else:
            g_lat, g_long = v[12], v[13]
        out[idx] = (v[0], v[1], v[2], g_lat, g_long)
    return out


# ---------------------------------------------------------------- session

_SESSION_LEAD = struct.Struct("<BbbBHBbB")


def parse_session(data):
    (weather, track_temp, air_temp, total_laps, track_length,
     session_type, track_id, formula) = _SESSION_LEAD.unpack_from(data, HEADER.size)
    return {
        "weather": weather, "track_temp": track_temp, "air_temp": air_temp,
        "total_laps": total_laps, "track_length": track_length,
        "session_type": session_type, "track_id": track_id, "formula": formula,
    }


# ---------------------------------------------------------------- lap data

_LAP_CAR = struct.Struct("<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB")  # 57 B


class CarLap:
    __slots__ = ("last_lap_ms", "current_lap_ms", "s1_ms", "s2_ms",
                 "lap_distance", "total_distance", "lap_num",
                 "invalid", "driver_status", "result_status")

    def __init__(self, v):
        self.last_lap_ms = v[0]
        self.current_lap_ms = v[1]
        self.s1_ms = v[3] * 60000 + v[2]
        self.s2_ms = v[5] * 60000 + v[4]
        self.lap_distance = v[10]
        self.total_distance = v[11]
        self.lap_num = v[14]
        self.invalid = v[18]
        self.driver_status = v[25]
        self.result_status = v[26]


def parse_lap_data(data, fmt):
    """Return ({car_idx: CarLap}, pb_ghost_idx, rival_idx). Indices 255 = none."""
    n = num_cars(fmt)
    cars = {}
    for idx in range(n):
        off = HEADER.size + idx * _LAP_CAR.size
        v = _LAP_CAR.unpack_from(data, off)
        cars[idx] = CarLap(v)
    trailer = HEADER.size + n * _LAP_CAR.size
    pb_idx, rival_idx = data[trailer], data[trailer + 1]
    return cars, pb_idx, rival_idx


# ---------------------------------------------------------------- telemetry

_TELEM_CAR_2026 = struct.Struct("<HfffBbHBBHHHHHBBBBBBBBBffffBBBB")  # 59 B
_TELEM_CAR_2025 = struct.Struct("<HfffBbHBBHHHHHBBBBBBBBHffffBBBB")  # 60 B


def parse_car_telemetry(data, fmt, wanted):
    """Return {car_idx: dict} for wanted car indices."""
    st = _TELEM_CAR_2026 if fmt >= 2026 else _TELEM_CAR_2025
    out = {}
    for idx in wanted:
        off = HEADER.size + idx * st.size
        if off + st.size > len(data):
            continue
        v = st.unpack_from(data, off)
        out[idx] = {
            "speed": v[0], "throttle": v[1], "steer": v[2], "brake": v[3],
            "gear": v[5], "rpm": v[6], "drs": v[7],
            # surface temps are v[14:18] (brakes are v[10:14])
            "tyre_temp": v[14:18],
        }
    return out


# ---------------------------------------------------------------- status

_STATUS_CAR_2026 = struct.Struct("<BBBBBfffHHBBHBBBbfffBffffB")  # 59 B
_STATUS_CAR_2025 = struct.Struct("<BBBBBfffHHBBHBBBbfffBfffB")   # 55 B


def parse_car_status(data, fmt, wanted):
    st = _STATUS_CAR_2026 if fmt >= 2026 else _STATUS_CAR_2025
    out = {}
    for idx in wanted:
        off = HEADER.size + idx * st.size
        if off + st.size > len(data):
            continue
        v = st.unpack_from(data, off)
        out[idx] = {
            "fuel": v[5], "tyre_actual": v[13], "tyre_visual": v[14],
            "ers_store": v[19], "ers_mode": v[20],
        }
    return out


# ---------------------------------------------------------------- telemetry2 (2026 only)

_TELEM2_CAR = struct.Struct("<BBHBBHBB")  # 10 B


def parse_car_telemetry2(data, wanted):
    out = {}
    for idx in wanted:
        off = HEADER.size + idx * _TELEM2_CAR.size
        if off + _TELEM2_CAR.size > len(data):
            continue
        v = _TELEM2_CAR.unpack_from(data, off)
        out[idx] = {"aero_mode": v[0], "overtake": v[4]}
    return out


# ---------------------------------------------------------------- time trial

_TT_SET_2026 = struct.Struct("<BHIIIIBBBBBB")  # 25 B
_TT_SET_2025 = struct.Struct("<BBIIIIBBBBBB")  # 24 B


def parse_time_trial(data, fmt):
    """Return dict of three datasets: session_best, personal_best, rival."""
    st = _TT_SET_2026 if fmt >= 2026 else _TT_SET_2025
    out = {}
    for i, name in enumerate(("session_best", "personal_best", "rival")):
        v = st.unpack_from(data, HEADER.size + i * st.size)
        out[name] = {
            "car_idx": v[0], "team": v[1], "lap_ms": v[2],
            "s1_ms": v[3], "s2_ms": v[4], "s3_ms": v[5], "valid": v[11],
        }
    return out


# ---------------------------------------------------------------- events

def parse_event_code(data):
    return data[HEADER.size:HEADER.size + 4].decode("ascii", "replace")
