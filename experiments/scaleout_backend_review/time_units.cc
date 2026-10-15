// Research probe for ns-3-alibabacloud 3e0c7c1; no ASTRA or UB linkage.
#include "ns3/core-module.h"
#include <iostream>

int main() {
  using namespace ns3;
  const int send_lat_ns = 7200;
  // Same conversion as the reviewed SimAI SendFlow, contrasted with explicit units.
  const Time raw = Time(send_lat_ns * 1000);
  const Time explicit_ns = NanoSeconds(send_lat_ns);
  std::cout << "{\"default_resolution_is_ns\":"
            << (Time::GetResolution() == Time::NS ? "true" : "false")
            << ",\"intended_ns\":" << send_lat_ns
            << ",\"raw_constructor_ns\":" << raw.GetNanoSeconds()
            << ",\"explicit_constructor_ns\":" << explicit_ns.GetNanoSeconds()
            << ",\"events_ns\":[";
  bool first = true;
  auto observe = [&first]() {
    if (!first) std::cout << ',';
    first = false;
    std::cout << Simulator::Now().GetNanoSeconds();
  };
  Simulator::Schedule(explicit_ns, observe);
  Simulator::Schedule(raw, observe);
  Simulator::Run();
  Simulator::Destroy();
  std::cout << "]}\n";
  return raw == explicit_ns * 1000 && explicit_ns.GetNanoSeconds() == send_lat_ns ? 0 : 1;
}
