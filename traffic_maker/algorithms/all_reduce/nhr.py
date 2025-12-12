from __future__ import annotations

from typing import IO, List, Tuple

from ..schedule import Phase, RankOp, Schedule, pretty_print_schedule


HCCL_MIN_SLICE_ALIGN = 128  # aligns with HCCL/HCOMM default (see alg_template_base_pub.h)


def round_up_with_divisor(value: int, divisor: int) -> int:
    if divisor <= 0:
        raise ValueError("divisor must be > 0")
    return ((value + divisor - 1) // divisor) * divisor


def hccl_calc_slices(total_bytes: int, rank_size: int, *, align: int = HCCL_MIN_SLICE_ALIGN) -> List[int]:
    """Mimic AllReduceNHR::PrepareRunAsync slice calculation.

    Returns a list of per-rank slice sizes (bytes), indexed by mapped slice index.
    """
    if rank_size <= 0:
        raise ValueError("rank_size must be > 0")
    if total_bytes < 0:
        raise ValueError("total_bytes must be >= 0")

    slice_size_calculated = (total_bytes + (rank_size - 1)) // rank_size
    slice_size_aligned = round_up_with_divisor(slice_size_calculated, align) if total_bytes > 0 else 0

    residue_size = total_bytes
    slice_sizes: List[int] = []
    for _ in range(rank_size):
        size = slice_size_aligned if residue_size > slice_size_aligned else residue_size
        slice_sizes.append(int(size))
        residue_size -= size
    return slice_sizes


def nhr_get_step_num(rank_size: int) -> int:
    """Mimic NHRBase::GetStepNumInterServer."""
    if rank_size <= 1:
        return 0
    steps = 0
    tmp = rank_size - 1
    while tmp != 0:
        tmp >>= 1
        steps += 1
    return steps


def nhr_get_rank_mapping(
    rank_size: int, *, keep_order: bool = False, return_tree: bool = False
):
    """Mimic NHRBase::GetRankMapping (tree reorder) from HCOMM.

    Returns slice_map where slice_map[oldRank] = mapped_index.

    If return_tree=True, returns (tree, slice_map) where tree[index] = oldRank.
    """
    tree = list(range(rank_size))
    if keep_order:
        return (tree, tree) if return_tree else tree

    tmp = [0] * rank_size
    n_steps = nhr_get_step_num(rank_size)
    length = rank_size

    def reorder_sequence(start: int, end: int, length_: int):
        for i in range(start, end):
            offset = i - start
            if (offset & 1) == 0:
                tmp[start + offset // 2] = tree[i]
            else:
                tmp[start + (offset + length_) // 2] = tree[i]

    for step in range(n_steps):
        n_slices = (rank_size - 1 + (1 << step)) // (1 << (step + 1))
        if n_slices <= 1:
            break

        end_flag = False
        part = 0
        while part * length < rank_size:
            start = part * length
            end = min(start + length, rank_size)
            reorder_sequence(start, end, length)
            if ((end - start) & 1) == 1:
                end_flag = True
            part += 1

        tree = tmp.copy()
        if end_flag:
            break
        length >>= 1

    slice_map = [0] * rank_size
    for i, rank in enumerate(tree):
        slice_map[rank] = i

    return (tree, slice_map) if return_tree else slice_map


def build_nhr_schedule(rank_size: int, total_bytes: int) -> Schedule:
    """Build the full NHR schedule data structure.

    Goal: mimic HCCL/HCOMM NHR phase flow and compute, per-phase:
    - (rank -> send_to/recv_from)
    - tx/rx slice indices (oldRank space)
    - tx/rx mapped slice indices (tree reorder)
    - tx/rx bytes (sum of slice sizes)
    """
    rank_size = int(rank_size)
    total_bytes = int(total_bytes)
    if rank_size <= 0:
        raise ValueError("rank_size must be > 0")
    if total_bytes < 0:
        raise ValueError("total_bytes must be >= 0")

    slice_sizes = hccl_calc_slices(total_bytes, rank_size)
    tree, slice_map = nhr_get_rank_mapping(rank_size, keep_order=False, return_tree=True)
    n_steps = nhr_get_step_num(rank_size)

    phases: List[Phase] = []
    phase_id = 0

    # ReduceScatter
    for step in range(n_steps):
        delta_rank = 1 << step
        n_slices = (rank_size - 1 + (1 << step)) // (1 << (step + 1))
        delta_slice_index = 1 << (step + 1)

        rank_ops: List[RankOp] = []
        for r in range(rank_size):
            send_to = (r + rank_size - delta_rank) % rank_size
            recv_from = (r + delta_rank) % rank_size

            tx_idx = send_to
            rx_idx = r
            tx_slices: List[int] = []
            rx_slices: List[int] = []
            tx_mapped: List[int] = []
            rx_mapped: List[int] = []
            tx_bytes = 0
            rx_bytes = 0

            for _ in range(n_slices):
                tx_slices.append(tx_idx)
                rx_slices.append(rx_idx)

                tx_m = slice_map[tx_idx]
                rx_m = slice_map[rx_idx]
                tx_mapped.append(tx_m)
                rx_mapped.append(rx_m)
                tx_bytes += slice_sizes[tx_m]
                rx_bytes += slice_sizes[rx_m]

                tx_idx = (tx_idx + rank_size - delta_slice_index) % rank_size
                rx_idx = (rx_idx + rank_size - delta_slice_index) % rank_size

            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=send_to,
                    recv_from=recv_from,
                    tx_blocks=tx_slices,
                    rx_blocks=rx_slices,
                    tx_mapped=tx_mapped,
                    rx_mapped=rx_mapped,
                    tx_bytes=int(tx_bytes),
                    rx_bytes=int(rx_bytes),
                )
            )

        phases.append(
            Phase(
                phase=phase_id,
                stage="ReduceScatter",
                step=step,
                delta_rank=delta_rank,
                n_blocks=n_slices,
                delta_block_index=delta_slice_index,
                rank_ops=rank_ops,
            )
        )
        phase_id += 1

    # AllGather
    for step in range(n_steps):
        delta_rank = 1 << (n_steps - 1 - step)
        n_slices = (rank_size - 1 + (1 << (n_steps - 1 - step))) // (1 << (n_steps - step))
        delta_slice_index = 1 << (n_steps - step)

        rank_ops = []
        for r in range(rank_size):
            send_to = (r + delta_rank) % rank_size
            recv_from = (r + rank_size - delta_rank) % rank_size

            tx_idx = r
            rx_idx = recv_from
            tx_slices = []
            rx_slices = []
            tx_mapped = []
            rx_mapped = []
            tx_bytes = 0
            rx_bytes = 0

            for _ in range(n_slices):
                tx_slices.append(tx_idx)
                rx_slices.append(rx_idx)

                tx_m = slice_map[tx_idx]
                rx_m = slice_map[rx_idx]
                tx_mapped.append(tx_m)
                rx_mapped.append(rx_m)
                tx_bytes += slice_sizes[tx_m]
                rx_bytes += slice_sizes[rx_m]

                tx_idx = (tx_idx + rank_size - delta_slice_index) % rank_size
                rx_idx = (rx_idx + rank_size - delta_slice_index) % rank_size

            rank_ops.append(
                RankOp(
                    rank=r,
                    send_to=send_to,
                    recv_from=recv_from,
                    tx_blocks=tx_slices,
                    rx_blocks=rx_slices,
                    tx_mapped=tx_mapped,
                    rx_mapped=rx_mapped,
                    tx_bytes=int(tx_bytes),
                    rx_bytes=int(rx_bytes),
                )
            )

        phases.append(
            Phase(
                phase=phase_id,
                stage="AllGather",
                step=step,
                delta_rank=delta_rank,
                n_blocks=n_slices,
                delta_block_index=delta_slice_index,
                rank_ops=rank_ops,
            )
        )
        phase_id += 1

    return Schedule(
        algo="ar_nhr",
        rank_size=rank_size,
        total_bytes=total_bytes,
        n_steps=n_steps,
        block_sizes=slice_sizes,
        tree=tree,
        block_map=slice_map,
        phases=phases,
    )


def pretty_print_nhr_schedule(
    schedule: Schedule,
    *,
    file: IO[str] | None = None,
    show_slicemap: bool = True,
    show_bytes: bool = True,
) -> None:
    pretty_print_schedule(
        schedule,
        file=file,
        show_mapping=bool(show_slicemap),
        show_bytes=bool(show_bytes),
    )


def iter_nhr_step_details(rank_size: int, total_bytes: int):
    """Backward-compatible generator (legacy): yields per-rank ops in phase order."""
    schedule = build_nhr_schedule(rank_size, total_bytes)
    for phase in schedule.phases:
        for op in phase.rank_ops:
            yield (
                phase.phase,
                phase.stage,
                phase.step,
                op.rank,
                op.send_to,
                op.recv_from,
                phase.n_blocks,
                phase.delta_block_index,
                op.tx_blocks,
                op.tx_mapped or [],
                op.tx_bytes,
                op.rx_blocks,
                op.rx_mapped or [],
                op.rx_bytes,
            )

def dump_nhr_debug(
    rank_size: int,
    total_bytes: int,
    *,
    print_slicemap: bool = False,
    print_steps: bool = False,
    file: IO[str] | None = None,
) -> None:
    """Print NHR slice mapping and/or per-step details."""
    rank_size = int(rank_size)
    total_bytes = int(total_bytes)

    if not (print_slicemap or print_steps):
        return

    schedule = build_nhr_schedule(rank_size, total_bytes)

    # 1) slicemap only
    if print_slicemap and not print_steps:
        print(
            f"[NHR] rank_size={schedule.rank_size} total_bytes={schedule.total_bytes} n_steps={schedule.n_steps}",
            file=file,
        )
        if schedule.block_sizes is not None:
            print("[NHR] slice sizes (bytes) by mapped index:", file=file)
            for i, sz in enumerate(schedule.block_sizes):
                print(f"  mapped_slice[{i}] = {sz}", file=file)
        if schedule.tree is not None:
            print("[NHR] tree (index -> oldRank):", file=file)
            print("  ", schedule.tree, file=file)
        if schedule.block_map is not None:
            print("[NHR] slice_map (oldRank -> mapped_index):", file=file)
            print("  ", schedule.block_map, file=file)
        return

    # 2) steps (optionally with slicemap)
    if print_steps:
        pretty_print_nhr_schedule(
            schedule,
            file=file,
            show_slicemap=bool(print_slicemap),
            show_bytes=True,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pretty-print a sample NHR schedule")
    parser.add_argument("-n", "--rank-size", type=int, default=4, help="Total ranks")
    parser.add_argument("-b", "--bytes", type=int, default=1024, help="Total bytes per rank")
    args = parser.parse_args()

    schedule = build_nhr_schedule(args.rank_size, args.bytes)
    pretty_print_nhr_schedule(schedule)
