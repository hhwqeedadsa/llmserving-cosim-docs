// Keep this pinned pre-C++20 spdlog_setup header out of ns-3's C++20 units.
#include "astra-sim/common/Logging.hh"
#include "spdlog/sinks/basic_file_sink.h"
#include "spdlog/sinks/rotating_file_sink.h"
namespace moe_rdma {
void InitializeLogging() {
  AstraSim::LoggerFactory::init("empty");
  // Upstream diagnostics rotate at 10 x 10 MiB. Node events are evidence,
  // so preserve them independently; host_probe bounds cumulative disk usage.
  // The pinned async logger uses blocking overflow, not dropping events.
  auto sink=std::make_shared<spdlog::sinks::basic_file_sink_mt>("log/astra_nodes.log",false);
  sink->set_level(spdlog::level::debug);
  sink->set_pattern("%v");
  auto workload=AstraSim::LoggerFactory::get_logger("workload");
  // Retain info/error diagnostics, but do not duplicate every audit event in
  // a lossy rotating sink. This changes observation only, never the clock.
  for(auto& diagnostic:workload->sinks())
    if(std::dynamic_pointer_cast<spdlog::sinks::rotating_file_sink_mt>(diagnostic) &&
       diagnostic->level()==spdlog::level::debug)
      diagnostic->set_level(spdlog::level::info);
  workload->sinks().push_back(sink);
}
void ShutdownLogging() { AstraSim::LoggerFactory::shutdown(); }
}
