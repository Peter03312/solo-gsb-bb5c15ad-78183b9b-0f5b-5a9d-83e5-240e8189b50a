def make_payload(
    circle=(0, 0, 10),
    freq=(100, 110),
    start="2026-10-01T08:00:00Z",
    end="2026-10-01T10:00:00Z",
    name="Event",
):
    cx, cy, radius = circle
    low, high = freq
    return {
        "name": name,
        "circle": {"cx": cx, "cy": cy, "radius": radius},
        "frequency": {"low": low, "high": high},
        "time_window": {"start": start, "end": end},
    }
