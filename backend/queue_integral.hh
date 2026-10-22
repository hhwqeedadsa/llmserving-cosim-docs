#pragma once
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace moe_cosim {
// Passive post-change occupancy. Each native hook is normalized by the adapter.
// No per-packet history, sampling timer, or policy input is needed.
class QueueIntegral {
 public:
  void Change(int64_t now,uint64_t post_bytes) {
    area_=Area(now);last_=now;bytes_=post_bytes;
    if(bytes_>peak_)peak_=bytes_;
    ++events_;
  }
  uint64_t Area(int64_t now) const {
    if(now<last_ || now<0)throw std::invalid_argument("Queue integral clock moved backwards");
    uint64_t dt=static_cast<uint64_t>(now-last_);
    if(bytes_ && dt>(std::numeric_limits<uint64_t>::max()-area_)/bytes_)
      throw std::overflow_error("Queue byte-nanosecond integral overflow");
    return area_+bytes_*dt;
  }
  uint64_t Bytes() const {return bytes_;}
  uint64_t Peak() const {return peak_;}
  uint64_t Events() const {return events_;}
 private:
  int64_t last_=0;
  uint64_t bytes_=0,peak_=0,area_=0,events_=0;
};
}
