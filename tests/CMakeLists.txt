# These are the plugin's real protocol units, compiled without RED4ext/Winsock.
set(v2_test_dir "${CMAKE_SOURCE_DIR}/plugin/tests")
foreach(suite IN ITEMS Tests ReliabilityTests ClockTests InterpTests Fuzz Golden)
    add_executable(coop_check_v2_${suite}
        "${v2_test_dir}/V2${suite}.cpp"
        "${v2_test_dir}/V2TestSupport.cpp"
    )
    target_link_libraries(coop_check_v2_${suite} PRIVATE coop_portable_v2)
endforeach()
if(COOP_ENABLE_SANITIZERS)
    target_compile_definitions(coop_check_v2_InterpTests PRIVATE COOP_INSTRUMENTED_TESTS=1)
endif()
add_test(NAME v2_codec COMMAND coop_check_v2_Tests)
add_test(NAME v2_reliability COMMAND coop_check_v2_ReliabilityTests)
add_test(NAME v2_clock COMMAND coop_check_v2_ClockTests)
add_test(NAME v2_interpolation COMMAND coop_check_v2_InterpTests)
add_test(NAME v2_fuzz COMMAND coop_check_v2_Fuzz 20000)

find_package(Python3 3.12 COMPONENTS Interpreter REQUIRED)
set(relay_dir "${CMAKE_SOURCE_DIR}/relay")
set(plugin_tools "${CMAKE_SOURCE_DIR}/plugin/tools")
add_test(NAME v2_python_golden COMMAND "${Python3_EXECUTABLE}" "${plugin_tools}/v2_golden.py"
    --relay "${relay_dir}" run --exe $<TARGET_FILE:coop_check_v2_Golden>)
add_test(NAME v2_python_reliability COMMAND "${Python3_EXECUTABLE}" "${plugin_tools}/v2_link_trace.py"
    --relay "${relay_dir}" run --exe $<TARGET_FILE:coop_check_v2_ReliabilityTests>)
add_test(NAME v2_python_interpolation COMMAND "${Python3_EXECUTABLE}" "${plugin_tools}/v2_interp_trace.py"
    --relay "${relay_dir}" run --exe $<TARGET_FILE:coop_check_v2_InterpTests>)
add_test(NAME relay_python COMMAND "${Python3_EXECUTABLE}" -m unittest discover -s tests -v)
set_tests_properties(relay_python PROPERTIES WORKING_DIRECTORY "${relay_dir}")
set_tests_properties(v2_codec v2_reliability v2_clock v2_interpolation v2_fuzz
    v2_python_golden v2_python_reliability v2_python_interpolation relay_python
    PROPERTIES TIMEOUT 180)

if(COOP_BUILD_NATIVE_PLUGIN)
    add_test(NAME native_core COMMAND coopnet_tests)
    add_test(NAME native_plugin_load COMMAND coopnet_plugin_probe $<TARGET_FILE:CP2077CoopNet>)
    set_tests_properties(native_core native_plugin_load PROPERTIES TIMEOUT 60)
    foreach(profile IN ITEMS clean bench lossy restart)
        add_test(NAME native_transport_${profile} COMMAND "${Python3_EXECUTABLE}"
            "${plugin_tools}/run_loopback.py" --relay "${relay_dir}"
            --exe $<TARGET_FILE:coopnet_loopback> --profile "${profile}"
            --out "${CMAKE_BINARY_DIR}/loopback/${profile}")
        set_tests_properties(native_transport_${profile} PROPERTIES TIMEOUT 240 RUN_SERIAL TRUE)
    endforeach()
endif()
