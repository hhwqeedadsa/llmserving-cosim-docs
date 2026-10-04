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
  EXECNAME cosim-logging-test
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/logging_flush_test.cc"
  LIBRARIES_TO_LINK cosim-astra-logging AstraSim spdlog::spdlog
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(cosim-logging-test PROPERTIES
  CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(cosim-logging-test PRIVATE
  "${CMAKE_CURRENT_LIST_DIR}" "${ASTRA_ROOT}/extern/helper")
build_exec(
  EXECNAME cosim-compute
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/cosim_compute.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
  LIBRARIES_TO_LINK
    cosim-astra-logging "$<LINK_LIBRARY:WHOLE_ARCHIVE,AstraSim>" ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
build_exec(
  EXECNAME cosim-time-test
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/time_adapter_test.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
  LIBRARIES_TO_LINK AstraSim ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
build_exec(
  EXECNAME cosim-write
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/cosim_write.cc"
    "${CMAKE_CURRENT_LIST_DIR}/port_observation.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
  LIBRARIES_TO_LINK
    cosim-astra-logging "$<LINK_LIBRARY:WHOLE_ARCHIVE,AstraSim>" ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
build_exec(
  EXECNAME cosim-write-test
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/write_adapter_test.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
  LIBRARIES_TO_LINK AstraSim ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
build_exec(
  EXECNAME cosim-observation-test
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/port_observation_test.cc"
    "${CMAKE_CURRENT_LIST_DIR}/port_observation.cc"
  LIBRARIES_TO_LINK AstraSim ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
foreach(target cosim-compute cosim-time-test cosim-write cosim-write-test cosim-observation-test)
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
target_link_options(cosim-write PRIVATE
  "LINKER:-Map,${CMAKE_BINARY_DIR}/cosim-m1/write-link.map")
file(GENERATE OUTPUT "${CMAKE_BINARY_DIR}/cosim-m1/write-target.txt" CONTENT
"write=$<TARGET_FILE:cosim-write>
write_test=$<TARGET_FILE:cosim-write-test>
")

build_exec(
  EXECNAME cosim-layer
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/cosim_layer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/graph_sequence.cc"
    "${CMAKE_CURRENT_LIST_DIR}/layer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/cann_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/multilayer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/full_model_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/kv_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/port_observation.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
  LIBRARIES_TO_LINK
    cosim-astra-logging "$<LINK_LIBRARY:WHOLE_ARCHIVE,AstraSim>" ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
set_target_properties(cosim-layer PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(cosim-layer PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/cosim-m2")
target_link_options(cosim-layer PRIVATE "LINKER:-Map,${CMAKE_BINARY_DIR}/cosim-m2/layer-link.map")
file(GENERATE OUTPUT "${CMAKE_BINARY_DIR}/cosim-m2/target-facts.txt" CONTENT
"layer=$<TARGET_FILE:cosim-layer>")

# Deliberate completion events live only in this test executable. The real layer
# driver continues to use UbWriteTransfer and has no test-backend switch.
build_exec(
  EXECNAME cosim-layer-delay-test
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/../tests/integration/layer_delay_driver.cc"
    "${CMAKE_CURRENT_LIST_DIR}/layer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/cann_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/multilayer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/full_model_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/kv_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
  LIBRARIES_TO_LINK
    cosim-astra-logging "$<LINK_LIBRARY:WHOLE_ARCHIVE,AstraSim>" ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
set_target_properties(cosim-layer-delay-test PROPERTIES
  CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(cosim-layer-delay-test PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
target_link_options(cosim-layer-delay-test PRIVATE
  "LINKER:-Map,${CMAKE_BINARY_DIR}/cosim-m2/delay-test-link.map")

build_exec(
  EXECNAME cosim-send-slots-test
  SOURCE_FILES "${ASTRA_ROOT}/tests/cosim/send_slots_test.cc"
  LIBRARIES_TO_LINK AstraSim
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim"
  IGNORE_PCH)
set_target_properties(cosim-send-slots-test PROPERTIES
  CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)

foreach(target cosim-sequence cosim-lifecycle-test)
  build_exec(
    EXECNAME ${target}
    SOURCE_FILES
      "${CMAKE_CURRENT_LIST_DIR}/cosim_layer.cc"
      "${CMAKE_CURRENT_LIST_DIR}/graph_sequence.cc"
      "${CMAKE_CURRENT_LIST_DIR}/layer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/cann_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/multilayer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/full_model_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/kv_plan.cc"
      "${CMAKE_CURRENT_LIST_DIR}/port_observation.cc"
      "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
      "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
      "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
    LIBRARIES_TO_LINK cosim-astra-logging "$<LINK_LIBRARY:WHOLE_ARCHIVE,AstraSim>" ${libunified-bus} ${libcore}
    EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
  set_target_properties(${target} PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
  target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
  target_compile_definitions(${target} PRIVATE COSIM_SEQUENCE)
  target_link_options(${target} PRIVATE "LINKER:-Map,${CMAKE_BINARY_DIR}/cosim-m2/${target}-link.map")
endforeach()
target_compile_definitions(cosim-lifecycle-test PRIVATE COSIM_LIFECYCLE_TEST)

execute_process(COMMAND git rev-parse HEAD WORKING_DIRECTORY "${COSIM_ROOT}/third_party/ns-3-ub"
  OUTPUT_VARIABLE COSIM_UB_REVISION OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
foreach(target cosim-service cosim-service-deadlock-test)
build_exec(
  EXECNAME ${target}
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/cosim_service.cc"
    "${CMAKE_CURRENT_LIST_DIR}/backend_config.cc"
    "${CMAKE_CURRENT_LIST_DIR}/fabric_config.cc"
    "${CMAKE_CURRENT_LIST_DIR}/graph_sequence.cc"
    "${CMAKE_CURRENT_LIST_DIR}/layer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/cann_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/multilayer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/full_model_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/kv_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/port_observation.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
  LIBRARIES_TO_LINK cosim-astra-logging "$<LINK_LIBRARY:WHOLE_ARCHIVE,AstraSim>" ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(${target} PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
target_compile_definitions(${target} PRIVATE COSIM_UB_REVISION="${COSIM_UB_REVISION}")
endforeach()
target_compile_definitions(cosim-service-deadlock-test PRIVATE COSIM_SERVICE_DEADLOCK_TEST)
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/cosim-m3")
target_link_options(cosim-service PRIVATE "LINKER:-Map,${CMAKE_BINARY_DIR}/cosim-m3/service-link.map")

build_exec(
  EXECNAME cosim-local-test
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/local_delivery_test.cc"
    "${CMAKE_CURRENT_LIST_DIR}/local_delivery.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_network_adapter.cc"
    "${CMAKE_CURRENT_LIST_DIR}/ub_write_transfer.cc"
    "${CMAKE_CURRENT_LIST_DIR}/write_completion.cc"
  LIBRARIES_TO_LINK AstraSim ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(cosim-local-test PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(cosim-local-test PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
