from setuptools import setup, Extension
import sys
import platform

c_modules = []

match sys.platform:
    case "darwin":
        # 必须得是arm64架构的
        if platform.machine() != "arm64":
            raise ValueError("Only arm64 architecture is supported on macOS")

        # 编译C模块
        include_dirs = [
            "native_code/macOS/include",
            "native_code/macOS/third_party/turbojpeg/include",
        ]
        srcs = [
            "native_code/macOS/src/adb_scr_media.c",
            "native_code/macOS/src/jpg_encoder.c",
            "native_code/macOS/src/vtb_decoder.c",
            "native_code/macOS/src/vtb_helper.c",
        ]

        library_dirs = ["native_code/macOS/third_party/turbojpeg/lib"]
        libraries = ["turbojpeg"]

        c_modules.append(
            Extension(
                "adb_scr.media_ext._adb_scr_media",
                sources=srcs,
                include_dirs=include_dirs,
                library_dirs=library_dirs,
                libraries=libraries,
                extra_compile_args=["-O3"],
                extra_link_args=[
                    "-framework",
                    "CoreFoundation",
                    "-framework",
                    "VideoToolbox",
                    "-framework",
                    "Accelerate",
                ],
            )
        )
    case _:
        raise ValueError("Unsupported platform")

setup(ext_modules=c_modules)
