"""Bounded queues: capture and each output have independent ownership."""
from queue import Empty, Full, Queue


class LatestQueue:
    def __init__(self, capacity=2):
        self.queue = Queue(maxsize=capacity)
        self.dropped = 0

    def put(self, value):
        try:
            self.queue.put_nowait(value)
        except Full:
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except Empty:
                pass
            try:
                self.queue.put_nowait(value)
            except Full:
                self.dropped += 1

    def get(self, timeout=None):
        return self.queue.get(timeout=timeout) if timeout is not None else self.queue.get_nowait()
