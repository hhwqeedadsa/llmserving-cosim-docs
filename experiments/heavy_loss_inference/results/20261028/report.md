# 重度丢包对照：离线复核

| 条件 | 通过 | 服务完成(ns) | 相对基线(ns) | 目标确认(ns) | 目标确认差(ns) | 重复载荷(B) | 丢弃命中 | 组0波次 | 组1波次 | 完成请求 |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | True | 54362 | 0 | 17673 | 0 | 0 | [] | 2/2 | 2/2 | 32/32 |
| data-57-transient | True | 91297 | 36935 | 57154 | 39481 | 12288 | [2, 2, 2, 2] | 2/2 | 2/2 | 32/32 |
| data-57-persistent | False | 444203 | 389841 | None | 0 | 24576 | [5, 5, 5, 5] | 0/1 | 2/2 | 16/32 |
| data-86-transient | True | 91297 | 36935 | 57154 | 39481 | 12288 | [2, 2, 2, 2, 2, 2] | 2/2 | 2/2 | 32/32 |
| ack-57-transient | True | 54362 | 0 | 17673 | 0 | 0 | [1, 1, 1, 1] | 2/2 | 2/2 | 32/32 |

失败条件：data-57-persistent={"message_id": 1, "reason": "recovery_retry_exhausted", "scope": "whole shared simulation stopped; no successful callbacks or credits synthesized; live resources retained for diagnosis until teardown", "time_ns": 444203}
