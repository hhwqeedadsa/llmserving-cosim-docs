#undef free
static void array(const std::vector<int>& values) {
  std::cout<<'[';bool first=true;for(int v:values){if(!first)std::cout<<',';std::cout<<v;first=false;}std::cout<<']';
}
static std::vector<int> selected(const bool* flags) {
  std::vector<int> out;for(int i=0;i<8;++i)if(flags[i])out.push_back(i);return out;
}
static void run(const std::vector<int>& counts,int devices,const std::string& test,int fail) {
  mode=test;failure_slot=fail;device_count=devices;
  for(int i=0;i<8;++i) {
    created[i]=retired[i]=freed[i]=false;handles[i]=nullptr;
    for(int k=0;k<2;++k){metadata[i][k]=nullptr;deleted[i][k]=false;}
  }
  reg_calls.clear();dereg_calls.clear();mr_calls.clear();meta_calls.clear();
  Shared shared{};ncclComm comm{3,&shared};Plugin plugin{MockReg,MockDereg};
  shared.ginState.numActiveBackends=counts.size();
  for(unsigned b=0;b<counts.size();++b) {
    auto& backend=shared.ginState.backends[b];backend.ginCommCount=counts[b];backend.ncclGin=&plugin;
    for(int c=0;c<counts[b];++c) {
      const int slot=int(b)*4+c;coll[slot]={3,slot};backend.ginComms[c]=&coll[slot];
      backend.ginProps[c].ptrSupport=(test=="multisegment_unsupported" && slot==fail)?0:NCCL_PTR_DMABUF;
    }
  }
  void* hosts[8]{};void* devs[8]{};
  const auto registered=ncclGinRegister(&comm,backing,sizeof(backing),hosts,devs,NCCL_WIN_STRICT_ORDERING,
      test=="multisegment_unsupported",17);
  ncclResult_t result=registered;
  if(registered==ncclSuccess && test!="register_error" && test!="null_handle" && test!="multisegment_unsupported")
    result=ncclGinDeregister(&comm,hosts);
  std::cout<<"{\"counts\":";array(counts);
  std::cout<<",\"devices_per_backend\":"<<devices<<",\"mode\":\""<<test<<"\",\"failure_slot\":"<<fail
      <<",\"register_result\":"<<registered<<",\"result\":"<<result<<",\"registration_calls\":";array(reg_calls);
  std::cout<<",\"deregistration_calls\":";array(dereg_calls);std::cout<<",\"mr_calls\":";array(mr_calls);
  std::cout<<",\"metadata_calls\":";array(meta_calls);std::cout<<",\"created\":";array(selected(created));
  std::cout<<",\"retired\":";array(selected(retired));std::cout<<",\"freed\":";array(selected(freed));
  std::cout<<",\"alias_device_mapping\":[";
  bool first=true;
  for(unsigned b=0;b<counts.size();++b)for(int c=0;c<counts[b];++c) {
    if(!first){std::cout<<',';}first=false;
    std::cout<<"["<<b*4+c<<','<<b<<','<<c%device_count<<']';
  }
  std::cout<<"],\"same_address_and_extent_checked\":true,\"actual_provider_executed\":false}\n";
  // Our substitutes reclaim surviving bookkeeping, never repeat original
  // deregistration with freed handles after a partial failure.
  for(int i=0;i<8;++i) {
    for(int k=0;k<2;++k)if(metadata[i][k] && !deleted[i][k])delete metadata[i][k];
    if(handles[i] && !freed[i])std::free(handles[i]);
  }
}
static void layout(const std::vector<int>& counts) {
  for(int devices:{1,2}) {
    run(counts,devices,"success",-1);
    for(unsigned b=0;b<counts.size();++b)for(int c=0;c<counts[b];++c)
      for(const auto* mode_name:{"register_error","null_handle","multisegment_unsupported", "mr_error",
          "metadata_handle_error","metadata_keys_error"})run(counts,devices,mode_name,int(b)*4+c);
  }
}
int main() {
  for(int a=1;a<=4;++a){layout({a});for(int b=1;b<=4;++b)layout({a,b});}
}
