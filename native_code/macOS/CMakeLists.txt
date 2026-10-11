# 仅供 IDE 索引；实际构建由 setup.py 中的 Extension 完成。
cmake_minimum_required(VERSION 3.18)
project(adb_scr_media LANGUAGES C OBJC)

find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter Development.Module)

# 不加入默认构建；源文件清单与 setup.py 的 macOS 扩展保持一致。
add_library(${PROJECT_NAME} STATIC EXCLUDE_FROM_ALL
    src/adb_scr_media.c
    src/jpg_encoder.c
    src/frame_jpg_encoder.m
    src/recording.m
    src/vtb_decoder.c
    src/vtb_helper.c
    include/frame_jpg_encoder.h
    include/jpg_encoder.h
    include/recording.h
    include/vtb_decoder.h
    include/vtb_helper.h
)

target_include_directories(${PROJECT_NAME} PRIVATE include)
target_link_libraries(${PROJECT_NAME} PRIVATE Python3::Module)
set_target_properties(${PROJECT_NAME} PROPERTIES
    C_STANDARD 11
    C_STANDARD_REQUIRED YES
    OBJC_STANDARD 11
    OBJC_STANDARD_REQUIRED YES
)
