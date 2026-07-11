"""Human-readable names for game enum ids (2026 Season Pack values)."""

TRACKS = {
    -1: "Unknown", 0: "Melbourne", 2: "Shanghai", 3: "Bahrain", 4: "Barcelona",
    5: "Monaco", 6: "Montreal", 7: "Silverstone", 9: "Hungaroring", 10: "Spa",
    11: "Monza", 12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Austin",
    16: "Interlagos", 17: "Red Bull Ring", 19: "Mexico City", 20: "Baku",
    26: "Zandvoort", 27: "Imola", 29: "Jeddah", 30: "Miami", 31: "Las Vegas",
    32: "Qatar", 39: "Silverstone (R)", 40: "Red Bull Ring (R)",
    41: "Zandvoort (R)", 42: "Madrid",
}

SESSION_TYPES = {
    0: "Unknown", 1: "P1", 2: "P2", 3: "P3", 4: "Short Practice",
    5: "Q1", 6: "Q2", 7: "Q3", 8: "Short Quali", 9: "One-Shot Quali",
    10: "Sprint SO1", 11: "Sprint SO2", 12: "Sprint SO3",
    13: "Short Sprint SO", 14: "One-Shot Sprint SO",
    15: "Race", 16: "Race 2", 17: "Race 3", 18: "Time Trial",
}

# Visual tyre compounds (subset that matters for F1 cars)
TYRES = {16: "Soft", 17: "Medium", 18: "Hard", 7: "Inter", 8: "Wet"}

WEATHER = {0: "Clear", 1: "Light Cloud", 2: "Overcast", 3: "Light Rain",
           4: "Heavy Rain", 5: "Storm"}


def track_name(track_id):
    return TRACKS.get(track_id, "Track %d" % track_id)


def session_type_name(st):
    return SESSION_TYPES.get(st, "Session %d" % st)


def tyre_name(visual_id):
    return TYRES.get(visual_id, "C%d" % visual_id)
