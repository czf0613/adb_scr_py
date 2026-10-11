# 仅供 IDE 索引；实际构建由 setup.py 中的 Extension 完成。
cmake_minimum_required(VERSION 3.18)
project(adb_scr_media LANGUAGES CXX)

find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter Development.Module)

# 不加入默认构建；源文件清单与 setup.py 的 Windows 扩展保持一致。
add_library(${PROJECT_NAME} STATIC EXCLUDE_FROM_ALL
    src/common.cpp
    src/decoder.cpp
    src/image.cpp
    src/recording.cpp
    src/binding.cpp
    include/media.h
)

target_include_directories(${PROJECT_NAME} PRIVATE include)
target_link_libraries(${PROJECT_NAME} PRIVATE Python3::Module)
target_compile_features(${PROJECT_NAME} PRIVATE cxx_std_17)
target_compile_definitions(${PROJECT_NAME} PRIVATE
    WIN32_LEAN_AND_MEAN=1
    NOMINMAX=1
    _WIN32_WINNT=0x0A00
)
if(MSVC)
    target_compile_options(${PROJECT_NAME} PRIVATE /EHsc /utf-8)
endif()
