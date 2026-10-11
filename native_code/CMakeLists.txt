# 仅供 IDE 索引；实际构建由 setup.py 中的 Extension 完成。
cmake_minimum_required(VERSION 3.18)
project(adb_scr_media_index LANGUAGES NONE)

if(APPLE)
    add_subdirectory(macOS)
elseif(WIN32)
    add_subdirectory(Windows)
else()
    message(FATAL_ERROR "IDE indexing supports macOS and Windows only")
endif()
