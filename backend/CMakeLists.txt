# Included after ns-3-UB has defined its modules and build_exec.
# The locked logging helper has unqualified format() calls incompatible with
# C++20 std::format. Keep this bridge on ASTRA's C++17 side of the boundary.
add_library(cosim-astra-logging STATIC "${CMAKE_CURRENT_LIST_DIR}/astra_logging.cc")
target_include_directories(cosim-astra-logging PRIVATE
  "${ASTRA_ROOT}" "${ASTRA_ROOT}/extern/helper")
target_link_libraries(cosim-astra-logging PRIVATE spdlog::spdlog)
set_target_properties(cosim-astra-logging PROPERTIES
  CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
build_exec(
  EXECNAME cosim-compute
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/cosim_compute.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
  LIBRARIES_TO_LINK
    cosim-astra-logging "$<LINK_LIBRARY:WHOLE_ARCHIVE,AstraSim>" ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
build_exec(
  EXECNAME cosim-time-test
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/time_adapter_test.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
  LIBRARIES_TO_LINK AstraSim ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
foreach(target cosim-compute cosim-time-test)
  set_target_properties(${target} PROPERTIES
    CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
  target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
endforeach()
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/cosim-m1")
target_link_options(cosim-compute PRIVATE
  "LINKER:-Map,${CMAKE_BINARY_DIR}/cosim-m1/link.map")
file(GENERATE OUTPUT "${CMAKE_BINARY_DIR}/cosim-m1/target-facts.txt" CONTENT
"compute=$<TARGET_FILE:cosim-compute>
time_test=$<TARGET_FILE:cosim-time-test>
astra_library=$<TARGET_FILE:AstraSim>
")
