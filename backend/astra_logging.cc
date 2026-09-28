#include "astra_logging.hh"

// spdlog_setup includes syslog.h; keep its LOG_INFO macro away from ns3::LogLevel.
#include "astra-sim/common/Logging.hh"

namespace moe_ub
{
void InitializeAstraLogging()
{
    AstraSim::LoggerFactory::init("empty");
}

void ShutdownAstraLogging()
{
    AstraSim::LoggerFactory::shutdown();
}

void FlushAstraLogging()
{
    spdlog::apply_all([](std::shared_ptr<spdlog::logger> logger) { logger->flush(); });
}
}
