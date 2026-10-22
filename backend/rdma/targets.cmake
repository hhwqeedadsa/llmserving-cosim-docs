get_filename_component(MOE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(MOE_ASTRA "${MOE_ROOT}/third_party/LLMServingSim/astra-sim")
if(TARGET ns3::libunified-bus OR NOT TARGET ns3::libpoint-to-point OR NOT TARGET AstraSim)
  message(FATAL_ERROR "RDMA targets require the locked ASTRA native ns-3, without UB")
endif()
include("${CMAKE_CURRENT_LIST_DIR}/serial_native.cmake")
add_library(moe-rdma-fabric STATIC "${CMAKE_CURRENT_LIST_DIR}/fabric.cc" "${CMAKE_CURRENT_LIST_DIR}/recovery.cc")
target_include_directories(moe-rdma-fabric PUBLIC "${CMAKE_CURRENT_LIST_DIR}" "${MOE_ASTRA}/extern/helper")
target_link_libraries(moe-rdma-fabric PUBLIC ${libcore} ${libnetwork} ${libinternet} ${libpoint-to-point} ${libapplications})
set_target_properties(moe-rdma-fabric PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON)
build_exec(
  EXECNAME moe-rdma-probe
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/probe.cc"
  LIBRARIES_TO_LINK moe-rdma-fabric ${libcore} ${libnetwork} ${libinternet} ${libpoint-to-point} ${libapplications}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/moe-rdma"
  IGNORE_PCH)
target_include_directories(moe-rdma-probe PRIVATE "${CMAKE_CURRENT_LIST_DIR}" "${MOE_ASTRA}/extern/helper")
set_target_properties(moe-rdma-probe PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON)
build_exec(
  EXECNAME moe-rdma-stream-probe
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/stream_probe.cc" "${CMAKE_CURRENT_LIST_DIR}/persistent_streams.cc"
  LIBRARIES_TO_LINK moe-rdma-fabric ${libcore} ${libnetwork} ${libinternet} ${libpoint-to-point} ${libapplications}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/moe-rdma"
  IGNORE_PCH)
target_include_directories(moe-rdma-stream-probe PRIVATE "${CMAKE_CURRENT_LIST_DIR}" "${MOE_ASTRA}/extern/helper")
set_target_properties(moe-rdma-stream-probe PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON)
add_library(moe-rdma-logging STATIC "${CMAKE_CURRENT_LIST_DIR}/logging.cc")
target_link_libraries(moe-rdma-logging PUBLIC AstraSim)
target_include_directories(moe-rdma-logging PRIVATE "${MOE_ASTRA}/extern/helper")
set_target_properties(moe-rdma-logging PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON)
build_exec(
  EXECNAME moe-rdma-service
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/service.cc" "${CMAKE_CURRENT_LIST_DIR}/astra_adapter.cc"
  LIBRARIES_TO_LINK moe-rdma-fabric moe-rdma-logging AstraSim ${libcore} ${libnetwork} ${libinternet} ${libpoint-to-point} ${libapplications}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/moe-rdma"
  IGNORE_PCH)
target_include_directories(moe-rdma-service PRIVATE "${CMAKE_CURRENT_LIST_DIR}" "${MOE_ASTRA}/extern/helper")
set_target_properties(moe-rdma-service PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON)
file(GENERATE OUTPUT "${CMAKE_BINARY_DIR}/moe-rdma-targets.json" CONTENT
  "{\"probe\":\"$<TARGET_FILE:moe-rdma-probe>\",\"stream_probe\":\"$<TARGET_FILE:moe-rdma-stream-probe>\",\"service\":\"$<TARGET_FILE:moe-rdma-service>\",\"ns3_core\":\"$<TARGET_FILE:ns3::libcore>\"}\n")
