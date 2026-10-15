// Allocation-only probe for locked ns-3-alibabacloud; no links, packets or service.
#include "ns3/core-module.h"
#include "ns3/switch-node.h"
#include "ns3/nvswitch-node.h"
#include "ns3/switch-mmu.h"
#include <fstream>
#include <iostream>
#include <unistd.h>
#include <vector>

static uint64_t Rss() {
  uint64_t total = 0, resident = 0;
  std::ifstream("/proc/self/statm") >> total >> resident;
  return resident * static_cast<uint64_t>(sysconf(_SC_PAGESIZE));
}
int main(int argc, char** argv) {
  unsigned count = 0;
  ns3::CommandLine cmd;
  cmd.AddValue("count", "Allocate 0..16 switches, without network traffic", count);
  cmd.Parse(argc, argv);
  if (count > 16) return 2;
  const auto before = Rss();
  std::vector<ns3::Ptr<ns3::SwitchNode>> nodes;
  for (unsigned i = 0; i < count; ++i) nodes.push_back(ns3::CreateObject<ns3::SwitchNode>());
  std::cout << "{\"scope\":\"allocation only; no thousand-GPU simulation\",\"switch_count\":" << count
            << ",\"sizeof_switch_node\":" << sizeof(ns3::SwitchNode)
            << ",\"sizeof_nvswitch_node\":" << sizeof(ns3::NVSwitchNode)
            << ",\"sizeof_switch_mmu\":" << sizeof(ns3::SwitchMmu)
            << ",\"rss_before_bytes\":" << before << ",\"rss_after_bytes\":" << Rss() << "}\n";
  nodes.clear();
  ns3::Simulator::Destroy();
}
