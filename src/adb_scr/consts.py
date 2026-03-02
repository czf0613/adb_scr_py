# 严禁from .consts import XXX 来拿到这些常量，会拿到当时的值，而拿不到新的值

SCRCPY_VERSION = "3.2"
# scrcpy-server.bin释放出来的路径
SCRCPY_SERVER_PATH = ""
ADB_PATH = "adb"
ADB_VERSION = "unknown"
SCRCPY_PATH_ON_DEVICE = "/data/local/tmp/scrcpy-server.jar"
# 录屏的帧率，这个可能以后要改，因为macOS有硬件解码器，这玩意性能强，有些平台没有硬件解码支持就会出问题
SCREEN_FPS = 30
