"""UDP listener + lap segmentation.

Runs in its own thread, owns the DB writer connection. Keeps a
latest-value cache for the player's car for motion/telemetry/status
packets and snapshots a sample row every time a LapData packet arrives
(LapData carries the two join keys: currentLapTime and lapDistance).

Only the player's car is recorded. Versions before 0.1.4 also captured
the Time Trial ghosts; the game's shadow-car telemetry turned out to be
largely fabricated, so that was removed — the history is in
docs/design-notes.md.
"""

import datetime
import json
import socket
import threading
import time

from . import db, ids, packets


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


class LapBuffer:
    __slots__ = ("lap_num", "samples", "invalid", "s1_ms", "s2_ms")

    def __init__(self, lap_num):
        self.lap_num = lap_num
        self.samples = []
        self.invalid = False
        self.s1_ms = 0
        self.s2_ms = 0


class Recorder(threading.Thread):
    def __init__(self, db_path, udp_port=20777):
        super().__init__(daemon=True, name="f1trace-recorder")
        self.db_path = db_path
        self.udp_port = udp_port
        self.status = {
            "listening": False, "udp_port": udp_port, "packets": 0, "pps": 0,
            "packet_format": None, "session": None, "live": None,
            "last_lap": None, "warnings": [],
            "packet_sizes": {},   # pid -> observed byte size (layout probe)
            "cars": {},           # tracked car idx -> live buffer stats
        }
        self._status_lock = threading.Lock()
        self._reset_session_state(None)

    # ---------------------------------------------------------- state

    def _reset_session_state(self, uid):
        self.session_uid = uid
        self.session_row_id = None
        self.session_info = None
        self.track_length = 0
        self.player_idx = None
        self.bufs = {}          # car_idx -> LapBuffer
        self.motion = {}        # car_idx -> (x, y, z, glat, glong)
        self.telem = {}         # car_idx -> dict
        self.car_status = {}    # car_idx -> dict
        self.telem2 = {}        # car_idx -> dict
        self.setups = {}        # car_idx -> setup dict
        self.teams = {}         # car_idx -> team id
        self.sess_assists = None   # player assist settings (Session packet)

    def _set_status(self, **kw):
        with self._status_lock:
            self.status.update(kw)

    def get_status(self):
        with self._status_lock:
            return dict(self.status)

    def _warn_once(self, msg):
        with self._status_lock:
            if msg not in self.status["warnings"]:
                self.status["warnings"].append(msg)
                print("[f1trace] WARNING: %s" % msg)

    # ---------------------------------------------------------- main loop

    def run(self):
        self.con = db.connect(self.db_path)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        try:
            sock.bind(("0.0.0.0", self.udp_port))
        except OSError:
            msg = ("UDP port %d is already in use — another TRACE instance "
                   "is probably running. Not recording." % self.udp_port)
            self._warn_once(msg)
            return
        sock.settimeout(1.0)
        self._set_status(listening=True)
        print("[f1trace] listening for telemetry on UDP %d" % self.udp_port)

        n_packets = 0
        window_start = time.time()
        window_count = 0
        while True:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                now = time.time()
                if now - window_start >= 2.0:
                    self._set_status(pps=0)
                    window_start, window_count = now, 0
                continue
            n_packets += 1
            window_count += 1
            now = time.time()
            if now - window_start >= 2.0:
                self._set_status(pps=int(window_count / (now - window_start)),
                                 packets=n_packets)
                window_start, window_count = now, 0
            try:
                self._handle(data)
            except Exception as e:  # never let one bad packet kill the thread
                self._warn_once("packet handling error: %r" % e)

    # ---------------------------------------------------------- dispatch

    def _handle(self, data):
        if len(data) < packets.HEADER.size:
            return
        h = packets.Header(data)
        if h.packet_format not in (2025, 2026):
            self._warn_once("unknown packetFormat %d — game may need the "
                            "'F1 25 2026 Season Pack' UDP setting; layouts "
                            "may not match" % h.packet_format)
        if h.session_uid != self.session_uid and h.session_uid != 0:
            self._reset_session_state(h.session_uid)
        self.player_idx = h.player_car_index
        fmt = h.packet_format
        self._set_status(packet_format=fmt)

        pid = h.packet_id
        sizes = self.status["packet_sizes"]
        if sizes.get(str(pid)) != len(data):
            with self._status_lock:
                sizes[str(pid)] = len(data)

        if pid == packets.LAP_DATA:
            self._on_lap_data(data, fmt)
        elif pid == packets.MOTION:
            self.motion.update(packets.parse_motion(data, fmt, self._wanted()))
        elif pid == packets.CAR_TELEMETRY:
            self.telem.update(packets.parse_car_telemetry(data, fmt, self._wanted()))
        elif pid == packets.CAR_STATUS:
            self.car_status.update(packets.parse_car_status(data, fmt, self._wanted()))
        elif pid == packets.CAR_TELEMETRY2 and fmt >= 2026:
            self.telem2.update(packets.parse_car_telemetry2(data, self._wanted()))
        elif pid == packets.CAR_SETUPS:
            self.setups.update(packets.parse_car_setups(data, fmt, self._wanted()))
        elif pid == packets.PARTICIPANTS:
            self.teams.update(packets.parse_participants(data, fmt))
        elif pid == packets.SESSION:
            self.sess_assists = packets.parse_session_assists(data)
            self._on_session(data, h)

    def _wanted(self):
        return set() if self.player_idx is None else {self.player_idx}

    # ---------------------------------------------------------- session

    def _on_session(self, data, h):
        s = packets.parse_session(data)
        self.track_length = s["track_length"]
        if self.session_info is None:
            self.session_info = s
            cur = self.con.execute(
                "INSERT INTO sessions (uid, started_at, packet_format, game_year,"
                " track_id, track_name, session_type, session_type_name,"
                " weather, air_temp, track_temp, track_length)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(h.session_uid), _now(), h.packet_format, h.game_year,
                 s["track_id"], ids.track_name(s["track_id"]),
                 s["session_type"], ids.session_type_name(s["session_type"]),
                 s["weather"], s["air_temp"], s["track_temp"], s["track_length"]))
            self.con.commit()
            self.session_row_id = cur.lastrowid
            print("[f1trace] new session: %s %s" % (
                ids.track_name(s["track_id"]),
                ids.session_type_name(s["session_type"])))
        self._set_status(session={
            "id": self.session_row_id,
            "track": ids.track_name(s["track_id"]),
            "type": ids.session_type_name(s["session_type"]),
        })

    # ---------------------------------------------------------- lap data

    def _on_lap_data(self, data, fmt):
        cars = packets.parse_lap_data(data, fmt)
        idx = self.player_idx
        cl = cars.get(idx) if idx is not None else None
        if cl is None:
            return

        buf = self.bufs.get(idx)
        if buf is None:
            buf = self.bufs[idx] = LapBuffer(cl.lap_num)
        if cl.lap_num != buf.lap_num:
            self._finalize(idx, buf,
                           cl.last_lap_ms if cl.lap_num == buf.lap_num + 1 else 0)
            buf = self.bufs[idx] = LapBuffer(cl.lap_num)

        if cl.lap_distance < 0 or cl.current_lap_ms <= 0:
            return

        samples = buf.samples
        if samples:
            last = samples[-1]
            if cl.current_lap_ms == last[0]:
                return  # duplicate frame
            if cl.current_lap_ms < last[0] or cl.lap_distance < last[1] - 1.0:
                # flashback / rewind / reset: drop samples past the
                # new position
                while samples and samples[-1][1] >= cl.lap_distance:
                    samples.pop()

        if cl.invalid:
            buf.invalid = True
        if cl.s1_ms:
            buf.s1_ms = cl.s1_ms
        if cl.s2_ms:
            buf.s2_ms = cl.s2_ms

        m = self.motion.get(idx)
        if m is None:
            return  # wait for the first motion packet
        t = self.telem.get(idx) or {}
        st = self.car_status.get(idx) or {}
        t2 = self.telem2.get(idx) or {}
        tt = t.get("tyre_temp") or (0, 0, 0, 0)
        samples.append((
            cl.current_lap_ms,
            round(cl.lap_distance, 1),
            round(m[0], 2), round(m[1], 2), round(m[2], 2),
            t.get("speed", 0),
            int(round((t.get("throttle") or 0.0) * 100)),
            int(round((t.get("brake") or 0.0) * 100)),
            int(round((t.get("steer") or 0.0) * 100)),
            t.get("gear", 0),
            1 if t.get("drs") else 0,
            1 if t2.get("overtake") else 0,
            t.get("rpm", 0),
            tt[2], tt[3], tt[0], tt[1],  # order RL,RR,FL,FR on wire; store FL,FR,RL,RR
            round(st.get("fuel", 0.0), 2),
            round((st.get("ers_store") or 0.0) / 1e6, 3),
            1 if t2.get("aero_mode") else 0,  # 2026: X-mode(1)/Z-mode(0)
        ))

        self._set_status(
            live={
                "lap_num": cl.lap_num,
                "lap_time_ms": cl.current_lap_ms,
                "distance": int(cl.lap_distance),
                "speed": t.get("speed", 0),
            },
            cars={str(i): {"role": "player", "lap_num": b.lap_num,
                           "samples": len(b.samples)}
                  for i, b in self.bufs.items()})

    COLUMNS = ("t", "d", "x", "y", "z", "spd", "thr", "brk", "str",
               "gear", "drs", "ot", "rpm", "tfl", "tfr", "trl", "trr",
               "fuel", "ers", "aero")

    def _assists_for(self, st):
        """Assist settings for a finishing lap: per-car TC/ABS from
        CarStatus plus the Session-packet settings."""
        a = {}
        if "tc" in st:
            a["tc"] = st["tc"]
            a["abs"] = st["abs"]
        if self.sess_assists:
            a.update(self.sess_assists)
        return a

    def _finalize(self, idx, buf, lap_time_ms):
        samples = buf.samples
        if lap_time_ms <= 0:
            return  # lap counter jumped or time missing
        if len(samples) < 50:
            return
        if self.session_row_id is None:
            return
        span = samples[-1][1] - samples[0][1]
        if self.track_length and span < 0.9 * self.track_length:
            return  # partial lap

        s1, s2 = buf.s1_ms, buf.s2_ms
        s3 = lap_time_ms - s1 - s2 if s1 and s2 else 0
        cols = {name: [s[i] for s in samples]
                for i, name in enumerate(self.COLUMNS)}
        st = self.car_status.get(idx) or {}
        assists = self._assists_for(st)
        setup = self.setups.get(idx)
        self.con.execute(
            "INSERT INTO laps (session_id, car_role, car_index, lap_num,"
            " lap_time_ms, s1_ms, s2_ms, s3_ms, valid, tyre_visual,"
            " top_speed, n_samples, created_at, samples, setup, assists,"
            " team_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.session_row_id, "player", idx, buf.lap_num, lap_time_ms,
             s1, s2, s3, 0 if buf.invalid else 1,
             st.get("tyre_visual"), max(cols["spd"]) if cols["spd"] else 0,
             len(samples), _now(), db.pack_samples(cols),
             json.dumps(setup) if setup else None,
             json.dumps(assists) if assists else None,
             self.teams.get(idx)))
        self.con.commit()
        print("[f1trace] stored lap %d — %s%s" % (
            buf.lap_num, _fmt_time(lap_time_ms),
            "" if not buf.invalid else " (invalid)"))
        self._set_status(last_lap={
            "role": "player", "lap_num": buf.lap_num,
            "lap_time_ms": lap_time_ms, "valid": not buf.invalid,
        })


def _fmt_time(ms):
    return "%d:%06.3f" % (ms // 60000, (ms % 60000) / 1000.0)
