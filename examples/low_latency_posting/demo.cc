#include "rdma/low_latency_posting.hh"
#include <iostream>
#include <string>

static unsigned Number(const char* raw) {
  const std::string text(raw);
  if(text.empty() || text.find_first_not_of("0123456789")!=std::string::npos)
    throw std::invalid_argument("arguments must be nonnegative decimal integers");
  const auto value=std::stoul(text);
  if(value>63)throw std::invalid_argument("example supports at most 63 puts");
  return static_cast<unsigned>(value);
}

int main(int argc,char** argv) {
  try {
    if(argc!=3)throw std::invalid_argument("usage: low-latency-posting-demo PUTS KEY_LAYOUT_1_TO_3");
    const auto count=Number(argv[1]),layout=Number(argv[2]);
    if(layout<1 || layout>3)throw std::invalid_argument("key layout must be 1, 2 or 3");
    const std::vector<moe_rdma::RegisteredSpan> keys{{0,32768},{32768,65536}};
    const uint64_t local=layout==1?0:(layout==2?30000:25000),remote=layout==1?0:30000;
    const auto pieces=moe_rdma::SplitRegisteredPut(local,remote,14352,keys,keys);
    moe_rdma::LowLatencyPostingQueue q(256,256);
    for(unsigned i=0;i<count;++i) {
      const auto op=q.ReservePut(i,i,pieces);
      for(auto ticket=op.begin;ticket<op.end;++ticket)q.Prepare(ticket);
      q.Post(i);
    }
    const auto before=q.Snapshot();
    const auto marker=q.ReserveNotification(1000);q.Prepare(marker.begin);q.Post(1000);
    const auto after=q.Snapshot();
    uint64_t consumed=0;while(q.ConsumePublished())++consumed;
    const auto before_completion=q.Snapshot();
    // Explicit synthetic local completion supplied by this demo, not a NIC ACK.
    q.CompleteThrough(consumed);q.Close();
    std::cout << "{\"passed\":true,\"mode\":\"deepep_v1.2.1_sync_batch4_reference\",\"puts\":" << count
      << ",\"WQEs_per_put\":" << pieces.size() << ",\"before_notification\":{\"reserved\":" << before.reserved
      << ",\"ready\":" << before.prepared_prefix << ",\"published\":" << before.published
      << "},\"after_notification\":{\"reserved\":" << after.reserved << ",\"published\":" << after.published
      << "},\"consumed_before_completion\":" << consumed << ",\"completed_before_supplied_callback\":"
      << before_completion.completed << ",\"closed\":true,\"completion_supplied_by_demo\":true,"
      << "\"network_execution\":false,\"hardware_calibrated\":false}\n";
  } catch(const std::exception& error) {
    std::cerr << error.what() << '\n';return 1;
  }
}
