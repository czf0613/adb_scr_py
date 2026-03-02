# 这个文件并不直接指导编译，只是为了方便IDE能够正确找到头文件和源文件。实际的编译过程由setup.py中的Extension模块完成。
cmake_minimum_required(VERSION 3.16)
project(adb_scr_media)

set(CMAKE_C_STANDARD 11)

# 包含目录
include_directories(
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/turbojpeg/include
    "/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/include/python3.14"
)

# 源文件
set(SOURCE_FILES
    src/adb_scr_media.c
    src/jpg_encoder.c
    src/vtb_decoder.c
    src/vtb_helper.c
)

# 仅用于IDE索引，不用于实际编译
add_library(${PROJECT_NAME} STATIC ${SOURCE_FILES})
