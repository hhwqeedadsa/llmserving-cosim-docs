from __future__ import annotations

from typing import IO, List, Sequence

from ..schedule import Phase, RankOp, Schedule, pretty_print_schedule


def _validate_matrix(name: str, mat: Sequence[Sequence[int]], n: int) -> None:
    if len(mat) != n:
        raise ValueError(f"{name} must have {n} rows, got {len(mat)}")
    for r, row in enumerate(mat):
        if len(row) != n:
            raise ValueError(f"{name}[{r}] must have {n} cols, got {len(row)}")


def _row_sum_bytes(counts_row: Sequence[int], unit_bytes: int) -> int:
    return int(sum(int(c) for c in counts_row) * int(unit_bytes))


def build_a2a_pairwisev_schedule(
    *,
    rank_size: int,
    send_counts: Sequence[Sequence[int]],
    send_displs: Sequence[Sequence[int]],
    recv_counts: Sequence[Sequence[int]],
    recv_displs: Sequence[Sequence[int]],
    send_unit_bytes: int,
    recv_unit_bytes: int,
    include_local_copy: bool = True,
) -> Schedule:
    rank_size = int(rank_size)
    send_unit_bytes = int(send_unit_bytes)
    recv_unit_bytes = int(recv_unit_bytes)

    if rank_size <= 1:
        return Schedule(algo="a2a_pairwisev", rank_size=rank_size, total_bytes=0, n_steps=0, phases=[], block_sizes=None)

    if send_unit_bytes <= 0 or recv_unit_bytes <= 0:
        raise ValueError("send_unit_bytes/recv_unit_bytes must be positive")

    _validate_matrix("send_counts", send_counts, rank_size)
    _validate_matrix("send_displs", send_displs, rank_size)
    _validate_matrix("recv_counts", recv_counts, rank_size)
    _validate_matrix("recv_displs", recv_displs, rank_size)

    max_send_bytes = max(_row_sum_bytes(send_counts[r], send_unit_bytes) for r in range(rank_size))
    max_recv_bytes = max(_row_sum_bytes(recv_counts[r], recv_unit_bytes) for r in range(rank_size))
    total_bytes = max(max_send_bytes, max_recv_bytes)

    phases: List[Phase] = []
    phase_id = 0

    if include_local_copy:
        rank_ops: List[RankOp] = []
        for r in range(rank_size):
            self_send_bytes = int(send_counts[r][r]) * send_unit_bytes
            self_send_off = int(send_displs[r][r]) * send_unit_bytes
            self_recv_off = int(recv_displs[r][r]) * recv_unit_bytes

            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=-1,
                    recv_from=-1,
                    tx_blocks=[r],
                    rx_blocks=[r],
                    tx_offsets=[self_send_off],
                    rx_offsets=[self_recv_off],
                    tx_remote_offsets=None,
                    rx_remote_offsets=None,
                    tx_bytes=self_send_bytes,
                    rx_bytes=self_send_bytes,
                )
            )

        phases.append(
            Phase(
                phase=phase_id,
                stage="LocalCopy",
                step=0,
                delta_rank=None,
                n_blocks=1,
                delta_block_index=None,
                rank_ops=rank_ops,
            )
        )
        phase_id += 1

    for step in range(1, rank_size):
        rank_ops = []
        for r in range(rank_size):
            prev_rank = (r + rank_size - step) % rank_size
            next_rank = (r + step) % rank_size

            tx_bytes = int(send_counts[r][next_rank]) * send_unit_bytes
            rx_bytes = int(recv_counts[r][prev_rank]) * recv_unit_bytes

            tx_src_off = int(send_displs[r][next_rank]) * send_unit_bytes
            rx_dst_off = int(recv_displs[r][prev_rank]) * recv_unit_bytes

            tx_dst_off_remote = int(recv_displs[next_rank][r]) * recv_unit_bytes
            rx_src_off_remote = int(send_displs[prev_rank][r]) * send_unit_bytes

            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=next_rank,
                    recv_from=prev_rank,
                    tx_blocks=[next_rank],
                    rx_blocks=[prev_rank],
                    tx_offsets=[tx_src_off],
                    rx_offsets=[rx_dst_off],
                    tx_remote_offsets=[tx_dst_off_remote],
                    rx_remote_offsets=[rx_src_off_remote],
                    tx_bytes=tx_bytes,
                    rx_bytes=rx_bytes,
                )
            )

        phases.append(
            Phase(
                phase=phase_id,
                stage="AlltoAllVPairWise",
                step=step,
                delta_rank=step,
                n_blocks=1,
                delta_block_index=None,
                rank_ops=rank_ops,
            )
        )
        phase_id += 1

    return Schedule(
        algo="a2a_pairwisev",
        rank_size=rank_size,
        total_bytes=total_bytes,
        n_steps=rank_size - 1,
        block_sizes=None,
        tree=None,
        block_map=None,
        phases=phases,
    )


def pretty_print_a2a_pairwisev_schedule(schedule: Schedule, *, file: IO[str] | None = None) -> None:
    pretty_print_schedule(schedule, file=file, show_mapping=False, show_bytes=True)


# Legacy alias kept for compatibility with earlier code that imported algorithms.a2av
build_a2av_pairwise_schedule = build_a2a_pairwisev_schedule
pretty_print_a2av_pairwise_schedule = pretty_print_a2a_pairwisev_schedule


def _build_uniform_matrix(rank_size: int, with_local: bool = True) -> tuple[list[list[int]], list[list[int]]]:
    """Helper to produce counts/displacements for uniform all-to-all."""
    counts = [[0] * rank_size for _ in range(rank_size)]
    displs = [[0] * rank_size for _ in range(rank_size)]
    for r in range(rank_size):
        offset = 0
        for c in range(rank_size):
            counts[r][c] = 1 if (with_local or c != r) else 0
            displs[r][c] = offset
            offset += counts[r][c]
    return counts, displs


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pretty-print a sample AlltoAllv schedule")
    parser.add_argument("-n", "--rank-size", type=int, default=4, help="Number of ranks")
    parser.add_argument("-u", "--unit-bytes", type=int, default=1, help="Unit byte size for each send/recv")
    args = parser.parse_args()

    send_counts, send_displs = _build_uniform_matrix(args.rank_size, with_local=False)
    recv_counts, recv_displs = _build_uniform_matrix(args.rank_size, with_local=False)

    schedule = build_a2a_pairwisev_schedule(
        rank_size=args.rank_size,
        send_counts=send_counts,
        send_displs=send_displs,
        recv_counts=recv_counts,
        recv_displs=recv_displs,
        send_unit_bytes=args.unit_bytes,
        recv_unit_bytes=args.unit_bytes,
        include_local_copy=False,
    )
    pretty_print_a2a_pairwisev_schedule(schedule)
