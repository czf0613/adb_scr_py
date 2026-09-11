"""macOS 原生媒体 API。

调用方必须传入符合注解及参数约束的值；此存根不提供运行时类型校验。
同一句柄的操作以原生互斥锁串行化，关闭后读取和入队安全失败。
扩展支持 free-threaded CPython，已验证 3.14t；需安装对应 ABI 的构建。
原生耗时操作会分离 Python 线程状态，在普通 CPython 下释放 GIL。
异步调用方仍须使用工作线程；此能力不扩展 Python 设备对象的事件循环边界。
"""

from typing import final

@final
class DecoderHandle:
    """由 create_decoder() 返回的不透明 Capsule，不能直接实例化。

    Notes:
        必须显式销毁。垃圾回收提供释放兜底，但不保证及时释放。
        关闭操作幂等；关闭后的句柄不能再次打开。
    """

def bgra8_to_jpg(width: int, height: int, bgra8: bytes, quality: int) -> bytes | None:
    """将紧密排列的 BGRA8 像素编码为 JPEG。

    Args:
        width: 正整数像素宽度。
        height: 正整数像素高度。
        bgra8: 每像素按 B、G、R、A 顺序排列，至少 width * height * 4 字节。
        quality: 1 到 100 的整数。

    Returns:
        JPEG bytes；缓冲区过短或编码失败时返回 None。

    Notes:
        不应依赖越界 quality 的转换行为。异步调用方应使用工作线程。
        编码期间分离 Python 线程状态；输入 bytes 保持存活，返回独立 bytes。
    """

def create_decoder(sps_and_pps: bytes) -> tuple[int, int, DecoderHandle] | None:
    """创建要求硬件加速的 H.264 解码器。

    Args:
        sps_and_pps: Annex B 格式的 SPS/PPS，使用四字节起始码。

    Returns:
        (width, height, handle)，尺寸单位为像素；失败时返回 None。

    Notes:
        包含系统调用；异步调用方必须使用 asyncio.to_thread()，并在
        取消时处理已创建资源的所有权。
    """

def enqueue_frame(handle: DecoderHandle, nalu: bytes, pts: int) -> bool:
    """提交视频包供异步解码。

    Args:
        handle: create_decoder() 返回的句柄。
        nalu: Annex B H.264 数据，使用四字节起始码；第一帧应为 IDR。
        pts: 显示时间戳，单位为微秒。

    Returns:
        提交成功为 True；句柄已关闭或提交失败为 False。

    Notes:
        不等待硬件解码完成；与同一句柄的读取、销毁互斥。
    """

def get_current_frame_bgra8(handle: DecoderHandle) -> tuple[int, int, bytes] | None:
    """读取最新帧并转换为紧密排列的 BGRA8。

    Args:
        handle: create_decoder() 返回的句柄。

    Returns:
        (width, height, pixels)，pixels 长度为 width * height * 4，
        内存通道顺序为 B、G、R、A。无帧、关闭或转换失败时返回 None。

    Notes:
        返回独立 bytes，其存活不依赖解码器。多次调用可能返回同一帧。
        同步等待 GCD 并执行转换，异步调用方必须使用 asyncio.to_thread()。
    """

def get_current_frame_jpg(
    handle: DecoderHandle,
    quality: int = 75,
    scale: float = 1.0,
    roi: tuple[int, int, int, int] | None = None,
    /,
) -> bytes | None:
    """直接从最新 CVPixelBuffer 编码 JPEG，无 Python BGRA8 中间数据。

    Args:
        handle: create_decoder() 返回的句柄。
        quality: 1–100 的整数，默认 75，不保证跨编码器画质一致。
        scale: 有限正数，默认 1.0，支持缩小与放大。
        roi: (x, y, width, height) 整数元组，原图左上角为原点，区域必须
            完全位于原图内；None 表示整图。先裁剪再缩放。

    Returns:
        独立 JPEG bytes；无帧、关闭或编码失败时返回 None。

    Raises:
        TypeError: 参数类型错误（不接受 bool 作为数值）。
        ValueError: 参数范围错误、ROI 越界或输出宽高超过 65535。
        OverflowError: 整数参数超出原生数值类型范围。

    Notes:
        输出宽高分别按 floor(裁剪尺寸 * scale + 0.5) 取整，最少 1。
        参数类型和静态范围先检查，ROI 边界及输出尺寸需要已有帧。
        原尺寸整图优先尝试硬件 JPEG，其他情况使用 Core Image。
        同一句柄的编码与销毁互斥；异步调用须使用 asyncio.to_thread()，
        并在取消时等待原生工作结束。原生入口只接受位置参数。
    """

