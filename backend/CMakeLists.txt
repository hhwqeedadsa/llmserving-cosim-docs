# Included after ns-3-UB has defined its modules and build_exec.
build_exec(
  EXECNAME cosim-queue-windows-test
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/queue_windows_test.cc"
  LIBRARIES_TO_LINK ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(cosim-queue-windows-test PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(cosim-queue-windows-test PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
build_exec(
  EXECNAME cosim-native-runtime-test
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../tests/integration/native_runtime_test.cc"
  LIBRARIES_TO_LINK ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(cosim-native-runtime-test PROPERTIES
  CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
build_exec(
  EXECNAME cosim-native-routing-test
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../tests/integration/native_routing_test.cc"
  LIBRARIES_TO_LINK ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(cosim-native-routing-test PROPERTIES
  CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(cosim-native-routing-test PRIVATE "${ASTRA_ROOT}/extern/helper")
find_package(ZLIB REQUIRED)
add_library(cosim-records STATIC "${CMAKE_CURRENT_LIST_DIR}/record_store.cc")
target_link_libraries(cosim-records PUBLIC ZLIB::ZLIB)
set_target_properties(cosim-records PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
# The locked logging helper has unqualified format() calls incompatible with
# C++20 std::format. Keep this bridge on ASTRA's C++17 side of the boundary.
add_library(cosim-astra-logging STATIC "${CMAKE_CURRENT_LIST_DIR}/astra_logging.cc")
target_include_directories(cosim-astra-logging PRIVATE
  "${ASTRA_ROOT}" "${ASTRA_ROOT}/extern/helper")
target_link_libraries(cosim-astra-logging PUBLIC cosim-records PRIVATE spdlog::spdlog)
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
  EXECNAME cosim-logging-archive-test
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/logging_archive_test.cc"
  LIBRARIES_TO_LINK cosim-astra-logging AstraSim spdlog::spdlog
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(cosim-logging-archive-test PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(cosim-logging-archive-test PRIVATE
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
build_exec(
  EXECNAME cosim-provenance-test
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/shared_provenance_test.cc"
    "${CMAKE_CURRENT_LIST_DIR}/port_observation.cc"
  LIBRARIES_TO_LINK AstraSim ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
foreach(target cosim-compute cosim-time-test cosim-write cosim-write-test cosim-observation-test cosim-provenance-test)
  set_target_properties(${target} PROPERTIES
    CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
  target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
endforeach()
target_link_libraries(cosim-observation-test cosim-records)
target_link_libraries(cosim-provenance-test cosim-records)
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
    "${CMAKE_CURRENT_LIST_DIR}/native_plan.cc"
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
    "${CMAKE_CURRENT_LIST_DIR}/native_plan.cc"
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
    "${CMAKE_CURRENT_LIST_DIR}/native_plan.cc"
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
foreach(target cosim-service cosim-service-deadlock-test cosim-service-resource-probe)
build_exec(
  EXECNAME ${target}
  SOURCE_FILES
    "${CMAKE_CURRENT_LIST_DIR}/cosim_service.cc"
    "${CMAKE_CURRENT_LIST_DIR}/operation_stages.cc"
    "${CMAKE_CURRENT_LIST_DIR}/backend_config.cc"
    "${CMAKE_CURRENT_LIST_DIR}/fabric_config.cc"
    "${CMAKE_CURRENT_LIST_DIR}/graph_sequence.cc"
    "${CMAKE_CURRENT_LIST_DIR}/layer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/cann_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/native_plan.cc"
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
target_compile_definitions(cosim-service-resource-probe PRIVATE COSIM_SERVICE_RESOURCE_PROBE)
build_exec(
  EXECNAME cosim-transport-retirement-test
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../tests/unit/transport_retirement_test.cc"
  LIBRARIES_TO_LINK ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(cosim-transport-retirement-test PROPERTIES
  CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
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

# Measurement only: invoke the real loader without constructing a UB network.
build_exec(
  EXECNAME cosim-plan-load-probe
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/../scripts/full_round_load_probe.cc"
    "${CMAKE_CURRENT_LIST_DIR}/layer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/cann_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/native_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/multilayer_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/full_model_plan.cc"
    "${CMAKE_CURRENT_LIST_DIR}/kv_plan.cc"
  LIBRARIES_TO_LINK AstraSim ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/cosim" IGNORE_PCH)
set_target_properties(cosim-plan-load-probe PROPERTIES
  CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_include_directories(cosim-plan-load-probe PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
