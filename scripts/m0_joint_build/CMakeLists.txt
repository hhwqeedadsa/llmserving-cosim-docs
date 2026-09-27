cmake_minimum_required(VERSION 3.24)

if(NOT TARGET ns3::unified-bus OR NOT COMMAND build_exec)
  message(FATAL_ERROR "Configure ns-3-UB with this directory's inject.cmake")
endif()
# The locked root target includes the core, Chakra and analytical MEMORY.
# Do not include any build/* or network_frontend/* project.
get_target_property(astra_sources AstraSim SOURCES)
foreach(source IN LISTS astra_sources)
  if(source MATCHES "network_frontend/|extern/network_backend/")
    message(FATAL_ERROR "Network backend entered the core library: ${source}")
  endif()
endforeach()
list(JOIN astra_sources "\n" astra_source_lines)
file(WRITE "${CMAKE_BINARY_DIR}/cosim-m0/astra-sources.txt"
     "${astra_source_lines}\n")

build_exec(
  EXECNAME m0-joint-link-probe
  SOURCE_FILES "${CMAKE_CURRENT_LIST_DIR}/link_probe.cc"
  LIBRARIES_TO_LINK
    "$<LINK_LIBRARY:WHOLE_ARCHIVE,AstraSim>" ${libunified-bus} ${libcore}
  EXECUTABLE_DIRECTORY_PATH "${CMAKE_OUTPUT_DIRECTORY}/m0"
  IGNORE_PCH
)
set_target_properties(m0-joint-link-probe PROPERTIES
  CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_link_options(m0-joint-link-probe PRIVATE
  "LINKER:-Map,${CMAKE_BINARY_DIR}/cosim-m0/link.map")
file(GENERATE OUTPUT "${CMAKE_BINARY_DIR}/cosim-m0/target-facts.txt" CONTENT
"astra_standard=$<TARGET_PROPERTY:AstraSim,CXX_STANDARD>
probe_standard=$<TARGET_PROPERTY:m0-joint-link-probe,CXX_STANDARD>
astra_library=$<TARGET_FILE:AstraSim>
probe=$<TARGET_FILE:m0-joint-link-probe>
core_library=$<TARGET_FILE:ns3::core>
ub_library=$<TARGET_FILE:ns3::unified-bus>
astra_links=$<TARGET_PROPERTY:AstraSim,LINK_LIBRARIES>
probe_links=$<TARGET_PROPERTY:m0-joint-link-probe,LINK_LIBRARIES>
")
