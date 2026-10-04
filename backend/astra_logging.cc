#include "astra_logging.hh"

// spdlog_setup includes syslog.h; keep its LOG_INFO macro away from ns3::LogLevel.
#include "astra-sim/common/Logging.hh"
#include "spdlog/async.h"
#include "spdlog/sinks/base_sink.h"

#include <future>
#include <stdexcept>

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

class ExecutionArchive final : public spdlog::sinks::base_sink<std::mutex>
{
  public:
    explicit ExecutionArchive(std::shared_ptr<moe_ub::RecordStore> store) : store_(std::move(store)) {}
    void Check()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!failure_.empty()) throw std::runtime_error("ASTRA execution archive: " + failure_);
    }
    moe_ub::RecordFile Drain()
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!failure_.empty()) throw std::runtime_error("ASTRA execution archive: " + failure_);
        auto file = store_->Seal(Name()); ++chunk_; return file;
    }
  protected:
    void sink_it_(const spdlog::details::log_msg& message) override
    {
        if (!failure_.empty()) return;
        const std::string_view payload(message.payload.data(), message.payload.size());
        constexpr std::string_view issue = "issue,sys->id=", callback = "callback,sys->id=";
        if (payload.substr(0, issue.size()) != issue && payload.substr(0, callback.size()) != callback) return;
        try { store_->Append(Name(), payload); }
        // spdlog catches sink exceptions on its worker. Surface the failure on
        // the simulation thread at the next flush instead of losing records.
        catch (const std::exception& error) { failure_ = error.what(); }
    }
    void flush_() override {}
  private:
    std::string Name() const { return "astra-" + std::to_string(chunk_) + ".log.gz"; }
    std::shared_ptr<moe_ub::RecordStore> store_;
    uint64_t chunk_ = 0;
    std::string failure_;
};
std::shared_ptr<ExecutionArchive> archive;
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
    archive.reset();
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
    if (archive) archive->Check();
}

void EnableAstraExecutionArchive(std::shared_ptr<RecordStore> store)
{
    if (!store || archive) throw std::logic_error("ASTRA archive needs one record store");
    archive = std::make_shared<ExecutionArchive>(std::move(store));
    archive->set_level(spdlog::level::debug);
    AstraSim::LoggerFactory::get_logger("workload")->sinks().push_back(archive);
}
RecordFile DrainAstraExecutionArchive()
{
    if (!archive) throw std::logic_error("ASTRA execution archive is disabled");
    FlushAstraLogging(); return archive->Drain();
}
}
