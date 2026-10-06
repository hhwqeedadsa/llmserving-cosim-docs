#pragma once

#include <algorithm>
#include <cstdint>
#include <functional>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>

namespace moe_ub {
inline void QueueRequire(bool ok,const char* message) {
    if(!ok) throw std::runtime_error(message);
}
struct LocalSamplingClock {
    int64_t offset_ns=0,drift_ppb=0;
    int64_t At(uint64_t global_ns) const {
        QueueRequire(global_ns<=uint64_t(INT64_MAX) && drift_ppb>-1000000000 && drift_ppb<=1000000000,
                     "Invalid queue sampling clock");
        const __int128 local=__int128(1000000000+drift_ppb)*global_ns/1000000000+offset_ns;
        QueueRequire(local>=INT64_MIN && local<=INT64_MAX,"Queue sampling clock overflow");
        return int64_t(local);
    }
};
struct QueueSampling {
    int64_t start_ns,end_ns,period_ns;
    LocalSamplingClock clock;
    uint64_t Count() const { return uint64_t((__int128(end_ns)-start_ns-1)/period_ns+1); }
};
struct QueueWindow {
    unsigned __int128 voq_byte_ns=0,fifo_byte_ns=0;
    uint64_t voq_peak_bytes=0,fifo_peak_bytes=0;
    bool Nonzero() const { return voq_peak_bytes || fifo_peak_bytes || voq_byte_ns || fifo_byte_ns; }
};
inline std::string QueueAreaText(unsigned __int128 value) {
    if(!value) return "0";
    std::string result;
    while(value) { result.push_back('0'+value%10);value/=10; }
    std::reverse(result.begin(),result.end());return result;
}

// One current window. Zero stretches are skipped; persistent waiting is split
// across actual local-clock window lengths. Peak includes inherited boundary
// state and all after-update states inside the half-open window (same-time
// intermediate states included). No simulation events or protocol queries.
class QueueWindows {
public:
    using Sink=std::function<void(uint64_t,const QueueWindow&)>;
    QueueWindows(QueueSampling sampling,uint64_t initial_time,uint64_t voq,uint64_t fifo,Sink sink)
        : sampling_(sampling),last_(sampling.clock.At(initial_time)),voq_(voq),fifo_(fifo),sink_(std::move(sink)) {
        QueueRequire(sampling.start_ns<sampling.end_ns && sampling.period_ns>0 &&
                     sampling.start_ns>=last_ && bool(sink_),"Invalid queue observation bounds or missing initial state");
    }
    void Update(uint64_t global_ns,std::optional<uint64_t> voq,std::optional<uint64_t> fifo) {
        QueueRequire(!finished_,"Queue update after export");
        const auto now=sampling_.clock.At(global_ns);Advance(now);
        if(voq) voq_=*voq;
        if(fifo) fifo_=*fifo;
        if(now>=sampling_.start_ns && now<sampling_.end_ns) {
            Open(Index(now));Peak();
        }
    }
    void Finish(uint64_t execution_end_ns) {
        if(finished_) return;
        const auto now=sampling_.clock.At(execution_end_ns);Advance(now);
        QueueRequire(now>=sampling_.end_ns || (voq_==0 && fifo_==0),
                     "Cannot extend nonempty queue beyond actual execution");
        Advance(std::max(now,sampling_.end_ns));Flush();finished_=true;
    }
    uint64_t Voq() const { return voq_; }
    uint64_t Fifo() const { return fifo_; }
private:
    uint64_t Index(int64_t time) const { return uint64_t((__int128(time)-sampling_.start_ns)/sampling_.period_ns); }
    int64_t Right(uint64_t index) const {
        return int64_t(std::min(__int128(sampling_.end_ns),__int128(sampling_.start_ns)+__int128(index+1)*sampling_.period_ns));
    }
    void Peak() { row_.voq_peak_bytes=std::max(row_.voq_peak_bytes,voq_);row_.fifo_peak_bytes=std::max(row_.fifo_peak_bytes,fifo_); }
    void Open(uint64_t index) {
        if(index_ && *index_!=index) Flush();
        if(!index_) { index_=index;row_={};Peak(); }
    }
    void Flush() {
        if(index_ && row_.Nonzero()) sink_(*index_,row_);
        index_.reset();row_={};
    }
    void Advance(int64_t now) {
        QueueRequire(now>=last_,"Queue clock moved backwards");
        auto cursor=std::max(last_,sampling_.start_ns);const auto end=std::min(now,sampling_.end_ns);
        while(cursor<end) {
            if(!voq_ && !fifo_) {
                if(index_ && end>=Right(*index_)) Flush();
                break;
            }
            const auto index=Index(cursor);Open(index);
            const auto stop=std::min(end,Right(index));const auto duration=uint64_t(__int128(stop)-cursor);
            row_.voq_byte_ns+=static_cast<unsigned __int128>(voq_)*duration;
            row_.fifo_byte_ns+=static_cast<unsigned __int128>(fifo_)*duration;Peak();
            cursor=stop;if(cursor==Right(index)) Flush();
        }
        last_=now;
    }
    QueueSampling sampling_;
    int64_t last_;
    uint64_t voq_,fifo_;
    Sink sink_;
    std::optional<uint64_t> index_;
    QueueWindow row_;
    bool finished_=false;
};
}
