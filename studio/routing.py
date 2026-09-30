"""Identify the ordinary VB-CABLE pair without confusing Cable A/B or Voicemeeter."""


def cable_pair(devices):
    def find(direction, endpoint):
        matches = [d for d in devices if d.get(direction, 0) > 0
                   and d["name"].casefold().startswith(endpoint.casefold() + " (")
                   and "vb-audio virtual cable" in d["name"].casefold()]
        matches.sort(key=lambda d: ("WASAPI" not in d["host"], d["id"]))
        return matches[0] if matches else None
    return find("outputs", "CABLE Input"), find("inputs", "CABLE Output")


def restore_device_key(devices, previous, direction):
    options = [d for d in devices if d.get(direction, 0)]
    if previous in [d["key"] for d in options]:
        return previous
    stem = previous.rsplit(" | ", 1)[0]
    matches = [d for d in options if d["key"].rsplit(" | ", 1)[0] == stem]
    return matches[0]["key"] if len(matches) == 1 else ""
