from __future__ import annotations

import math
from typing import IO, List, Tuple

from ..schedule import Phase, RankOp, Schedule, pretty_print_schedule
from .nhr import hccl_calc_slices


LogicCommPair = Tuple[int, int, int]
LogicCommPairsByPhase = List[List[LogicCommPair]]


def _largest_power_of_two_leq(n: int) -> int:
    n = int(n)
    if n <= 0:
        raise ValueError("n must be positive")
    return 1 << int(math.floor(math.log2(n)))


def _calc_block_and_part1(rank_size: int) -> tuple[int, int]:
    rank_size = int(rank_size)
    if rank_size <= 0:
        raise ValueError("rank_size must be positive")
    block_size = _largest_power_of_two_leq(rank_size)
    part1_size = (rank_size - block_size) * 2
    return block_size, part1_size


def _build_active_rank_maps(rank_size: int) -> tuple[int, int, List[int], List[int | None]]:
    block_size, part1_size = _calc_block_and_part1(rank_size)

    active_to_real: List[int] = []
    for r in range(min(part1_size, rank_size)):
        if (r % 2) == 0:
            active_to_real.append(r)
    for r in range(part1_size, rank_size):
        active_to_real.append(r)

    if len(active_to_real) != block_size:
        raise RuntimeError(
            f"recursive_hd mapping internal error: active_to_real size {len(active_to_real)} != block_size {block_size}"
        )

    real_to_active: List[int | None] = [None] * rank_size
    for a, r in enumerate(active_to_real):
        real_to_active[r] = a

    return block_size, part1_size, active_to_real, real_to_active


