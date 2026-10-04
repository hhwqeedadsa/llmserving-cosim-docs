#include "record_store.hh"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace fs = std::filesystem;
void Check(bool ok) { if (!ok) throw std::runtime_error("record store assertion failed"); }
template<class F> void Reject(F call)
{
    bool failed = false; try { call(); } catch (const std::exception&) { failed = true; } Check(failed);
}
int main(int argc, char** argv)
{
    Check(argc == 2); const fs::path root(argv[1]); Check(!fs::exists(root)); fs::create_directories(root);
    moe_ub::RecordStore store(root / "records", 1000000, 320, 128, 64);
    Reject([&] { store.Append("../outside.gz", "bad"); });
    Reject([&] { store.Append("bad.gz", "two\nrows"); });
    Reject([&] { store.Append("bad.gz", std::string(128, 'x')); });
    for (unsigned round = 0; round < 150; ++round)
        for (unsigned port = 0; port < 23; ++port)
            store.Append("port-" + std::to_string(port) + ".jsonl.gz", std::to_string(round) + ":" + std::to_string(port));
    const auto empty = store.Seal("empty.jsonl.gz");
    Check(empty.records == 0 && empty.compressed_bytes > 0);
    const auto files = store.SealAll();
    Check(files.size() == 24 && store.PeakBufferedBytes() <= 320);
    uint64_t total = 0;
    for (const auto& file : files)
    {
        total += fs::file_size(root / "records" / file.name); unsigned index = 0;
        moe_ub::ReadRecords(root / "records", file, [&](const auto& row) {
            const auto port = file.name.substr(5, file.name.find('.') - 5);
            Check(row == std::to_string(index++) + ":" + port);
        }, 128);
        Check(index == file.records);
    }
    Check(total == store.Bytes());
    Reject([&] { store.Append(files.back().name, "after-seal"); });
    auto file = store.Seal("port-0.jsonl.gz");
    auto bad = file; ++bad.records;
    Reject([&] { moe_ub::ReadRecords(root / "records", bad, [](const auto&) {}); });
    bad = file; bad.crc32 ^= 1;
    Reject([&] { moe_ub::ReadRecords(root / "records", bad, [](const auto&) {}); });
    { std::fstream data(root / "records" / file.name, std::ios::in | std::ios::out | std::ios::binary);
      data.seekg(15); const char corrupted = data.get() ^ 0x80; data.seekp(15); data.put(corrupted); }
    Reject([&] { moe_ub::ReadRecords(root / "records", file, [](const auto&) {}); });
    file = store.Seal("port-1.jsonl.gz");
    fs::resize_file(root / "records" / file.name, file.compressed_bytes - 4); file.compressed_bytes -= 4;
    Reject([&] { moe_ub::ReadRecords(root / "records", file, [](const auto&) {}); });
    moe_ub::RecordStore limited(root / "limited", 1, 320, 128, 64);
    Reject([&] { limited.Seal("empty.gz"); }); Check(limited.Bytes() == 0);
    Reject([&] { limited.Append("again.gz", "x"); });
    std::cout << "PASS bounded shared buffers, exact gzip records, corruption rejection and disk limit\n";
}
