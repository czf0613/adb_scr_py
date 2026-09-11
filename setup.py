from setuptools import setup, Extension
import sys

# 编译C模块
c_modules = []
match sys.platform:
    case "darwin":
        srcs = [
            "native_code/macOS/src/adb_scr_media.c",
            "native_code/macOS/src/jpg_encoder.c",
            "native_code/macOS/src/frame_jpg_encoder.m",
            "native_code/macOS/src/recording.m",
            "native_code/macOS/src/vtb_decoder.c",
            "native_code/macOS/src/vtb_helper.c",
        ]
        c_modules.append(
            Extension(
                "adb_scr.media_ext._adb_scr_media",
                sources=srcs,
                include_dirs=["native_code/macOS/include"],
                extra_compile_args=["-O3"],
                extra_link_args=[
                    "-framework",
                    "CoreFoundation",
                    "-framework",
                    "CoreGraphics",
                    "-framework",
                    "ImageIO",
                    "-framework",
                    "VideoToolbox",
                    "-framework",
                    "Accelerate",
                    "-framework",
                    "Foundation",
                    "-framework",
                    "CoreImage",
                    "-framework",
                    "AVFoundation",
                    "-framework",
                    "AudioToolbox",
                    "-framework",
                    "Metal",
                ],
            )
        )
    case _:
        raise ValueError("Unsupported platform")

setup(ext_modules=c_modules)
