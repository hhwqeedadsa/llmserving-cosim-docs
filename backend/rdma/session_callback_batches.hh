#pragma once
#include "session_wakeups.hh"

namespace moe_rdma {
// Records actual collection boundaries; never schedules simulator events.
class SessionCallbackBatches {
 public:
  using Value=nlohmann::json;
  bool Add(int64_t now,const Value& events,bool drained) {
    Value row={{"now_ns",now},{"events",events},{"drained",drained}};
    const auto bytes=row.dump().size();
    SessionWakeups::Check(bytes<=65536 && events.size()<=512,"session callback single batch budget exceeded");
    SessionWakeups::Check(rows_.size()<64 && bytes_<65536 && callbacks_<512,"callback buffer was not flushed at high water");
    rows_.push_back(row);bytes_+=bytes;callbacks_+=events.size();
    SessionWakeups::Check(bytes_<=131072 && callbacks_<=1024,"session callback buffer budget exceeded");
    peak_bytes_=std::max(peak_bytes_,bytes_);peak_batches_=std::max(peak_batches_,rows_.size());
    peak_callbacks_=std::max(peak_callbacks_,callbacks_);++logical_;
    SessionWakeups::Check(logical_<=16384,"session callback logical step budget exceeded");
    return rows_.size()>=64 || bytes_>=65536 || callbacks_>=512;
  }
  const Value& Rows() const {return rows_;}
  void Clear(){rows_=Value::array();bytes_=0;callbacks_=0;}
  Value Report() const {
    return {{"mode","callback_batches/1"},{"logical_steps",logical_},{"pending_batches",rows_.size()},
      {"peak_batches",peak_batches_},{"peak_compact_batch_bytes",peak_bytes_},{"peak_callbacks",peak_callbacks_},
      {"physical_decision_limit",256},{"logical_step_limit",16384}};
  }
 private:
  Value rows_=Value::array();
  std::size_t bytes_=0,callbacks_=0,peak_bytes_=0,peak_batches_=0,peak_callbacks_=0,logical_=0;
};
}
