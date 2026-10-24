// JSONL emitted without external JSON libraries; all strings are controlled.
static void Strings(){std::cout<<"[";for(unsigned i=0;i<trace.size();++i)std::cout<<(i?",":"")<<'"'<<trace[i]<<'"';std::cout<<"]";}
int main() {
  for(bool child:{false,true})for(int proxy:{0,1,2})for(int old:{0,1})
  for(const auto& fail:std::vector<std::string>{"none","set_device","host_sync","device_sync","poll_events","poll_callbacks","proxy_stop"}) {
    trace.clear();fail_at=fail;LocalComm state(child);
    if(proxy){state.c.proxyState=&state.proxy;if(proxy==2){state.proxy.thread.available=true;state.proxy.threadUDS.available=true;}}
    state.c.proxyRefCountOld=old;ncclCommRevokeAsyncJob job{{},&state.c};
    const auto result=commRevokeAsync(&job.base);
    std::cout<<"{\"kind\":\"revoke_async\",\"child\":"<<child<<",\"proxy\":"<<proxy<<",\"old_refs\":"<<old
             <<",\"fail\":\""<<fail<<"\",\"return\":"<<result<<",\"async\":"<<state.c.asyncResult
             <<",\"host_flag\":"<<state.host<<",\"device_flag\":"<<state.device<<",\"child_host\":"<<state.child_host
             <<",\"child_device\":"<<state.child_device<<",\"trace\":";Strings();std::cout<<"}\n";
  }
  for(bool child:{false,true})for(bool abort:{false,true})
  for(const auto& fail:std::vector<std::string>{"none","group_start","device_sync","child_init","group_end"}) {
    trace.clear();fail_at=fail;LocalComm state(child);state.host=state.device=state.child_host=state.child_device=0;ncclComm_t next=nullptr;int excluded=1;
    const auto result=ncclCommShrink(&state.c,&excluded,1,&next,nullptr,abort?NCCL_SHRINK_ABORT:0);
    std::cout<<"{\"kind\":\"shrink\",\"child\":"<<child<<",\"abort\":"<<abort<<",\"fail\":\""<<fail
             <<"\",\"return\":"<<result<<",\"host_flag\":"<<state.host<<",\"device_flag\":"<<state.device
             <<",\"child_host\":"<<state.child_host<<",\"child_device\":"<<state.child_device
             <<",\"child_handle_returned\":"<<(next!=nullptr)<<",\"trace\":";Strings();std::cout<<"}\n";
  }
  for(bool revoked:{false,true})for(bool shrink:{false,true})for(bool abort:{false,true})
  for(bool split_share:{false,true})for(bool shrink_share:{false,true}) {
    trace.clear();LocalComm state(false);state.c.revokedFlag=revoked;
    state.c.config.splitShare=split_share;state.c.config.shrinkShare=shrink_share;
    std::cout<<"{\"kind\":\"sharing\",\"revoked\":"<<revoked<<",\"shrink\":"<<shrink<<",\"abort\":"<<abort
             <<",\"split_share\":"<<split_share<<",\"shrink_share\":"<<shrink_share
             <<",\"shared\":"<<Share(&state.c,shrink,abort?NCCL_SHRINK_ABORT:0)<<"}\n";
  }
  for(bool internal:{false,true})for(bool aborted:{false,true})for(int deadline:{-1,4,10002})
  for(bool empty:{false,true})for(int done_after:{0,2,-1}) {
    trace.clear();LocalComm state(false);setCommAbortFlags(&state.c,aborted?1:0);
    RecordingDevComm dev{};SelectAbort(&dev,&state.c,internal);
    polls=0;blocking_calls=0;cuda::fences=0;cycles=0;complete_after=done_after;
    doca_gpu_dev_verbs_qp qp;ncclGinGdakiGPUContext gpu{&qp};ncclGinCtx ctx{&gpu,0};ncclGinRequest_t req{empty?0u:1u,0};
    int result=-1;std::string exit="returned";
    try {
      result=deadline<0?waitImplCore<false,recording_mode>(ctx,req,cuda::memory_order_acquire,dev.abortFlag,0):
                       waitImplCore<true,recording_mode>(ctx,req,cuda::memory_order_acquire,dev.abortFlag,deadline);
    }catch(const RecordingStop& e){exit=e.what();}
    std::cout<<"{\"kind\":\"gin_wait\",\"internal\":"<<internal<<",\"abort_set\":"<<aborted<<",\"deadline\":"<<deadline
             <<",\"empty\":"<<empty<<",\"complete_after\":"<<done_after<<",\"abort_pointer_present\":"<<(dev.abortFlag!=nullptr)
             <<",\"return\":"<<result<<",\"exit\":\""<<exit<<"\",\"polls\":"<<polls<<",\"blocking_calls\":"<<blocking_calls
             <<",\"fences\":"<<cuda::fences<<"}\n";
  }
}
