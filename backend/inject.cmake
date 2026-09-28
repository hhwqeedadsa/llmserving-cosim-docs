# Keep the verified M0 library boundary and its runnable probe.
include("${CMAKE_CURRENT_LIST_DIR}/../scripts/m0_joint_build/inject.cmake")
function(cosim_m1_add_compute)
  include("${CMAKE_CURRENT_FUNCTION_LIST_DIR}/CMakeLists.txt")
endfunction()
cmake_language(DEFER CALL cosim_m1_add_compute)