def build_ar_rhd_schedule(rank_size: int, total_bytes: int) -> Schedule:
    rank_size = int(rank_size)
    total_bytes = int(total_bytes)

    if rank_size <= 1:
        return Schedule(algo="ar_rhd", rank_size=rank_size, total_bytes=total_bytes, n_steps=0, block_sizes=[], phases=[])

    block_size, part1_size, active_to_real, real_to_active = _build_active_rank_maps(rank_size)

    step_num = int(math.log2(block_size))
    block_sizes = hccl_calc_slices(total_bytes, block_size)

    phases: List[Phase] = []
    phase_id = 0

    def add_phase(stage: str, step: int, delta_rank: int | None, n_blocks: int, delta_block_index: int | None,
                  rank_ops: List[RankOp]) -> None:
        nonlocal phase_id
        phases.append(
            Phase(
                phase=phase_id,
                stage=stage,
                step=step,
                delta_rank=delta_rank,
                n_blocks=n_blocks,
                delta_block_index=delta_block_index,
                rank_ops=rank_ops,
            )
        )
        phase_id += 1

    if part1_size > 0:
        rank_ops: List[RankOp] = []
        for r in range(rank_size):
            if r < part1_size and (r % 2) == 1:
                rank_ops.append(
                    RankOp(rank=r, send_to=r - 1, recv_from=-1, tx_blocks=[], rx_blocks=[], tx_bytes=total_bytes, rx_bytes=0)
                )
            elif r < part1_size and (r % 2) == 0:
                rank_ops.append(
                    RankOp(rank=r, send_to=-1, recv_from=r + 1, tx_blocks=[], rx_blocks=[], tx_bytes=0, rx_bytes=total_bytes)
                )
            else:
                rank_ops.append(RankOp(rank=r, send_to=-1, recv_from=-1, tx_blocks=[], rx_blocks=[], tx_bytes=0, rx_bytes=0))
        add_phase(stage="FoldIn", step=0, delta_rank=None, n_blocks=0, delta_block_index=None, rank_ops=rank_ops)

    for step in range(step_num):
        peer_bitmask = 1 << (step_num - step - 1)
        rank_ops = []
        for a in range(block_size):
            peer_a = a ^ peer_bitmask
            r = active_to_real[a]
            peer_r = active_to_real[peer_a]

            tx_slice_id = peer_a & (~(peer_bitmask - 1))
            rx_slice_id = a & (~(peer_bitmask - 1))

            tx_blocks = list(range(tx_slice_id, tx_slice_id + peer_bitmask))
            rx_blocks = list(range(rx_slice_id, rx_slice_id + peer_bitmask))

            tx_bytes = sum(int(block_sizes[b]) for b in tx_blocks)
            rx_bytes = sum(int(block_sizes[b]) for b in rx_blocks)

            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=peer_r,
                    recv_from=peer_r,
                    tx_blocks=tx_blocks,
                    rx_blocks=rx_blocks,
                    tx_bytes=tx_bytes,
                    rx_bytes=rx_bytes,
                )
            )

        add_phase(
            stage="ReduceScatter",
            step=step,
            delta_rank=peer_bitmask,
            n_blocks=peer_bitmask,
            delta_block_index=None,
            rank_ops=rank_ops,
        )

    for step in range(step_num):
        peer_bitmask = 1 << step
        rank_ops = []
        for a in range(block_size):
            peer_a = a ^ peer_bitmask
            r = active_to_real[a]
            peer_r = active_to_real[peer_a]

            tx_slice_id = a & (~(peer_bitmask - 1))
            rx_slice_id = peer_a & (~(peer_bitmask - 1))

            tx_blocks = list(range(tx_slice_id, tx_slice_id + peer_bitmask))
            rx_blocks = list(range(rx_slice_id, rx_slice_id + peer_bitmask))

            tx_bytes = sum(int(block_sizes[b]) for b in tx_blocks)
            rx_bytes = sum(int(block_sizes[b]) for b in rx_blocks)

            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=peer_r,
                    recv_from=peer_r,
                    tx_blocks=tx_blocks,
                    rx_blocks=rx_blocks,
                    tx_bytes=tx_bytes,
                    rx_bytes=rx_bytes,
                )
            )

        add_phase(
            stage="AllGather",
            step=step,
            delta_rank=peer_bitmask,
            n_blocks=peer_bitmask,
            delta_block_index=None,
            rank_ops=rank_ops,
        )

    if part1_size > 0:
        rank_ops = []
        for r in range(rank_size):
            if r < part1_size and (r % 2) == 0:
                rank_ops.append(
                    RankOp(rank=r, send_to=r + 1, recv_from=-1, tx_blocks=[], rx_blocks=[], tx_bytes=total_bytes, rx_bytes=0)
                )
            elif r < part1_size and (r % 2) == 1:
                rank_ops.append(
                    RankOp(rank=r, send_to=-1, recv_from=r - 1, tx_blocks=[], rx_blocks=[], tx_bytes=0, rx_bytes=total_bytes)
                )
            else:
                rank_ops.append(RankOp(rank=r, send_to=-1, recv_from=-1, tx_blocks=[], rx_blocks=[], tx_bytes=0, rx_bytes=0))
        add_phase(stage="FoldOut", step=0, delta_rank=None, n_blocks=0, delta_block_index=None, rank_ops=rank_ops)

    return Schedule(algo="ar_rhd", rank_size=rank_size, total_bytes=total_bytes, n_steps=step_num, block_sizes=block_sizes, tree=None, block_map=None, phases=phases)


def pretty_print_ar_rhd_schedule(schedule: Schedule, *, file: IO[str] | None = None) -> None:
    pretty_print_schedule(schedule, file=file, show_mapping=True, show_bytes=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pretty-print a sample RHD schedule")
    parser.add_argument("-n", "--rank-size", type=int, default=8, help="Total ranks")
    parser.add_argument("-b", "--bytes", type=int, default=4096, help="Total bytes per rank")
    args = parser.parse_args()

    schedule = build_ar_rhd_schedule(args.rank_size, args.bytes)
    pretty_print_ar_rhd_schedule(schedule)