def destroy_decoder(handle: DecoderHandle) -> None:
    """幂等关闭句柄，等待在途操作、VideoToolbox 回调及 GCD 帧队列结束。

    Args:
        handle: create_decoder() 返回的句柄，允许已关闭句柄。

    Notes:
        必须用 asyncio.to_thread() 从异步路径调用。取消等待不终止原生
        工作；调用方应等待工作结束后再释放生命周期保护。没有安全的
        强制销毁超时，不能释放仍被原生任务引用的内存。
    """

@final
class RecordingHandle:
    """独立于解码器的不透明 MP4 录制 Capsule。

    显式 stop_recording() 完成文件；析构提供尽力完成和释放的兜底。
    原生订阅持有独立引用，关闭/释放 Capsule 后解码器仍可安全解绑。
    """


def start_recording(
    decoder: DecoderHandle,
    output_file: str,
    audio_config: bytes | None,
    source_pts: int,
    fps: int,
    /,
) -> RecordingHandle:
    """使用最新 NV12 快照创建 MP4，编码零时刻关键帧并绑定解码器。

    Args:
        decoder: 必须已有解码帧的活动解码器。
        output_file: 不存在的输出路径；不创建父目录，不覆盖已有文件。
        audio_config: scrcpy AAC-LC AudioSpecificConfig，None 表示仅视频。
        source_pts: 录制开始时的设备时间，单位微秒，必须非负。
        fps: 1–240 的预期帧率；实时硬件 H.264 编码使用系统默认码率和画质。

    Raises:
        RuntimeError: 无帧、路径不可用、AAC 配置或编码/封装失败。
        ValueError: FPS 或时间戳超出范围。

    Notes:
        返回前首帧已编码并交给 AVAssetWriter。画布保持首帧尺寸，
        后续尺寸变化按比例居中留黑。必须用工作线程并保留取消所有权。
    """


def set_decoder_recording(
    decoder: DecoderHandle, recording: RecordingHandle | None, /
) -> None:
    """原子替换解码器的录制订阅，None 解绑；原生订阅持有独立引用。

    解绑已关闭解码器安全，向已关闭解码器绑定录制抛 RuntimeError。
    该调用等待解码器队列，应使用 asyncio.to_thread()。
    """


def append_recording_audio(
    recording: RecordingHandle, packet: bytes, pts: int, /
) -> None:
    """复制并提交原始 AAC access unit，保留压缩内容，不解码或转码。

    pts 为设备微秒 PTS，必须严格递增；起点前的包忽略。每包上限
    1 MiB，录制队列最多 256 包/4 MiB。队列超限、音频未配置或
    录制已结束抛 RuntimeError；异步失败在后续提交或 stop 时报告。
    一个 AAC 包时长以内的源时钟抖动按采样时钟量化；更大的累计
    间隙在停止时用系统 MP4 空编辑保留，最多 4096 处。超过一个
    包时长的累计重叠记录持久失败。首次最多缓存三个 AAC 包；
    系统封装所需的预滚与尾部补包不出现在有效播放区间内。
    必须通过工作线程调用并等待取消清理。
    """


def stop_recording(recording: RecordingHandle, end_pts: int, /) -> None:
    """拒绝新包，排空队列，补缓存尾帧并等待 MP4 完成，重复调用安全。

    end_pts 为设备微秒时间。先用 set_decoder_recording(..., None)
    解绑。录制结束时间为 end_pts - source_pts，最少一微秒；
    已入队且晚于停止边界的视频不进入有效播放区间。
    静止画面无需新包，最终时长仍覆盖开始到停止的时间。
    AAC 存在较大时间间隙时，先复制到同目录临时文件，用系统
    MP4 编辑保留间隙，再原子替换输出；音视频压缩内容不重编码。
    该处理增加停止耗时，失败时保留原输出并清理拥有的临时文件。
    异步编码、AAC、文件写入或完成失败抛 RuntimeError；重复停止
    仍报告已记录的失败。工作期间分离 Python 线程状态；异步调用
    必须使用工作线程，并等待原生结束后才释放取消或关闭所有权。
    """
