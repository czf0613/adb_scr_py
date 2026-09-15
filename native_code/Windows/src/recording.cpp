#include "media.h"

namespace media {
namespace {
ComPtr<IMFMediaType> audio_type(const Bytes& config, uint32_t& rate) {
    if (config.size() < 2) {
        throw Failure("invalid AAC AudioSpecificConfig");
    }
    unsigned object = config[0] >> 3;
    unsigned frequency = ((config[0] & 7) << 1) | (config[1] >> 7);
    unsigned channels = (config[1] >> 3) & 15;
    static const uint32_t rates[] = {96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350};
    if (object != 2 || frequency >= 13 || channels < 1 || channels > 2 || (config[1] & 7)) {
        throw Failure("expected AAC-LC, 1024 samples per packet, mono or stereo");
    }
    rate = rates[frequency];
    ComPtr<IMFMediaType> type;
    check(MFCreateMediaType(&type), "AAC media type");
    check(type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Audio), "AAC major type");
    check(type->SetGUID(MF_MT_SUBTYPE, MEDIASUBTYPE_RAW_AAC1), "AAC subtype");
    check(type->SetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, rate), "AAC sample rate");
    check(type->SetUINT32(MF_MT_AUDIO_NUM_CHANNELS, channels), "AAC channels");
    check(type->SetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, 16), "AAC bit depth");
    check(type->SetUINT32(MF_MT_AUDIO_AVG_BYTES_PER_SECOND, 16000), "AAC nominal byte rate");
    check(type->SetUINT32(MF_MT_AUDIO_BLOCK_ALIGNMENT, 1), "AAC block alignment");
    check(type->SetBlob(MF_MT_USER_DATA, config.data(), static_cast<UINT32>(config.size())), "AAC configuration");
    return type;
}
void codec_value(ICodecAPI* codec, const GUID& key, ULONG value, const char* label, bool required = true) {
    VARIANT variant;
    VariantInit(&variant);
    variant.vt = VT_UI4;
    variant.ulVal = value;
    HRESULT hr = codec->SetValue(&key, &variant);
    if (required) {
        check(hr, label);
    }
}
}
Recorder::Recorder(const FramePtr& frame, const std::wstring& path, const Bytes& audio,
                   int64_t source_pts, int fps, double quality)
    : width(frame->width), height(frame->height), origin(source_pts) {
    if (fps < 1 || fps > 240 || source_pts < 0 || source_pts > INT64_MAX / 10 ||
        !std::isfinite(quality) || quality < 0 || quality > 1) {
        throw std::invalid_argument("recording quality, FPS or timestamp out of range");
    }
    try {
        queue.call([&] {
            // Validate all media options before obtaining exclusive file ownership.
            ComPtr<IMFMediaType> audio_format;
            if (!audio.empty()) {
                audio_format = audio_type(audio, sample_rate);
            }
            check(MFCreateFile(MF_ACCESSMODE_READWRITE, MF_OPENMODE_FAIL_IF_EXIST,
                MF_FILEFLAGS_NONE, path.c_str(), &stream), "create new MP4 file");
            ComPtr<IMFAttributes> attributes;
            check(MFCreateAttributes(&attributes, 4), "sink writer attributes");
            check(attributes->SetGUID(MF_TRANSCODE_CONTAINERTYPE, MFTranscodeContainerType_MPEG4), "MP4 container");
            check(attributes->SetUINT32(MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS, TRUE), "allow system hardware transforms");
            check(attributes->SetUINT32(MF_LOW_LATENCY, TRUE), "low latency writer");
            check(attributes->SetUINT32(MF_SINK_WRITER_DISABLE_THROTTLING, TRUE), "disable implicit writer throttling");
            check(MFCreateSinkWriterFromURL(nullptr, stream.Get(), attributes.Get(), &writer), "create MP4 sink writer");
            auto output = video_type(MFVideoFormat_H264, width, height, fps);
            // Required media-type field; the encoder is subsequently configured for quality VBR.
            uint64_t nominal = static_cast<uint64_t>(width) * height * fps / 5;
            check(output->SetUINT32(MF_MT_AVG_BITRATE, static_cast<UINT32>(std::min<uint64_t>(MAXDWORD, std::max<uint64_t>(1000000, nominal)))), "H.264 nominal bitrate");
            check(output->SetUINT32(MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_Main), "H.264 profile");
            check(writer->AddStream(output.Get(), &video_stream), "add H.264 stream");
            auto input = video_type(MFVideoFormat_NV12, width, height, fps);
            ComPtr<IMFAttributes> parameters;
            check(MFCreateAttributes(&parameters, 3), "encoder parameters");
            parameters->SetUINT32(CODECAPI_AVEncCommonRateControlMode, eAVEncCommonRateControlMode_Quality);
            parameters->SetUINT32(CODECAPI_AVEncCommonQuality,
                std::max<ULONG>(1, static_cast<ULONG>(std::floor(quality * 100 + 0.5))));
            parameters->SetUINT32(CODECAPI_AVEncMPVDefaultBPictureCount, 0);
            check(writer->SetInputMediaType(video_stream, input.Get(), parameters.Get()), "set NV12 recording input");
            ComPtr<ICodecAPI> codec;
            check(writer->GetServiceForStream(video_stream, GUID_NULL, IID_PPV_ARGS(&codec)), "H.264 encoder configuration");
            codec_value(codec.Get(), CODECAPI_AVEncCommonRateControlMode, eAVEncCommonRateControlMode_Quality, "quality VBR");
            codec_value(codec.Get(), CODECAPI_AVEncCommonQuality,
                std::max<ULONG>(1, static_cast<ULONG>(std::floor(quality * 100 + 0.5))), "recording quality");
            codec_value(codec.Get(), CODECAPI_AVEncMPVDefaultBPictureCount, 0, "disable B frames", false);
            if (audio_format) {
                check(writer->AddStream(audio_format.Get(), &audio_stream), "add AAC stream");
                check(writer->SetInputMediaType(audio_stream, audio_format.Get(), nullptr), "set AAC passthrough input");
            }
            check(writer->BeginWriting(), "begin MP4 writing");
            // A one-microsecond anchor rounds to zero duration in the Windows
            // MP4 track timescale. One millisecond is representable at every FPS.
            pending = prepare_video(frame);
            write_video(pending, 0, 1000);
        });
    } catch (...) {
        queue.call([this] { writer.Reset(); stream.Reset(); pending.reset(); }, true);
        queue.close();
        throw;
    }
}
Recorder::~Recorder() {
    try {
        stop(INT64_MIN);
    } catch (...) {
    }
}
FramePtr Recorder::prepare_video(const FramePtr& frame) {
    ComPtr<IMFSample> source;
    if (frame->width != width || frame->height != height) {
        source = convert_frame(frame, MFVideoFormat_NV12, width, height, true);
    } else {
        source = frame->sample;
    }
    // A CPU-addressable NV12 sample is accepted by both software and hardware encoders.
    // Do not mutate timing on the shared decoded sample.
    ComPtr<IMFMediaBuffer> buffer;
    check(source->ConvertToContiguousBuffer(&buffer), "recording NV12 buffer");
    ComPtr<IMF2DBuffer> image;
    Bytes pixels;
    if (SUCCEEDED(buffer.As(&image))) {
        DWORD size;
        check(image->GetContiguousLength(&size), "NV12 contiguous size");
        pixels.resize(size);
        check(image->ContiguousCopyTo(pixels.data(), size), "copy recording NV12");
    } else {
        BYTE* data;
        DWORD size;
        check(buffer->Lock(&data, nullptr, &size), "lock recording NV12");
        pixels.assign(data, data + size);
        buffer->Unlock();
    }
    size_t expected = static_cast<size_t>(width) * height * 3 / 2;
    // Aligned coded dimensions need a processor pass to remove the coded aperture.
    if (pixels.size() != expected) {
        auto fitted = std::make_shared<Frame>(*frame);
        fitted->sample = convert_frame(frame, MFVideoFormat_NV12, width, height,
            frame->width != width || frame->height != height);
        fitted->type = video_type(MFVideoFormat_NV12, width, height);
        fitted->device.reset();
        fitted->width = width;
        fitted->height = height;
        ComPtr<IMFMediaBuffer> packed;
        check(fitted->sample->ConvertToContiguousBuffer(&packed), "converted NV12 buffer");
        ComPtr<IMF2DBuffer> packed_image;
        if (SUCCEEDED(packed.As(&packed_image))) {
            DWORD length;
            check(packed_image->GetContiguousLength(&length), "converted NV12 size");
            pixels.resize(length);
            check(packed_image->ContiguousCopyTo(pixels.data(), length), "pack NV12");
        } else {
            BYTE* data;
            DWORD length;
            check(packed->Lock(&data, nullptr, &length), "lock converted NV12");
            pixels.assign(data, data + length);
            packed->Unlock();
        }
    }
    if (pixels.size() != expected) {
        throw Failure("recording NV12 buffer dimensions do not match the canvas");
    }
    if (frame->width != width || frame->height != height) {
        // Normalize NV12 black explicitly: processor border RGB conversion is
        // inconsistent across system processor paths. Preserve the scaled image.
        RECT rect = fitted_rect(frame->width, frame->height, width, height);
        for (uint32_t y = 0; y < height; y++) {
            auto row = pixels.data() + static_cast<size_t>(y) * width;
            if (y < static_cast<uint32_t>(rect.top) || y >= static_cast<uint32_t>(rect.bottom)) {
                memset(row, 16, width);
            } else {
                memset(row, 16, rect.left);
                memset(row + rect.right, 16, width - rect.right);
            }
        }
        for (uint32_t y = 0; y < height / 2; y++) {
            auto row = pixels.data() + static_cast<size_t>(width) * height + static_cast<size_t>(y) * width;
            if (y < static_cast<uint32_t>(rect.top / 2) || y >= static_cast<uint32_t>(rect.bottom / 2)) {
                memset(row, 128, width);
            } else {
                memset(row, 128, rect.left);
                memset(row + rect.right, 128, width - rect.right);
            }
        }
    }
    auto prepared = std::make_shared<Frame>();
    prepared->runtime = frame->runtime;
    prepared->sample = make_sample(pixels.data(), pixels.size(), frame->pts);
    prepared->type = video_type(MFVideoFormat_NV12, width, height);
    prepared->width = width;
    prepared->height = height;
    prepared->pts = frame->pts;
    return prepared;
}
void Recorder::write_video(const FramePtr& frame, int64_t pts, int64_t duration) {
    int64_t end = end_boundary.load();
    if (pts >= end || duration <= 0) {
        return;
    }
    duration = std::min(duration, end - pts);
    ComPtr<IMFSample> sample;
    check(MFCreateSample(&sample), "recording timed sample");
    ComPtr<IMFMediaBuffer> buffer;
    check(frame->sample->GetBufferByIndex(0, &buffer), "prepared NV12 buffer");
    check(sample->AddBuffer(buffer.Get()), "share immutable recording pixels");
    check(sample->SetSampleTime(pts * 10), "recording sample timestamp");
    check(sample->SetSampleDuration(duration * 10), "recording sample duration");
    if (pts == 0) {
        check(sample->SetUINT32(MFSampleExtension_Discontinuity, TRUE), "initial recording sample");
    }
    write_sample(video_stream, sample.Get(), video_budget, 32, 128 * 1024 * 1024,
        static_cast<size_t>(width) * height * 3 / 2);
}
void Recorder::submit(const FramePtr& frame) {
    std::lock_guard<std::mutex> lock(ingress);
    queue.check_error();
    if (!accepting || frame->pts < origin) {
        return;
    }
    if (pending_video >= 8) {
        auto error = std::make_exception_ptr(Failure("recording video queue overloaded: limit=8 frames", true));
        queue.fail(error);
        std::rethrow_exception(error);
    }
    size_t size = static_cast<size_t>(width) * height * 3 / 2;
    pending_video++;
    try {
        // Detach pixels before enqueuing: slow encoding must not pin decoder surfaces.
        // This runs on the decoder's MTA worker; the decoder ingress stays bounded.
        auto prepared = prepare_video(frame);
        queue.post([this, prepared] {
            int64_t relative = prepared->pts - origin;
            if (video_extended_for_audio && relative < pending_pts) {
                throw Failure("video arrived after the audio-driven recording boundary; media delay exceeded 500 ms", true);
            }
            if (relative >= pending_pts && relative < end_boundary.load()) {
                if (relative > pending_pts) {
                    write_video(pending, pending_pts, relative - pending_pts);
                }
                pending = prepared;
                pending_pts = relative;
            }
            std::lock_guard<std::mutex> lock(ingress);
            pending_video--;
        }, size);
    } catch (...) {
        pending_video--;
        queue.fail(std::current_exception());
        throw;
    }
}
void Recorder::audio(const uint8_t* data, size_t size, int64_t pts) {
    std::lock_guard<std::mutex> lock(ingress);
    queue.check_error();
    if (!accepting || !sample_rate || !size || size > 1024 * 1024 || pts < 0 || pts > INT64_MAX / 10) {
        auto error = std::make_exception_ptr(Failure("recording audio is unavailable, stopped, or packet is invalid"));
        queue.fail(error);
        std::rethrow_exception(error);
    }
    if (pts < origin) {
        return;
    }
    if (pending_audio >= 256 || size > 4 * 1024 * 1024 - audio_bytes) {
        auto error = std::make_exception_ptr(Failure("recording audio queue overloaded: limit=256 packets/4 MiB", true));
        queue.fail(error);
        std::rethrow_exception(error);
    }
    auto bytes = std::make_shared<Bytes>(data, data + size);
    pending_audio++;
    audio_bytes += size;
    try {
        queue.post([this, bytes, pts] {
            int64_t relative = pts - origin;
            if (relative < end_boundary.load()) {
                if (pts <= last_audio_pts) {
                    throw Failure("AAC timestamps must increase");
                }
                if (first_audio_pts < 0) {
                    first_audio_pts = relative;
                }
                int64_t expected = first_audio_pts + audio_count * 1024000000LL / sample_rate;
                int64_t tolerance = 1024000000LL / sample_rate;
                if (std::abs(relative - expected) > tolerance) {
                    throw Failure("AAC timestamp gap or overlap exceeds one packet; Windows passthrough timeline cannot be guaranteed");
                }
                int64_t duration = (audio_count + 1) * 1024000000LL / sample_rate - audio_count * 1024000000LL / sample_rate;
                if (pending && relative - pending_pts >= 1000000) {
                    // The MP4 sink stops requesting AAC when video is far behind.
                    // Commit a static-picture segment, leaving 500 ms for video
                    // already in transit/decode. Late video fails instead of dropping.
                    int64_t boundary = relative - 500000;
                    write_video(pending, pending_pts, boundary - pending_pts);
                    pending_pts = boundary;
                    video_extended_for_audio = true;
                }
                auto sample = make_sample(bytes->data(), bytes->size(), expected, duration);
                write_sample(audio_stream, sample.Get(), audio_budget, 256, 4 * 1024 * 1024, bytes->size());
                audio_count++;
                last_audio_pts = pts;
            }
            std::lock_guard<std::mutex> lock(ingress);
            pending_audio--;
            audio_bytes -= bytes->size();
        }, size);
    } catch (...) {
        pending_audio--;
        audio_bytes -= size;
        throw;
    }
}
void Recorder::check_sink_budget(DWORD stream_index, SinkBudget& budget, size_t max_count,
                                 size_t max_bytes, size_t incoming) {
    MF_SINK_WRITER_STATISTICS stats{};
    stats.cb = sizeof(stats);
    check(writer->GetStatistics(stream_index, &stats), "query sink writer backlog");
#ifdef ADB_SCR_TESTING
    if (stalled_sink_statistics) { stats.qwNumSamplesProcessed = 0; }
#endif
    while (!budget.outstanding.empty() && budget.outstanding.front().sequence <= stats.qwNumSamplesProcessed) {
        budget.bytes -= budget.outstanding.front().bytes;
        budget.outstanding.pop_front();
    }
    if ((incoming && budget.outstanding.size() >= max_count) || incoming > max_bytes ||
        budget.bytes > max_bytes - incoming || stats.dwByteCountQueued > max_bytes - incoming) {
        throw Failure("sink writer backlog exceeded its sample or byte budget", true);
    }
    if (!budget.outstanding.empty() && Clock::now() - budget.outstanding.front().accepted > std::chrono::seconds(5)) {
        throw Failure("sink writer backlog exceeded 5 seconds: stream=" + std::to_string(stream_index) +
            " received=" + std::to_string(stats.qwNumSamplesReceived) +
            " encoded=" + std::to_string(stats.qwNumSamplesEncoded) +
            " processed=" + std::to_string(stats.qwNumSamplesProcessed), true);
    }
}
void Recorder::write_sample(DWORD stream_index, IMFSample* sample, SinkBudget& budget,
                            size_t max_count, size_t max_bytes, size_t bytes) {
    check_sink_budget(stream_index, budget, max_count, max_bytes, bytes);
    auto accepted = Clock::now();
    check(writer->WriteSample(stream_index, sample), "write recording sample");
    budget.outstanding.push_back({++budget.submitted, bytes, accepted});
    budget.bytes += bytes;
}
void Recorder::check_sink_budgets() {
    if (writer) {
        check_sink_budget(video_stream, video_budget, 32, 128 * 1024 * 1024);
        if (sample_rate) {
            check_sink_budget(audio_stream, audio_budget, 256, 4 * 1024 * 1024);
        }
    }
}
void Recorder::check_error() {
    queue.check_error();
    std::lock_guard<std::mutex> lock(ingress);
    if (accepting && !status_pending.exchange(true)) {
        // Keep COM calls on the writer worker. The next monitor tick can still
        // detect an expired job even if this status operation gets stuck.
        queue.post([this] {
            check_sink_budgets();
            status_pending.store(false);
        });
    }
}
void Recorder::stop(int64_t end_pts) {
    std::lock_guard<std::mutex> stop_lock(stop_mutex);
    if (stopped) {
        queue.check_error();
        return;
    }
    {
        std::lock_guard<std::mutex> lock(ingress);
        accepting = false;
        if (end_pts != INT64_MIN) {
            if (end_pts < 0 || end_pts > INT64_MAX / 10) {
                throw std::invalid_argument("recording end timestamp out of range");
            }
            end_boundary.store(std::max<int64_t>(1000, end_pts - origin));
        }
    }
    queue.call([this, end_pts] {
        try {
            queue.check_error();
            int64_t end = end_pts == INT64_MIN ? pending_pts + 1 : end_boundary.load();
            if (pending && pending_pts < end) {
                write_video(pending, pending_pts, end - pending_pts);
            }
        } catch (...) {
            queue.fail(std::current_exception());
        }
        if (writer) {
            HRESULT hr = writer->Finalize();
            if (FAILED(hr)) {
                try { check(hr, "finalize MP4"); } catch (...) { queue.fail(std::current_exception()); }
            }
        }
        pending.reset();
        writer.Reset();
        stream.Reset();
    }, true);
    queue.close();
    stopped = true;
    queue.check_error();
}
#ifdef ADB_SCR_TESTING
void Recorder::block(HANDLE entered, HANDLE release) {
    queue.post([entered, release] { SetEvent(entered); WaitForSingleObject(release, 10000); });
}
void Recorder::stall_sink_statistics(bool expired) {
    queue.call([this, expired] {
        stalled_sink_statistics = true;
        if (expired) {
            for (auto& entry : video_budget.outstanding) {
                entry.accepted -= std::chrono::seconds(6);
            }
        }
    });
}
#endif
}
