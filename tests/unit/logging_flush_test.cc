#include "astra_logging.hh"
#include "astra-sim/common/Logging.hh"
#include "spdlog/sinks/base_sink.h"

#include <chrono>
#include <fstream>
#include <future>
#include <iostream>
#include <stdexcept>

namespace
{
class Gate final : public spdlog::sinks::base_sink<std::mutex>
{
  public:
    std::promise<void> entered, release;
  protected:
    void sink_it_(const spdlog::details::log_msg&) override
    {
        entered.set_value();
        release.get_future().get();
    }
    void flush_() override {}
};
}

int main()
{
    using namespace std::chrono_literals;
    moe_ub::InitializeAstraLogging();
    auto logger = AstraSim::LoggerFactory::get_logger("cosim-flush-test");
    auto gate = std::make_shared<Gate>();
    logger->sinks().insert(logger->sinks().begin(), gate);
    logger->debug("queued-before-export");
    gate->entered.get_future().get();
    std::promise<void> started;
    auto flushed = std::async(std::launch::async, [&]() {
        started.set_value();
        moe_ub::FlushAstraLogging();
    });
    started.get_future().get();
    const bool waited = flushed.wait_for(50ms) == std::future_status::timeout;
    gate->release.set_value();
    flushed.get();
    std::ifstream input("log/log.log");
    const std::string contents((std::istreambuf_iterator<char>(input)), {});
    const bool saved = contents.find("queued-before-export") != std::string::npos;
    moe_ub::ShutdownAstraLogging();
    if (!waited || !saved)
    {
        std::cerr << "Flush returned before the queued execution log reached disk\n";
        return 1;
    }
    std::cout << "PASS: flush waits for a blocked asynchronous writer and exports complete records\n";
    return 0;
}
