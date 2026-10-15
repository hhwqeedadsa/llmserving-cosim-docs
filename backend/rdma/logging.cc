// Keep this pinned pre-C++20 spdlog_setup header out of ns-3's C++20 units.
#include "astra-sim/common/Logging.hh"
namespace moe_rdma {
void InitializeLogging() { AstraSim::LoggerFactory::init("empty"); }
void ShutdownLogging() { AstraSim::LoggerFactory::shutdown(); }
}
