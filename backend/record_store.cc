#include "record_store.hh"
#include <algorithm>
#include <fstream>
#include <limits>
#include <regex>
#include <stdexcept>
#include <zlib.h>

namespace moe_ub
{
namespace
{
void Require(bool ok, const std::string& message)
{
    if (!ok) throw std::runtime_error("Record store: " + message);
}
bool ValidName(const std::string& name)
{
    // Names are generated internally, not arbitrary paths from a manifest.
    static const std::regex pattern("[A-Za-z0-9_-]+(\\.[A-Za-z0-9_-]+)*\\.gz");
    return std::regex_match(name, pattern);
}
std::vector<unsigned char> Compress(const std::string& text)
{
    z_stream stream{};
    Require(deflateInit2(&stream, Z_BEST_SPEED, Z_DEFLATED, 15 + 16, 8, Z_DEFAULT_STRATEGY) == Z_OK,
            "cannot initialize gzip");
    struct End { z_stream* p; ~End() { deflateEnd(p); } } end{&stream};
    std::vector<unsigned char> result(deflateBound(&stream, text.size()));
    stream.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(text.data()));
    stream.avail_in = text.size(); stream.next_out = result.data(); stream.avail_out = result.size();
    Require(deflate(&stream, Z_FINISH) == Z_STREAM_END && stream.avail_in == 0, "gzip compression failed");
    result.resize(stream.total_out);
    return result;
}
}

RecordStore::RecordStore(std::filesystem::path directory, uint64_t max_bytes,
    uint64_t max_buffer_bytes, uint64_t max_record_bytes, uint64_t chunk_bytes)
    : directory_(std::move(directory)), max_bytes_(max_bytes), max_buffer_(max_buffer_bytes),
      max_record_(max_record_bytes), chunk_(chunk_bytes)
{
    Require(max_bytes_ > 0 && chunk_ > 0 && max_record_ > 0 &&
            max_record_ < UINT32_MAX / 4 && chunk_ < UINT32_MAX / 4 &&
            max_buffer_ >= max_record_ + chunk_, "invalid finite storage limits");
    Require(!std::filesystem::exists(directory_), "output directory already exists");
    Require(std::filesystem::create_directories(directory_), "cannot create output directory");
}
void RecordStore::CheckHealthy() const { Require(!failed_, "an earlier write failed"); }
RecordStore::State& RecordStore::Get(const std::string& name)
{
    auto found = files_.find(name);
    if (found != files_.end()) return found->second;
    Require(ValidName(name), "invalid record name");
    Require(!std::filesystem::exists(directory_ / name), "record file already exists");
    State state; state.file.name = name;
    return files_.emplace(name, std::move(state)).first->second;
}
void RecordStore::Flush(State& state)
{
    if (!state.buffered && state.file.compressed_bytes) return;
    const std::string empty;
    const auto& text = state.buffered ? state.buffer->data : empty;
    try
    {
        const auto encoded = Compress(text);
        Require(encoded.size() <= max_bytes_ - bytes_, "compressed disk budget exceeded");
        // An empty stream is flushed only by Seal/SealAll, under the mutex.
        // It can never receive another append. Share those immutable inodes,
        // while conservatively charging the encoded bytes of every reference.
        if (text.empty() && !empty_source_.empty() && empty_links_ < 32000)
        {
            std::filesystem::create_hard_link(directory_ / empty_source_, directory_ / state.file.name);
            ++empty_links_;
        }
        else
        {
            std::ofstream output(directory_ / state.file.name, std::ios::binary | std::ios::app);
            output.write(reinterpret_cast<const char*>(encoded.data()), encoded.size()); output.close();
            Require(bool(output), "cannot write " + state.file.name);
            // Bounded groups also avoid the filesystem's hard-link count limit.
            if (text.empty()) { empty_source_ = state.file.name; empty_links_ = 1; }
        }
        bytes_ += encoded.size(); state.file.compressed_bytes += encoded.size();
        if (state.buffered)
        {
            buffered_ -= state.buffer->data.size(); buffers_.erase(state.buffer); state.buffered = false;
        }
    }
    catch (...) { failed_ = true; throw; }
}
void RecordStore::Append(const std::string& name, std::string_view record)
{
    std::lock_guard<std::mutex> lock(mutex_); CheckHealthy();
    Require(record.size() < max_record_ && record.find('\n') == std::string_view::npos &&
            record.find('\r') == std::string_view::npos, "oversized or multiline record");
    auto& state = Get(name); Require(!state.file.sealed, "append after seal");
    const uint64_t size = record.size() + 1;
    Require(state.file.raw_bytes <= UINT64_MAX - size && state.file.records < UINT64_MAX, "record count overflow");
    while (buffered_ + size > max_buffer_) Flush(files_.at(buffers_.front().name));
    if (!state.buffered)
    {
        buffers_.push_back({name, {}}); state.buffer = std::prev(buffers_.end()); state.buffered = true;
    }
    else buffers_.splice(buffers_.end(), buffers_, state.buffer);
    state.buffer->data.append(record); state.buffer->data.push_back('\n');
    buffered_ += size; peak_buffered_ = std::max(peak_buffered_, buffered_);
    state.file.raw_bytes += size; ++state.file.records;
    state.file.crc32 = ::crc32(state.file.crc32, reinterpret_cast<const Bytef*>(record.data()), record.size());
    const unsigned char newline = '\n'; state.file.crc32 = ::crc32(state.file.crc32, &newline, 1);
    if (state.buffer->data.size() >= chunk_) Flush(state);
}
RecordFile RecordStore::Seal(const std::string& name)
{
    std::lock_guard<std::mutex> lock(mutex_); CheckHealthy();
    auto& state = Get(name); Flush(state); state.file.sealed = true; return state.file;
}
std::vector<RecordFile> RecordStore::SealAll()
{
    std::lock_guard<std::mutex> lock(mutex_); CheckHealthy();
    std::vector<RecordFile> result;
    for (auto& [name, state] : files_) { Flush(state); state.file.sealed = true; result.push_back(state.file); }
    return result;
}
std::vector<RecordFile> RecordStore::Files() const
{
    std::lock_guard<std::mutex> lock(mutex_); CheckHealthy();
    std::vector<RecordFile> result;
    for (const auto& [name, state] : files_) result.push_back(state.file);
    return result;
}
uint64_t RecordStore::Bytes() const { std::lock_guard<std::mutex> lock(mutex_); return bytes_; }
uint64_t RecordStore::PeakBufferedBytes() const { std::lock_guard<std::mutex> lock(mutex_); return peak_buffered_; }

