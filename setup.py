from setuptools import setup, Extension
import sys
import sysconfig

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
    case "win32":
        if sysconfig.get_platform() not in {"win-amd64", "win-arm64"}:
            raise ValueError("Windows media backend supports x64 and ARM64 only")
        c_modules.append(
            Extension(
                "adb_scr.media_ext._adb_scr_media",
                sources=[
                    f"native_code/Windows/src/{name}.cpp"
                    for name in ("common", "decoder", "image", "recording", "binding")
                ],
                include_dirs=["native_code/Windows/include"],
                depends=["native_code/Windows/include/media.h"],
                libraries=["mfplat", "mf", "mfuuid", "mfreadwrite", "wmcodecdspuuid",
                           "d3d11", "dxgi", "ole32", "oleaut32", "windowscodecs", "shlwapi"],
                define_macros=[("WIN32_LEAN_AND_MEAN", "1"), ("NOMINMAX", "1"),
                               ("_WIN32_WINNT", "0x0A00")],
                extra_compile_args=["/std:c++17", "/EHsc", "/O2", "/utf-8"],
                language="c++",
            )
        )
    case _:
        raise ValueError("Unsupported platform")

setup(ext_modules=c_modules)
