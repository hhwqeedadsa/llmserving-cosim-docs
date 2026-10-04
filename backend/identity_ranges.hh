#pragma once
#include <cstdint>
#include <map>

namespace moe_ub
{
// Preserve exact run-wide uniqueness after graph retirement, including gaps and
// nonmonotonic IDs. Consecutive allocated identities occupy one interval.
class IdentityRanges
{
  public:
    bool count(uint64_t value) const
    {
        auto next = ranges_.upper_bound(value);
        return next != ranges_.begin() && std::prev(next)->second >= value;
    }
    bool insert(uint64_t value)
    {
        auto next = ranges_.upper_bound(value);
        auto previous = next == ranges_.begin() ? ranges_.end() : std::prev(next);
        if (previous != ranges_.end() && previous->second >= value) return false;
        const bool left = previous != ranges_.end() && previous->second < UINT64_MAX && previous->second + 1 == value;
        const bool right = next != ranges_.end() && value < UINT64_MAX && next->first == value + 1;
        if (left)
        {
            previous->second = right ? next->second : value;
            if (right) ranges_.erase(next);
        }
        else if (right)
        {
            const auto last = next->second; ranges_.erase(next); ranges_.emplace(value, last);
        }
        else ranges_.emplace(value, value);
        ++size_; return true;
    }
    uint64_t size() const { return size_; }
    size_t Intervals() const { return ranges_.size(); }
  private:
    std::map<uint64_t, uint64_t> ranges_;
    uint64_t size_ = 0;
};
}