void ReadRecords(const std::filesystem::path& directory, const RecordFile& file,
    const std::function<void(const std::string&)>& consume, uint64_t max_record_bytes)
{
    Require(file.sealed && ValidName(file.name) && max_record_bytes > 0, "invalid sealed record reference");
    const auto path = directory / file.name;
    Require(std::filesystem::file_size(path) == file.compressed_bytes, "encoded file size mismatch");
    std::ifstream header(path, std::ios::binary);
    Require(header.get() == 0x1f && header.get() == 0x8b, "not gzip data");
    auto stream = gzopen(path.c_str(), "rb"); Require(stream != nullptr, "cannot open gzip records");
    struct End { gzFile p; ~End() { gzclose(p); } } end{stream};
    char chunk[16384]; std::string pending;
    uint64_t bytes = 0, records = 0; uint32_t crc = 0;
    for (;;)
    {
        const int count = gzread(stream, chunk, sizeof(chunk));
        int error = Z_OK; gzerror(stream, &error);
        Require(count >= 0 && (error == Z_OK || error == Z_STREAM_END), "corrupt or truncated gzip records");
        if (!count) break;
        Require(uint64_t(count) <= file.raw_bytes - bytes, "raw byte count exceeded");
        bytes += count; crc = ::crc32(crc, reinterpret_cast<const Bytef*>(chunk), count);
        for (int i = 0; i < count; ++i)
        {
            if (chunk[i] == '\n')
            {
                Require(++records <= file.records, "record count exceeded"); consume(pending); pending.clear();
            }
            else { pending.push_back(chunk[i]); Require(pending.size() < max_record_bytes, "oversized record"); }
        }
    }
    Require(pending.empty() && bytes == file.raw_bytes && records == file.records && crc == file.crc32,
            "record content or count mismatch");
}
}
