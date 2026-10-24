#pragma once
#include "fabric.hh"

namespace moe_rdma {
// Pure admission bounds for the explicitly copied, source-window PUT model.
// These are named data-content capacities, not a total host/device memory bound.
inline uint64_t BufferInteger(const Json& value,uint64_t low,uint64_t high) {
  Require(value.is_number_integer() && (value.is_number_unsigned() || value.get<int64_t>()>=0),
          "invalid buffer capacity integer");
  const auto n=value.get<uint64_t>();Require(n>=low && n<=high,"buffer capacity out of bounds");return n;
}
inline void ValidateBoundedBuffers(const Json& input) {
  if(!input.contains("buffer_model"))return;
  const auto& b=input.at("buffer_model");
  Require(b.is_object() && b.size()==5 && b.at("mode")=="bounded_copied_put/1",
          "invalid bounded buffer model fields/mode");
  const auto transport=BufferInteger(b.at("transport_bytes_per_source"),33,2*1024*1024);
  const auto registered=BufferInteger(b.at("registered_bytes_per_owner"),1,1024*1024);
  const auto cache=BufferInteger(b.at("target_cache_bytes_per_owner"),33,2*1024*1024);
  const auto local=BufferInteger(b.at("local_copy_bytes_per_owner"),1,32768);
  Require(input.at("schema_version")=="moe-rdma-transactions/4" && !input.contains("submission_model"),
          "bounded copied buffers require transactions/4 without explicit submission");
  std::map<uint64_t,uint64_t> windows,cached,destinations;
  for(const auto& c:input.at("connections"))destinations.emplace(c.at("id"),c.at("dst"));
  for(const auto& w:input.at("windows")) {
    Require(w.at("hex").is_string(),"bounded registration requires hex bytes");
    auto& bytes=windows[w.at("owner")];bytes+=w.at("hex").get<std::string>().size()/2;
    Require(bytes<=registered,"registered window capacity exceeded");
  }
  for(const auto& op:input.at("operations")) {
    if(op.at("kind")=="local") {
      if(op.contains("copy"))Require(BufferInteger(op.at("copy").at("source").at("bytes"),1,32768)<=local,
                                    "local copy snapshot capacity exceeded");
      continue;
    }
    Require(op.at("kind")=="remote" && op.at("verb")=="put" && op.contains("source") && !op.contains("hex"),
            "bounded copied buffers support source-window put only");
    const auto size=BufferInteger(op.at("source").at("bytes"),1,32768)+32;
    Require(size<=transport,"one frame exceeds source transport capacity");
    auto& bytes=cached[destinations.at(op.at("connection"))];bytes+=size;
    Require(bytes<=cache,"retained target request cache capacity exceeded");
  }
}
}
