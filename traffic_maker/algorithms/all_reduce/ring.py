from __future__ import annotations

from typing import IO, List

from ..schedule import Phase, RankOp, Schedule, pretty_print_schedule
from .nhr import hccl_calc_slices


def build_ar_ring_schedule(rank_size: int, total_bytes: int) -> Schedule:
    """Mimic HCCL Ring AllReduce = ReduceScatterRing + AllGatherRing.

    Reference slice indices:
      RS: tx=(rank-1-s), rx=(rank-2-s) (mod N)
      AG: tx=(rank+s), rx=(rank+1+s) (mod N)
    """
    rank_size = int(rank_size)
    total_bytes = int(total_bytes)
    if rank_size <= 1:
        return Schedule(algo="ar_ring", rank_size=rank_size, total_bytes=total_bytes, n_steps=0, block_sizes=[], phases=[])

    block_sizes = hccl_calc_slices(total_bytes, rank_size)

    phases: List[Phase] = []
    phase_id = 0

    # ReduceScatter (rank_size - 1 phases)
    for step in range(rank_size - 1):
        rank_ops: List[RankOp] = []
        for r in range(rank_size):
            send_to = (r + 1) % rank_size
            recv_from = (r - 1 + rank_size) % rank_size

            tx_block = (r - 1 - step + rank_size) % rank_size
            rx_block = (r - 2 - step + rank_size) % rank_size

            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=send_to,
                    recv_from=recv_from,
                    tx_blocks=[tx_block],
                    rx_blocks=[rx_block],
                    tx_bytes=int(block_sizes[tx_block]),
                    rx_bytes=int(block_sizes[rx_block]),
                )
            )

        phases.append(
            Phase(
                phase=phase_id,
                stage="ReduceScatter",
                step=step,
                delta_rank=1,
                n_blocks=1,
                delta_block_index=1,
                rank_ops=rank_ops,
            )
        )
        phase_id += 1

    # AllGather (rank_size - 1 phases)
    for step in range(rank_size - 1):
        rank_ops = []
        for r in range(rank_size):
            send_to = (r + 1) % rank_size
            recv_from = (r - 1 + rank_size) % rank_size

            tx_block = (r + step) % rank_size
            rx_block = (r + 1 + step) % rank_size

            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=send_to,
                    recv_from=recv_from,
                    tx_blocks=[tx_block],
                    rx_blocks=[rx_block],
                    tx_bytes=int(block_sizes[tx_block]),
                    rx_bytes=int(block_sizes[rx_block]),
                )
            )

        phases.append(
            Phase(
                phase=phase_id,
                stage="AllGather",
                step=step,
                delta_rank=1,
                n_blocks=1,
                delta_block_index=1,
                rank_ops=rank_ops,
            )
        )
        phase_id += 1

    return Schedule(
        algo="ar_ring",
        rank_size=rank_size,
        total_bytes=total_bytes,
        n_steps=rank_size - 1,
        block_sizes=block_sizes,
        tree=None,
        block_map=None,
        phases=phases,
    )


def pretty_print_ar_ring_schedule(schedule: Schedule, *, file: IO[str] | None = None) -> None:
    pretty_print_schedule(schedule, file=file, show_mapping=True, show_bytes=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pretty-print a sample ring AllReduce schedule")
    parser.add_argument("-n", "--rank-size", type=int, default=4,
                        help="Number of ranks in the ring")
    parser.add_argument("-b", "--bytes", type=int, default=1024,
                        help="Total bytes per rank")
    args = parser.parse_args()

    schedule = build_ar_ring_schedule(args.rank_size, args.bytes)
    pretty_print_ar_ring_schedule(schedule)
