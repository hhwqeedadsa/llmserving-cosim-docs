from __future__ import annotations

from typing import IO, List

from ..schedule import Phase, RankOp, Schedule, pretty_print_schedule


def build_a2a_pairwise_schedule(rank_size: int, total_bytes: int) -> Schedule:
    rank_size = int(rank_size)
    total_bytes = int(total_bytes)

    if rank_size <= 1:
        return Schedule(algo="a2a_pairwise", rank_size=rank_size, total_bytes=total_bytes, n_steps=0, phases=[], block_sizes=None)

    phase_bytes = int(total_bytes / rank_size)

    phases: List[Phase] = []
    for step in range(rank_size - 1):
        send_gap = step + 1
        rank_ops: List[RankOp] = []
        for r in range(rank_size):
            send_to = (r + send_gap) % rank_size
            recv_from = (r - send_gap + rank_size) % rank_size
            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=send_to,
                    recv_from=recv_from,
                    tx_blocks=[send_to],
                    rx_blocks=[recv_from],
                    tx_bytes=phase_bytes,
                    rx_bytes=phase_bytes,
                )
            )

        phases.append(
            Phase(
                phase=step,
                stage="All2AllPairwise",
                step=step,
                delta_rank=send_gap,
                n_blocks=1,
                delta_block_index=None,
                rank_ops=rank_ops,
            )
        )

    return Schedule(
        algo="a2a_pairwise",
        rank_size=rank_size,
        total_bytes=total_bytes,
        n_steps=rank_size - 1,
        block_sizes=None,
        tree=None,
        block_map=None,
        phases=phases,
    )


def pretty_print_a2a_pairwise_schedule(schedule: Schedule, *, file: IO[str] | None = None) -> None:
    pretty_print_schedule(schedule, file=file, show_mapping=False, show_bytes=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pretty-print a sample pairwise AlltoAll schedule")
    parser.add_argument("-n", "--rank-size", type=int, default=4,
                        help="Number of ranks")
    parser.add_argument("-b", "--bytes", type=int, default=1024,
                        help="Total bytes per rank")
    args = parser.parse_args()

    schedule = build_a2a_pairwise_schedule(args.rank_size, args.bytes)
    pretty_print_a2a_pairwise_schedule(schedule)
