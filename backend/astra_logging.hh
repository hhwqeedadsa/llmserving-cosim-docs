#pragma once
#include "record_store.hh"
#include <memory>

namespace moe_ub
{
void InitializeAstraLogging();
void FlushAstraLogging();
void ShutdownAstraLogging();
// Add a lossless execution-only archive before workloads are constructed.
// The normal rotating diagnostic sinks remain unchanged. Drain waits for the
// asynchronous logger and seals one chunk; the next event starts a new chunk.
void EnableAstraExecutionArchive(std::shared_ptr<RecordStore> store);
RecordFile DrainAstraExecutionArchive();
}
