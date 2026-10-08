"""Frozen public information conditions; no source paths or internal readers."""
END_NS = 100_000
BASE_NS = 100
GRIDS = ((100, 0), (1000, 0), (1000, 500), (10000, 0), (10000, 5000))
CONDITIONS = ('counts', 'counts-queues')
COUNT_FIELDS = ('counted_packets', 'counted_packet_bytes')
QUEUE_FIELDS = ('voq_waiting_byte_ns', 'voq_waiting_peak_bytes',
                'fifo_waiting_byte_ns', 'fifo_waiting_peak_bytes')


def windows(period, origin):
    if (period, origin) not in GRIDS:
        raise ValueError('Undeclared grid')
    points = sorted({0, END_NS, *range(origin, END_NS, period)})
    return list(zip(points, points[1:]))


def grid_id(period, origin):
    return f'p{period}-o{origin}'
