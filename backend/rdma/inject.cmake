# Configure only the locked ASTRA RDMA ns-3. Never include ns-3-UB here.
cmake_minimum_required(VERSION 3.24)
if(MOE_PROTOBUF_CONFIG)
  # Scoped to this configure process; required for static Protobuf's Abseil deps.
  set(ENV{PROTOBUF_FROM_SOURCE} "True")
endif()
function(moe_rdma_targets)
  include("${CMAKE_CURRENT_FUNCTION_LIST_DIR}/targets.cmake")
endfunction()
cmake_language(DEFER CALL moe_rdma_targets)
