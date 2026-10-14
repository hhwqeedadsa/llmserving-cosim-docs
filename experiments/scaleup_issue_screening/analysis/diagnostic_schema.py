"""Restricted local counter contract; no private source or result readers."""
FIELDS=('voq_byte_ns','fifo_byte_ns','waiting_ns','busy_ns','queued_busy_ns','tx_bytes','tx_packets',
        'probes','blocked_probes','queue_peak_bytes','serial_work_ns','fifo_arrival_bytes')
IX={n:i for i,n in enumerate(FIELDS)}
BITS=dict(voq_byte_ns=64,fifo_byte_ns=64,waiting_ns=32,busy_ns=32,queued_busy_ns=32,
          tx_bytes=64,tx_packets=32,probes=32,blocked_probes=32,queue_peak_bytes=32,
          serial_work_ns=64,fifo_arrival_bytes=64)
