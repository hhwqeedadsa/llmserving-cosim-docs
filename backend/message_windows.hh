#pragma once
#include "queue_windows.hh"
#include "message_categories.hh"
#include <array>
#include <map>
#include <set>

namespace moe_ub {
struct ByteCounts {
    uint64_t packets=0,whole=0,business=0,control=0,overhead=0,metadata=0,padding=0;
};
using MessageBytes=std::array<uint64_t,5>;
using MessageAttribution=std::map<std::pair<std::string,MessageCategory>,uint64_t>;
inline void AddBytes(uint64_t& value,uint64_t extra) {
    QueueRequire(extra<=UINT64_MAX-value,"Observation aggregate counter overflow");value+=extra;
}
class MessageWindows {
public:
    using Sink=std::function<void(uint64_t,const ByteCounts&,const MessageAttribution&)>;
    MessageWindows(QueueSampling sampling,std::set<MessageCategory> retained,uint64_t max_entries,Sink sink)
        :sampling_(sampling),retained_(std::move(retained)),limit_(max_entries),sink_(std::move(sink)) {}
    void Add(uint64_t now,uint64_t whole,const MessageBytes& application,uint64_t padding,const std::string& operation) {
        QueueRequire(!finished_,"Packet after aggregate seal");const auto local=sampling_.clock.At(now);
        if(local<sampling_.start_ns || local>=sampling_.end_ns) return;
        const uint64_t index=uint64_t((__int128(local)-sampling_.start_ns)/sampling_.period_ns);
        QueueRequire(!index_ || index>=*index_,"Unordered packet aggregation");
        if(index_ && index!=*index_) Flush();
        index_=index;
        uint64_t metadata=0;for(size_t i=1;i<application.size();++i) AddBytes(metadata,application[i]);
        const auto business=application[0];
        QueueRequire(business<=whole && metadata<=whole-business && padding<=whole-business-metadata,"Invalid aggregate byte classification");
        AddBytes(row_.packets,1);AddBytes(row_.whole,whole);AddBytes(row_.business,business);AddBytes(row_.metadata,metadata);AddBytes(row_.padding,padding);
        AddBytes(packets_,1);AddBytes(bytes_,whole);
        const auto payload=business+metadata+padding;AddBytes(payload?row_.overhead:row_.control,whole-payload);
        for(const auto kind:retained_) if(const auto bytes=application.at(static_cast<size_t>(kind))) {
            QueueRequire(!operation.empty(),"Application attribution lacks actual operation");
            const auto key=std::make_pair(operation,kind);
            if(!attribution_.contains(key)) QueueRequire(attribution_.size()<limit_,"Window application attribution entry budget exceeded");
            AddBytes(attribution_[key],bytes);
        }
        peak_entries_=std::max(peak_entries_,uint64_t(attribution_.size()));
    }
    void Finish() {if(!finished_) {Flush();finished_=true;}}
    uint64_t PeakEntries() const {return peak_entries_;}
    uint64_t Packets() const {return packets_;}
    uint64_t Bytes() const {return bytes_;}
private:
    void Flush() {if(index_) sink_(*index_,row_,attribution_);index_.reset();row_={};attribution_.clear();}
    QueueSampling sampling_;
    std::set<MessageCategory> retained_;
    uint64_t limit_,peak_entries_=0,packets_=0,bytes_=0;
    Sink sink_;
    std::optional<uint64_t> index_;
    ByteCounts row_;
    MessageAttribution attribution_;
    bool finished_=false;
};

// Lazy boundary snapshots: no scheduled events. Initial is the left-limit
// state, not a synthetic peak; final closes the last constant-state interval.
class DetailQueueState {
public:
    using Sink=std::function<void(const char*,uint64_t,uint64_t,uint64_t)>;
    DetailQueueState(uint64_t start,uint64_t end,uint64_t voq,uint64_t fifo,Sink sink)
        :start_(start),end_(end),voq_(voq),fifo_(fifo),sink_(std::move(sink)) {
        QueueRequire(start<end,"Invalid detailed state interval");Advance(0);
    }
    void Update(uint64_t now,std::optional<uint64_t> voq,std::optional<uint64_t> fifo) {
        Advance(now);if(voq) voq_=*voq;if(fifo) fifo_=*fifo;
        if(begun_ && !ended_) sink_("update",now,voq_,fifo_);
    }
    void Finish(uint64_t now) {
        Advance(now);
        if(!ended_) {QueueRequire(!voq_ && !fifo_,"Nonempty queue past actual execution");Advance(end_);}
    }
private:
    void Advance(uint64_t now) {
        QueueRequire(now>=last_,"Unordered detailed queue state");last_=now;
        if(!begun_ && now>=start_) {sink_("initial",start_,voq_,fifo_);begun_=true;}
        if(!ended_ && now>=end_) {sink_("final",end_,voq_,fifo_);ended_=true;}
    }
    uint64_t start_,end_,voq_,fifo_,last_=0;
    Sink sink_;
    bool begun_=false,ended_=false;
};
}
