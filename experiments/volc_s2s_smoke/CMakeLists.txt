cmake_minimum_required(VERSION 3.16)

project(volc_s2s_ws_smoke LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" VOLC_SYSTEM_PROCESSOR_LOWER)
if(NOT VOLC_SYSTEM_PROCESSOR_LOWER MATCHES "^(aarch64|arm64)$")
  message(FATAL_ERROR
    "This Spike is intentionally ARM64-only. Detected CMAKE_SYSTEM_PROCESSOR=${CMAKE_SYSTEM_PROCESSOR}")
endif()

set(VOLC_SDK_COMMIT "2c94f96f3aad4094e0e818cbb031149fd4384ead")
set(MBEDTLS_COMMIT "22098d41c6620ce07cf8a0134d37302355e1e5ef")

get_filename_component(REPOSITORY_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(VOLC_SDK_ROOT
  "${REPOSITORY_ROOT}/third_party/ConversationalAI-Embedded-Kit-2.0/volc_conv_ai")

if(NOT EXISTS "${VOLC_SDK_ROOT}/inc/volc_conv_ai.h")
  message(FATAL_ERROR
    "Official SDK submodule is missing. Run: git submodule update --init --recursive")
endif()

include(FetchContent)

# The SDK's bundled zlib archive is a macOS arm64 archive, and its Linux RTC
# library is x86-64. Neither is used here. Build only mbedTLS from pinned source
# and use the native Ubuntu ARM64 zlib package.
set(ENABLE_PROGRAMS OFF CACHE BOOL "" FORCE)
set(ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(USE_SHARED_MBEDTLS_LIBRARY OFF CACHE BOOL "" FORCE)
set(USE_STATIC_MBEDTLS_LIBRARY ON CACHE BOOL "" FORCE)
set(MBEDTLS_FATAL_WARNINGS OFF CACHE BOOL "" FORCE)
FetchContent_Declare(
  mbedtls
  GIT_REPOSITORY https://github.com/Mbed-TLS/mbedtls.git
  GIT_TAG ${MBEDTLS_COMMIT}
  GIT_SHALLOW FALSE
)
FetchContent_MakeAvailable(mbedtls)

find_package(Threads REQUIRED)
find_package(ZLIB REQUIRED)

set(VOLC_COMMON_SOURCES
  "${VOLC_SDK_ROOT}/src/volc_conv_ai.c"
  "${VOLC_SDK_ROOT}/src/base/volc_device_manager.c"
  "${VOLC_SDK_ROOT}/src/util/volc_auth.c"
  "${VOLC_SDK_ROOT}/src/util/volc_base64.c"
  "${VOLC_SDK_ROOT}/src/util/volc_http.c"
  "${VOLC_SDK_ROOT}/src/util/volc_json.c"
  "${VOLC_SDK_ROOT}/third_party/mbedtls_port/tls_certificate.c"
  "${VOLC_SDK_ROOT}/third_party/mbedtls_port/tls_client.c"
  "${VOLC_SDK_ROOT}/third_party/webclient/src/webclient.c"
  "${VOLC_SDK_ROOT}/src/util/cJSON/cJSON.c"
)

set(VOLC_WS_SOURCES
  "${VOLC_SDK_ROOT}/src/transports/low_load/src/volc_ws.c"
  "${VOLC_SDK_ROOT}/src/transports/low_load/third_party/websocket/websocket.c"
  "${VOLC_SDK_ROOT}/osal/src/linux/volc_osal.c"
)

add_library(volc_conv_ai_ws STATIC ${VOLC_COMMON_SOURCES} ${VOLC_WS_SOURCES})
add_library(volc::conv_ai_ws ALIAS volc_conv_ai_ws)

target_include_directories(volc_conv_ai_ws PUBLIC
  "${VOLC_SDK_ROOT}/inc"
  "${VOLC_SDK_ROOT}/src"
  "${VOLC_SDK_ROOT}/src/util/cJSON"
  "${VOLC_SDK_ROOT}/src/transports/low_load/inc"
  "${VOLC_SDK_ROOT}/src/transports/low_load/third_party/websocket"
  "${VOLC_SDK_ROOT}/third_party/mbedtls_port"
  "${VOLC_SDK_ROOT}/third_party/webclient/inc"
  "${VOLC_SDK_ROOT}/osal/inc"
)

target_compile_definitions(volc_conv_ai_ws PRIVATE
  _GNU_SOURCE
  ENABLE_WS_MODE
  ENABLE_MBEDTLS
  PLATFORM_LINUX
)

target_link_libraries(volc_conv_ai_ws PUBLIC
  Threads::Threads
  mbedtls
  mbedx509
  mbedcrypto
  ZLIB::ZLIB
)

add_executable(volc_ws_smoke
  src/main.c
  src/wav_writer.c
)

add_executable(volc_ws_bridge
  src/bridge.c
)

target_compile_definitions(volc_ws_smoke PRIVATE
  VOLC_SDK_COMMIT="${VOLC_SDK_COMMIT}"
  VOLC_MBEDTLS_COMMIT="${MBEDTLS_COMMIT}"
)

target_compile_definitions(volc_ws_bridge PRIVATE
  VOLC_SDK_COMMIT="${VOLC_SDK_COMMIT}"
  VOLC_MBEDTLS_COMMIT="${MBEDTLS_COMMIT}"
)

target_compile_options(volc_ws_smoke PRIVATE
  -Wall
  -Wextra
  -Wpedantic
)

target_compile_options(volc_ws_bridge PRIVATE
  -Wall
  -Wextra
  -Wpedantic
)

target_link_libraries(volc_ws_smoke PRIVATE volc::conv_ai_ws)
target_link_libraries(volc_ws_bridge PRIVATE volc::conv_ai_ws)

message(STATUS "Volc SDK commit: ${VOLC_SDK_COMMIT}")
message(STATUS "mbedTLS commit: ${MBEDTLS_COMMIT}")
message(STATUS "Transport: low-load WebSocket only")
message(STATUS "RTC transport: disabled/not linked")
