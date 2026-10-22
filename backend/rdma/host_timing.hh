#pragma once
#include "json/json.hpp"
#include <ctime>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

namespace moe_rdma {
// Linux host observation only. Never reads/updates ns-3 time or schedules an
// event. CPU is this process (including its threads), not descendants.
class HostTiming {
 public:
  explicit HostTiming(const std::string& path) {
    if(path.empty())return;
    std::ifstream old(path);if(old.good())throw std::runtime_error("host timing output exists");
    out_.open(path);if(!out_)throw std::runtime_error("cannot open host timing output");
    enabled_=true;
    Write({{"type","header"},{"schema_version","moe-host-phase-trace/1"},
      {"writer","native"},{"pid",getpid()},{"wall_clock","CLOCK_MONOTONIC"},
      {"cpu_clock","CLOCK_PROCESS_CPUTIME_ID"},{"unit","ns"}});
  }
  class Scope {
   public:
    Scope(HostTiming& trace,const char* name):trace_(trace),id_(trace.Begin(name)) {}
    Scope(const Scope&)=delete;
    ~Scope() noexcept {trace_.End(id_);}
   private: HostTiming& trace_;uint64_t id_;
  };
  Scope Span(const char* name) {return Scope(*this,name);}
  bool Enabled() const {return enabled_;}
  void Snapshot(const char* name,nlohmann::json values) {
    if(enabled_)Write({{"type","snapshot"},{"name",name},{"wall_ns",Clock(CLOCK_MONOTONIC)},
      {"cpu_ns",Clock(CLOCK_PROCESS_CPUTIME_ID)},{"values",std::move(values)}});
  }
  void Complete() {
    if(!enabled_)return;
    if(failed_ || !stack_.empty())throw std::runtime_error("incomplete host timing trace");
    Write({{"type","footer"},{"complete",true},{"spans",next_}});
    out_.close();if(!out_)throw std::runtime_error("cannot close host timing output");
    enabled_=false;
  }
 private:
  static int64_t Clock(clockid_t kind) {
    timespec value{};
    if(clock_gettime(kind,&value))throw std::runtime_error("host clock failed");
    return int64_t(value.tv_sec)*1000000000+value.tv_nsec;
  }
  void Write(const nlohmann::json& row) {
    if(++records_>65536)throw std::runtime_error("host timing record budget exceeded");
    out_<<row.dump()<<'\n';out_.flush();
    if(!out_)throw std::runtime_error("cannot write host timing record");
  }
  uint64_t Begin(const char* name) {
    if(!enabled_)return 0;
    const auto id=++next_;
    Write({{"type","begin"},{"id",id},{"parent",stack_.empty()?0:stack_.back()},
      {"name",name},{"wall_ns",Clock(CLOCK_MONOTONIC)},
      {"cpu_ns",Clock(CLOCK_PROCESS_CPUTIME_ID)}});
    stack_.push_back(id);return id;
  }
  void End(uint64_t id) noexcept {
    if(!id)return;
    try {
      if(stack_.empty() || stack_.back()!=id)throw std::runtime_error("host timing nesting changed");
      Write({{"type","end"},{"id",id},{"wall_ns",Clock(CLOCK_MONOTONIC)},
        {"cpu_ns",Clock(CLOCK_PROCESS_CPUTIME_ID)}});
      stack_.pop_back();
    }catch(...){failed_=true;}
  }
  std::ofstream out_;std::vector<uint64_t> stack_;
  uint64_t next_=0,records_=0;bool enabled_=false,failed_=false;
};
}
