cmake_minimum_required(VERSION 3.13)
set(PICO_BOARD pico_w CACHE STRING "Physical board")
set(PICO_SDK_PATH "${CMAKE_CURRENT_LIST_DIR}/../pico_ssm_reproduction/pico-ssm/lib/pico-sdk")
include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")
project(pico_continuous_capture C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
pico_sdk_init()
add_executable(pico_continuous_capture main.c)
pico_generate_pio_header(pico_continuous_capture "${CMAKE_CURRENT_LIST_DIR}/finite.pio")
target_link_libraries(pico_continuous_capture pico_stdlib hardware_pio hardware_dma hardware_clocks)
target_compile_options(pico_continuous_capture PRIVATE -Wall -Wextra -Werror)
target_compile_definitions(pico_continuous_capture PRIVATE
    PICO_STDIO_USB_CONNECT_WAIT_TIMEOUT_MS=0 PICO_STDIO_USB_DEFAULT_CRLF=0
    PICO_STDIO_USB_RESET_BOOTSEL_INTERFACE_DISABLE_MASK=1
    USBD_PRODUCT="Pico_Async_Stream"
    PICO_STDIO_USB_STDOUT_TIMEOUT_US=100000)
pico_enable_stdio_usb(pico_continuous_capture 1)
pico_enable_stdio_uart(pico_continuous_capture 0)
pico_set_program_name(pico_continuous_capture "Pico external DUT bounded capture")
pico_set_program_version(pico_continuous_capture "continuous-input-v1")
pico_add_extra_outputs(pico_continuous_capture)
