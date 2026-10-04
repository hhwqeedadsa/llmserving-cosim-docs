#pragma once
#include <cstdint>
#include <map>
#include <utility>

namespace moe_ub
{
struct NodeTimes
{
    uint64_t start_ns = 0, end_ns = 0;
    bool started = false, finished = false;
};
using ExecutionTimes = std::map<std::pair<int, uint64_t>, NodeTimes>;
}
