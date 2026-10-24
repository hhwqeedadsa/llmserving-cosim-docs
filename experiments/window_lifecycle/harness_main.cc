#undef free
static void numbers(const std::vector<int>& values) {
  std::cout<<'[';for(size_t i=0;i<values.size();++i){if(i)std::cout<<',';std::cout<<values[i];}std::cout<<']';
}
static void run(int refs,int segments,int connections,const std::string& test,int fs,int fc) {
  mode=test;fail_segment=fs;fail_slot=fc;calls.clear();retired.clear();failed_results.clear();events.clear();
  memory_freed=window_freed=false;shadow_lookups=0;
  Shared shared{};ncclComm comm{&shared,{},0};Plugin plugin{mock_dereg};
  shared.ginState.numActiveBackends=1;auto& backend=shared.ginState.backends[0];
  backend.ginCommCount=connections;backend.ncclGin=&plugin;
  Segment storage[3]{};Handle handles[3][4]{};
  for(int s=0;s<segments;++s)for(int c=0;c<connections;++c) {
    handles[s][c]={s,c};storage[s].ginHostWins[c]=&handles[s][c];backend.ginComms[c]=&handles[0][c];
  }
  int allocation_handles[3]{1,2,3};ncclDevrMemory memory{};
  memory.refCount=refs;memory.ginSegmentInfos=storage;memory.numGinSegments=segments;
  memory.numSegments=segments;memory.memHandles=allocation_handles;memory.lsaMaxSize=4096;
  ncclDevrWindow window{&memory,nullptr,reinterpret_cast<void*>(4096)};
  ncclWindow_vidmem device{&window,nullptr};ncclDevCommWindowTable table{};
  table.entries[0].window=&device;ncclDevrWindowSorted sorted[1]{{4096,&window}};
  comm.devrState.memHead=&memory;comm.devrState.windowTable=&table;
  comm.devrState.winSorted=sorted;comm.devrState.winSortedCount=1;
  memory_identity=&memory;window_identity=&window;
  const auto result=ncclCommWindowDeregister(&comm,test=="null_window"?nullptr:&device);
  std::cout<<"{\"refs\":"<<refs<<",\"segments\":"<<segments<<",\"connections\":"<<connections
      <<",\"mode\":\""<<test<<"\",\"failure_segment\":"<<fs<<",\"failure_slot\":"<<fc
      <<",\"public_result\":"<<result<<",\"remaining_refs\":"<<memory.refCount
      <<",\"memory_freed\":"<<(memory_freed?"true":"false")
      <<",\"window_freed\":"<<(window_freed?"true":"false")
      <<",\"remaining_sorted_windows\":"<<comm.devrState.winSortedCount<<",\"mr_calls\":";numbers(calls);
  std::cout<<",\"retired\":";numbers(retired);std::cout<<",\"ignored_provider_results\":";numbers(failed_results);
  std::cout<<",\"events\":[";
  for(size_t i=0;i<events.size();++i){if(i)std::cout<<',';std::cout<<'"'<<events[i]<<'"';}
  std::cout<<"],\"real_provider_executed\":false}\n";
}
int main() {
  for(int refs=1;refs<=3;++refs)for(int segments=1;segments<=3;++segments)for(int connections=1;connections<=3;++connections) {
    for(const auto* test:{"success","null_window","initial_shadow_error","table_shadow_error",
                         "table_memset_error","window_shadow_error","local_deregister_error","stream_sync_error"})
      run(refs,segments,connections,test,-1,-1);
    for(int s=0;s<segments;++s)for(int c=0;c<connections;++c)
      for(const auto* test:{"provider_error","metadata_error"})run(refs,segments,connections,test,s,c);
  }
}
