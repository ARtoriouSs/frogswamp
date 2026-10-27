"""Which turns of an HA conversation the bridge has not seen yet (no HA imports, so it can be tested alone)"""

import time

ROLES = ("user", "assistant")


def pending_turns(entries, forwarded):
    """Turns HA handled itself since our last answer, without the current request (the last entry)"""
    return [
        {"role": role, "text": text}
        for role, text in entries[forwarded:-1]
        if role in ROLES and text
    ]


class Forwarded:
    """How many chat log entries of a conversation the bridge has seen, forgotten after `ttl` seconds"""

    def __init__(self, ttl, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self.counts = {}

    def get(self, conv_id):
        entry = self.counts.get(conv_id)
        if entry is None or self.clock() - entry[1] > self.ttl:
            self.counts.pop(conv_id, None)
            return 0
        return entry[0]

    def set(self, conv_id, count):
        now = self.clock()
        self.counts = {c: e for c, e in self.counts.items() if now - e[1] <= self.ttl}
        self.counts[conv_id] = (count, now)
