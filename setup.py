from setuptools import setup, Extension
import sys

# 编译C模块
c_modules = []
match sys.platform:
    case "darwin":
        srcs = [
            "native_code/macOS/src/adb_scr_media.c",
            "native_code/macOS/src/jpg_encoder.c",
            "native_code/macOS/src/vtb_decoder.c",
            "native_code/macOS/src/vtb_helper.c",
        ]
        include_dirs = [
            "native_code/macOS/include",
            "native_code/macOS/third_party/turbojpeg/include",
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
