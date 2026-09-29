#include "astra_logging.hh"

// spdlog_setup includes syslog.h; keep its LOG_INFO macro away from ns3::LogLevel.
#include "astra-sim/common/Logging.hh"
#include "spdlog/async.h"
#include "spdlog/sinks/base_sink.h"

#include <future>

namespace
{
class FlushBarrier final : public spdlog::sinks::base_sink<std::mutex>
{
  public:
    std::promise<void> completed;
  protected:
    void sink_it_(const spdlog::details::log_msg&) override {}
    void flush_() override { completed.set_value(); }
};
}

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
    // The locked LoggerFactory uses one asynchronous worker. A flush only
    // enqueues work; wait for a FIFO barrier before reading execution records.
    auto barrier = std::make_shared<FlushBarrier>();
    auto completed = barrier->completed.get_future();
    auto logger = std::make_shared<spdlog::async_logger>("cosim-flush-barrier", barrier,
        spdlog::thread_pool(), spdlog::async_overflow_policy::block);
    logger->flush();
    completed.get();
}
}
