#include "media.h"

namespace media {
namespace {
std::vector<DWORD> nalu_lengths(const Bytes& bytes) {
    std::vector<size_t> starts;
    for (size_t i = 0; i + 3 < bytes.size(); i++) {
        if (bytes[i] == 0 && bytes[i + 1] == 0) {
            size_t prefix = bytes[i + 2] == 1 ? 3 :
                (i + 4 < bytes.size() && bytes[i + 2] == 0 && bytes[i + 3] == 1 ? 4 : 0);
            if (prefix) {
                starts.push_back(i);
                i += prefix - 1;
            }
        }
    }
    if (starts.empty() || starts.front() != 0) {
        throw Failure("H.264 sample must contain Annex B NAL units");
    }
    starts.push_back(bytes.size());
    std::vector<DWORD> lengths;
    for (size_t i = 1; i < starts.size(); i++) {
        lengths.push_back(static_cast<DWORD>(starts[i] - starts[i - 1]));
    }
    return lengths;
}
class Bits {
    Bytes bytes;
    size_t bit = 0;
public:
    explicit Bits(const Bytes& rbsp) : bytes(rbsp) {}
    uint32_t read(unsigned count) {
        if (count > 32 || bit + count > bytes.size() * 8) {
            throw Failure("truncated H.264 SPS");
        }
        uint32_t result = 0;
        for (unsigned i = 0; i < count; i++, bit++) {
            result = (result << 1) | ((bytes[bit / 8] >> (7 - bit % 8)) & 1);
        }
        return result;
    }
    uint32_t ue() {
        unsigned zeros = 0;
        while (!read(1)) {
            if (++zeros > 30) {
                throw Failure("invalid H.264 Exp-Golomb value");
            }
        }
        return ((1u << zeros) - 1) + read(zeros);
    }
    int32_t se() {
        uint32_t value = ue();
        return value & 1 ? static_cast<int32_t>((value + 1) / 2) : -static_cast<int32_t>(value / 2);
    }
};
}
std::pair<uint32_t, uint32_t> sps_dimensions(const Bytes& config) {
    size_t start = 0, end = config.size();
    bool found = false;
    for (size_t i = 0; i + 3 < config.size(); i++) {
        size_t prefix = config[i] == 0 && config[i + 1] == 0 ?
            (config[i + 2] == 1 ? 3 : (i + 4 < config.size() && config[i + 2] == 0 && config[i + 3] == 1 ? 4 : 0)) : 0;
        if (!prefix) {
            continue;
        }
        if (found) {
            end = i;
            break;
        }
        if ((config[i + prefix] & 31) == 7) {
            found = true;
            start = i + prefix + 1;
        }
        i += prefix - 1;
    }
    if (!found) {
        throw Failure("Annex B configuration has no SPS");
    }
    Bytes rbsp;
    unsigned zeros = 0;
    for (size_t i = start; i < end; i++) {
        uint8_t byte = config[i];
        if (zeros >= 2 && byte == 3) {
            zeros = 0;
            continue;
        }
        rbsp.push_back(byte);
        zeros = byte == 0 ? zeros + 1 : 0;
    }
    Bits bits(rbsp);
    uint32_t profile = bits.read(8);
    bits.read(16);
    bits.ue();
    uint32_t chroma = 1;
    if (profile == 100 || profile == 110 || profile == 122 || profile == 244 ||
        profile == 44 || profile == 83 || profile == 86 || profile == 118 ||
        profile == 128 || profile == 138 || profile == 139 || profile == 134 || profile == 135) {
        chroma = bits.ue();
        if (chroma > 3) {
            throw Failure("invalid SPS chroma format");
        }
        if (chroma == 3 && bits.read(1)) {
            chroma = 0;
        }
        if (bits.ue() != 0 || bits.ue() != 0) {
            throw Failure("only 8-bit H.264 is supported");
        }
        bits.read(1);
        if (bits.read(1)) {
            for (unsigned i = 0; i < (chroma == 3 ? 12u : 8u); i++) {
                if (bits.read(1)) {
                    int last = 8, next = 8;
                    for (unsigned j = 0; j < (i < 6 ? 16u : 64u); j++) {
                        if (next) {
                            next = static_cast<int>((static_cast<int64_t>(last) + bits.se() + 256) & 255);
                        }
                        last = next ? next : last;
                    }
                }
            }
        }
    }
    bits.ue();
    uint32_t order = bits.ue();
    if (order == 0) {
        bits.ue();
    } else if (order == 1) {
        bits.read(1);
        bits.se();
        bits.se();
        uint32_t count = bits.ue();
        if (count > 255) {
            throw Failure("invalid SPS picture order cycle");
        }
        for (uint32_t i = 0; i < count; i++) {
            bits.se();
        }
    } else if (order > 2) {
        throw Failure("invalid SPS picture order type");
    }
    bits.ue();
    bits.read(1);
    uint64_t w = (static_cast<uint64_t>(bits.ue()) + 1) * 16;
    uint64_t map_height = static_cast<uint64_t>(bits.ue()) + 1;
    uint32_t progressive = bits.read(1);
    uint64_t h = map_height * 16 * (2 - progressive);
    if (!progressive) {
        bits.read(1);
    }
    bits.read(1);
    if (bits.read(1)) {
        uint64_t left = bits.ue(), right = bits.ue(), top = bits.ue(), bottom = bits.ue();
        uint64_t crop_x = (chroma == 1 || chroma == 2) ? 2 : 1;
        uint64_t crop_y = (chroma == 1 ? 2 : 1) * (2 - progressive);
        if ((left + right) * crop_x >= w || (top + bottom) * crop_y >= h) {
            throw Failure("invalid SPS crop");
        }
        w -= (left + right) * crop_x;
        h -= (top + bottom) * crop_y;
    }
    if (!w || !h || w > 16384 || h > 16384 || w * h > 64 * 1024 * 1024) {
        throw Failure("H.264 dimensions exceed the native allocation budget");
    }
    return {static_cast<uint32_t>(w), static_cast<uint32_t>(h)};
}

Decoder::Decoder(const Bytes& config)
    : width(sps_dimensions(config).first), height(sps_dimensions(config).second) {
    configuration = config;
    try {
        queue.call([&] {
            check(CoCreateInstance(CLSID_CMSH264DecoderMFT, nullptr, CLSCTX_INPROC_SERVER,
                IID_PPV_ARGS(&transform)), "create Windows H.264 decoder");
            ComPtr<IMFAttributes> attributes;
            if (SUCCEEDED(transform->GetAttributes(&attributes))) {
                attributes->SetUINT32(CODECAPI_AVLowLatencyMode, TRUE);
            }
            ComPtr<ICodecAPI> codec;
            if (SUCCEEDED(transform.As(&codec))) {
                VARIANT low_latency;
                VariantInit(&low_latency);
                // The Microsoft H.264 decoder specifically requires VT_UI4.
                low_latency.vt = VT_UI4;
                low_latency.ulVal = TRUE;
                check(codec->SetValue(&CODECAPI_AVLowLatencyMode, &low_latency), "enable low-latency H.264 decoding");
            }
            auto input = video_type(MFVideoFormat_H264, width, height);
            ComPtr<IMFAttributes> output_attributes;
            if (SUCCEEDED(transform->GetOutputStreamAttributes(0, &output_attributes))) {
                // Latest frame + source being copied for recording + screenshot readers.
                output_attributes->SetUINT32(MF_SA_MINIMUM_OUTPUT_SAMPLE_COUNT, 8);
                output_attributes->SetUINT32(MF_SA_MINIMUM_OUTPUT_SAMPLE_COUNT_PROGRESSIVE, 8);
            }
            check(input->SetBlob(MF_MT_MPEG_SEQUENCE_HEADER, config.data(), static_cast<UINT32>(config.size())), "decoder sequence header");
            check(input->SetUINT32(MF_NALU_LENGTH_SET, TRUE), "complete-picture NAL metadata");
            device = std::make_shared<Device>();
            if (device->manager) {
                UINT aware = 0;
                if (attributes && SUCCEEDED(attributes->GetUINT32(MF_SA_D3D11_AWARE, &aware)) && aware) {
                    if (FAILED(transform->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER,
                        reinterpret_cast<ULONG_PTR>(device->manager.Get())))) {
                        device.reset();
                    }
                } else {
                    device.reset();
                }
            }
            HRESULT result = transform->SetInputType(0, input.Get(), 0);
            if (FAILED(result) && device) {
                transform->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER, 0);
                device.reset();
                result = transform->SetInputType(0, input.Get(), 0);
            }
            check(result, "set decoder input");
            try {
                negotiate();
            } catch (...) {
                if (!device) {
                    throw;
                }
                transform->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER, 0);
                device.reset();
                check(transform->SetInputType(0, input.Get(), 0), "set system-memory decoder input");
                negotiate();
            }
            begin_transform(transform.Get());
            if (codec) {
                VARIANT value;
                VariantInit(&value);
                value.vt = VT_UI4;
                value.ulVal = TRUE;
                check(codec->SetValue(&CODECAPI_AVLowLatencyMode, &value), "activate decoder low latency");
            }
        });
    } catch (...) {
        close();
        throw;
    }
}
Decoder::~Decoder() { close(); }
void Decoder::negotiate() {
    for (DWORD i = 0; ; i++) {
        ComPtr<IMFMediaType> type;
        HRESULT hr = transform->GetOutputAvailableType(0, i, &type);
        if (hr == MF_E_NO_MORE_TYPES) {
            throw Failure("Windows H.264 decoder has no usable NV12 output type");
        }
        check(hr, "enumerate decoder output");
        GUID subtype;
        check(type->GetGUID(MF_MT_SUBTYPE, &subtype), "decoder output subtype");
        if (subtype == MFVideoFormat_NV12) {
            check(transform->SetOutputType(0, type.Get(), 0), "set NV12 decoder output");
            output_type = type;
            return;
        }
    }
}
void Decoder::receive() {
    while (true) {
        queue.check_error();
        HRESULT status;
        auto sample = output_sample(transform.Get(), status);
        if (status == MF_E_TRANSFORM_NEED_MORE_INPUT) {
            return;
        }
        if (status == MF_E_TRANSFORM_STREAM_CHANGE) {
            negotiate();
            continue;
        }
        check(status, "decode H.264 output");
        if (!sample) {
            continue;
        }
        auto frame = std::make_shared<Frame>();
        frame->runtime = queue.lease();
        frame->sample = sample;
        frame->type = output_type;
        frame->device = device;
        frame->width = width;
        frame->height = height;
        LONGLONG pts;
        check(sample->GetSampleTime(&pts), "decoded frame timestamp");
        frame->pts = pts / 10;
        {
            std::lock_guard<std::mutex> lock(frames_mutex);
            latest = frame;
        }
        if (recorder) {
            try {
                recorder->submit(frame);
            } catch (...) {
                // The recorder stores and reports its own persistent failure.
            }
        }
    }
}
bool Decoder::enqueue(const uint8_t* data, size_t size, int64_t pts) {
    queue.check_error();
    if (closed) {
        return false;
    }
    if (size <= 4 || size > 64 * 1024 * 1024 || pts < 0 || pts > INT64_MAX / 10) {
        throw Failure("invalid H.264 packet size or timestamp");
    }
    // Queue ingress is serialized by the Capsule lock. Own bytes before returning.
    auto bytes = std::make_shared<Bytes>(data, data + size);
    queue.post([this, bytes, pts] {
        if (!configuration.empty()) {
            bytes->insert(bytes->begin(), configuration.begin(), configuration.end());
            configuration.clear();
        }
        auto sample = make_sample(bytes->data(), bytes->size(), pts, 1);
        auto lengths = nalu_lengths(*bytes);
        check(sample->SetBlob(MF_NALU_LENGTH_INFORMATION, reinterpret_cast<const UINT8*>(lengths.data()),
            static_cast<UINT32>(lengths.size() * sizeof(DWORD))), "NAL lengths");
        HRESULT hr = transform->ProcessInput(0, sample.Get(), 0);
        if (hr == MF_E_NOTACCEPTING) {
            receive();
            hr = transform->ProcessInput(0, sample.Get(), 0);
        }
        check(hr, "submit H.264 frame");
        receive();
    }, size);
    return true;
}
FramePtr Decoder::snapshot() {
    queue.check_error();
    std::lock_guard<std::mutex> lock(frames_mutex);
    return latest;
}
void Decoder::attach(std::shared_ptr<Recorder> value) {
    queue.call([this, value] { recorder = value; }, !value);
}
void Decoder::check_error() { queue.check_error(); }
void Decoder::close() {
    if (closed) {
        return;
    }
    closed = true;
    queue.call([this] {
        recorder.reset();
        if (transform) {
            transform->ProcessMessage(MFT_MESSAGE_COMMAND_FLUSH, 0);
            transform->ProcessMessage(MFT_MESSAGE_NOTIFY_END_STREAMING, 0);
        }
        {
            std::lock_guard<std::mutex> lock(frames_mutex);
            latest.reset();
        }
        output_type.Reset();
        transform.Reset();
        device.reset();
    }, true);
    queue.close();
}
#ifdef ADB_SCR_TESTING
void Decoder::block(HANDLE entered, HANDLE release) {
    queue.post([entered, release] { SetEvent(entered); WaitForSingleObject(release, 10000); });
}
#endif
}
