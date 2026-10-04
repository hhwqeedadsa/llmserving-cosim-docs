#pragma once

#include <cstdint>
#include <filesystem>
#include <functional>
#include <list>
#include <map>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

namespace moe_ub
{
struct RecordFile
{
    std::string name;
    uint64_t records = 0, raw_bytes = 0, compressed_bytes = 0;
    uint32_t crc32 = 0;
    bool sealed = false;
};

// Lossless newline records in concatenated gzip members. A bounded shared LRU
// buffer replaces one compressor/file handle per observed port. The disk limit
// counts encoded bytes before writing, including gzip headers and checksums.
// Explicit sealing is required; destruction never silently publishes success.
class RecordStore
{
  public:
    RecordStore(std::filesystem::path directory, uint64_t max_bytes,
                uint64_t max_buffer_bytes, uint64_t max_record_bytes = 1048576,
                uint64_t chunk_bytes = 65536);
    void Append(const std::string& name, std::string_view record);
    RecordFile Seal(const std::string& name);
    std::vector<RecordFile> SealAll();
    std::vector<RecordFile> Files() const;
    uint64_t Bytes() const;
    uint64_t PeakBufferedBytes() const;
    const std::filesystem::path& Directory() const { return directory_; }
  private:
    struct Buffer { std::string name, data; };
    struct State
    {
        RecordFile file;
        bool buffered = false;
        std::list<Buffer>::iterator buffer;
    };
    State& Get(const std::string& name);
    void Flush(State& state);
    void CheckHealthy() const;
    const std::filesystem::path directory_;
    const uint64_t max_bytes_, max_buffer_, max_record_, chunk_;
    mutable std::mutex mutex_;
    std::map<std::string, State> files_;
    std::list<Buffer> buffers_;
    uint64_t bytes_ = 0, buffered_ = 0, peak_buffered_ = 0;
    bool failed_ = false;
};

// Checks encoded size, all gzip checksums, raw bytes/CRC, record count and final
// newline. It never collects the complete file. The caller owns semantic checks.
void ReadRecords(const std::filesystem::path& directory, const RecordFile& file,
                 const std::function<void(const std::string&)>& consume,
                 uint64_t max_record_bytes = 1048576);
}
