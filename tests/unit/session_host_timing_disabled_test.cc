// A disabled observer must never call either observation clock.
#define clock_gettime forbidden_clock_gettime
#include "host_timing.hh"
#undef clock_gettime
#include <iostream>
static unsigned calls=0;
extern "C" int forbidden_clock_gettime(clockid_t,struct timespec*) noexcept {++calls;return -1;}
int main() {
  moe_rdma::HostTiming observer("");
  {auto outer=observer.Span("disabled");{auto inner=observer.Span("nested");}
    observer.Snapshot("disabled",{{"value",1}});}
  observer.Complete();
  if(calls || observer.Enabled())return 1;
  std::cout<<"{\"passed\":true,\"disabled_observation_clock_calls\":0,\"network_executed\":false}\n";
}
