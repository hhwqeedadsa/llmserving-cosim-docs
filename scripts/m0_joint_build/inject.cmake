# Include through CMAKE_PROJECT_NS3_INCLUDE, after NS3's project() call.
cmake_minimum_required(VERSION 3.24)

get_filename_component(COSIM_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(ASTRA_ROOT "${COSIM_ROOT}/third_party/LLMServingSim/astra-sim")
set(CHAKRA_ROOT "${ASTRA_ROOT}/extern/graph_frontend/chakra")
foreach(generated et_def.pb.cc et_def.pb.h)
  if(NOT EXISTS "${CHAKRA_ROOT}/schema/protobuf/${generated}")
    message(FATAL_ERROR "Missing M0.1 generated protobuf file: ${generated}")
  endif()
endforeach()
# add_subdirectory is not allowed during deferred execution.
add_subdirectory("${ASTRA_ROOT}" "${CMAKE_BINARY_DIR}/cosim-m0/astra")

function(cosim_m0_add_probe)
  include("${CMAKE_CURRENT_FUNCTION_LIST_DIR}/CMakeLists.txt")
endfunction()

# NS3's modules and build_exec must exist before adding the probe.
cmake_language(DEFER CALL cosim_m0_add_probe)
