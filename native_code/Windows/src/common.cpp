#include "media.h"
#include <sstream>

namespace media {
void check(HRESULT hr, const char* operation) {
    if (FAILED(hr)) {
        std::ostringstream message;
        message << operation << " failed (HRESULT 0x" << std::hex << static_cast<uint32_t>(hr) << ')';
        throw Failure(message.str());
    }
}
Apartment::Apartment() : result(CoInitializeEx(nullptr, COINIT_MULTITHREADED)) {
    if (result != RPC_E_CHANGED_MODE) {
        check(result, "initialize COM");
    }
}
Apartment::~Apartment() {
    if (SUCCEEDED(result)) {
        CoUninitialize();
    }
}
Runtime::Runtime() { check(MFStartup(MF_VERSION, MFSTARTUP_LITE), "initialize Media Foundation"); }
Runtime::~Runtime() { MFShutdown(); }

Queue::Queue(const char* label, size_t count, size_t bytes)
    : max_jobs(count), max_bytes(bytes), name(label) {
    std::promise<void> ready;
    auto initialized = ready.get_future();
    thread = std::thread(&Queue::loop, this, std::move(ready));
    try {
        initialized.get();
    } catch (...) {
        thread.join();
        throw;
    }
}
Queue::~Queue() { close(); }
void Queue::expire_locked() {
    auto oldest = active ? active : (jobs.empty() ? nullptr : jobs.front());
    if (!error && oldest && !oldest->cleanup && Clock::now() - oldest->accepted > std::chrono::seconds(5)) {
        error = std::make_exception_ptr(Failure(name + " queue exceeded 5000 ms latency budget", true));
    }
}
std::future<void> Queue::post(std::function<void()> run, size_t bytes, bool cleanup) {
    std::lock_guard<std::mutex> lock(mutex);
    expire_locked();
    if (!cleanup && error) {
        std::rethrow_exception(error);
    }
    if (closing) {
        throw Failure(name + " queue has closed");
    }
    if (!cleanup && (pending >= max_jobs || bytes > max_bytes - pending_bytes)) {
        error = std::make_exception_ptr(Failure(name + " queue overloaded: pending=" +
            std::to_string(pending) + ", bytes=" + std::to_string(pending_bytes) +
            ", limits=" + std::to_string(max_jobs) + "/" + std::to_string(max_bytes), true));
        wake.notify_all();
        std::rethrow_exception(error);
    }
    auto job = std::make_shared<Job>();
    job->run = std::move(run);
    job->bytes = bytes;
    job->cleanup = cleanup;
    job->accepted = Clock::now();
    auto result = job->done.get_future();
    pending++;
    pending_bytes += bytes;
    jobs.push_back(std::move(job));
    wake.notify_one();
    return result;
}
void Queue::call(std::function<void()> run, bool cleanup) { post(std::move(run), 0, cleanup).get(); }
void Queue::check_error() {
    std::lock_guard<std::mutex> lock(mutex);
    expire_locked();
    if (error) {
        std::rethrow_exception(error);
    }
}
void Queue::fail(std::exception_ptr value) {
    std::lock_guard<std::mutex> lock(mutex);
    if (!error) {
        error = value;
    }
    wake.notify_all();
}
void Queue::close() {
    {
        std::lock_guard<std::mutex> lock(mutex);
        closing = true;
        wake.notify_all();
    }
    if (thread.joinable()) {
        thread.join();
    }
}
void Queue::loop(std::promise<void> ready) {
    bool initialized = false;
    try {
        Apartment apartment;
        runtime = std::make_shared<Runtime>();
        ready.set_value();
        initialized = true;
        while (true) {
            std::shared_ptr<Job> job;
            std::exception_ptr failure;
            {
                std::unique_lock<std::mutex> lock(mutex);
                wake.wait(lock, [&] { return closing || !jobs.empty(); });
                if (jobs.empty()) {
                    break;
                }
                expire_locked();
                job = jobs.front();
                jobs.pop_front();
                active = job;
                failure = error;
            }
            try {
                if (!job->cleanup && failure) {
                    std::rethrow_exception(failure);
                }
                job->run();
                job->done.set_value();
            } catch (...) {
                fail(std::current_exception());
                job->done.set_exception(std::current_exception());
            }
            {
                std::lock_guard<std::mutex> lock(mutex);
                pending--;
                pending_bytes -= job->bytes;
                active.reset();
            }
            // Captured COM resources are released before MFShutdown/CoUninitialize.
            job.reset();
        }
    } catch (...) {
        if (!initialized) {
            ready.set_exception(std::current_exception());
        }
    }
}

Device::Device() {
#ifdef ADB_SCR_TEST_DISABLE_D3D
    return;
#endif
    D3D_FEATURE_LEVEL level;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
        D3D11_CREATE_DEVICE_VIDEO_SUPPORT | D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr, 0, D3D11_SDK_VERSION, &device, &level, nullptr);
    if (FAILED(hr)) {
        return; // An acceleration hint; no hardware requirement.
    }
    ComPtr<ID3D10Multithread> protection;
    if (SUCCEEDED(device.As(&protection))) {
        protection->SetMultithreadProtected(TRUE);
    }
    UINT token = 0;
    if (FAILED(MFCreateDXGIDeviceManager(&token, &manager)) || FAILED(manager->ResetDevice(device.Get(), token))) {
        manager.Reset();
        device.Reset();
    }
}
ComPtr<IMFMediaType> video_type(REFGUID format, uint32_t w, uint32_t h, uint32_t fps) {
    ComPtr<IMFMediaType> type;
    check(MFCreateMediaType(&type), "create video type");
    check(type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video), "video major type");
    check(type->SetGUID(MF_MT_SUBTYPE, format), "video subtype");
    if (format == MFVideoFormat_ARGB32 || format == MFVideoFormat_RGB32) {
        check(type->SetUINT32(MF_MT_DEFAULT_STRIDE, w * 4), "top-down RGB stride");
    }
    check(MFSetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, w, h), "video dimensions");
    check(MFSetAttributeRatio(type.Get(), MF_MT_FRAME_RATE, fps, 1), "frame rate");
    check(MFSetAttributeRatio(type.Get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1), "pixel aspect");
    check(type->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive), "interlace mode");
    check(type->SetUINT32(MF_MT_YUV_MATRIX, MFVideoTransferMatrix_BT709), "YUV matrix");
    check(type->SetUINT32(MF_MT_VIDEO_PRIMARIES, MFVideoPrimaries_BT709), "primaries");
    check(type->SetUINT32(MF_MT_TRANSFER_FUNCTION, MFVideoTransFunc_709), "transfer function");
    check(type->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE,
        format == MFVideoFormat_ARGB32 || format == MFVideoFormat_RGB32 ? MFNominalRange_0_255 : MFNominalRange_16_235), "range");
    return type;
}
ComPtr<IMFSample> make_sample(const uint8_t* data, size_t size, int64_t time, int64_t duration) {
    if (size > MAXDWORD || time < 0 || time > INT64_MAX / 10 || duration < 0 || duration > INT64_MAX / 10) {
        throw Failure("media sample size or timestamp out of range");
    }
    ComPtr<IMFMediaBuffer> buffer;
    check(MFCreateMemoryBuffer(static_cast<DWORD>(size), &buffer), "allocate sample");
    BYTE* target;
    check(buffer->Lock(&target, nullptr, nullptr), "lock sample");
    if (data && size) {
        memcpy(target, data, size);
    }
    buffer->Unlock();
    check(buffer->SetCurrentLength(static_cast<DWORD>(size)), "sample length");
    ComPtr<IMFSample> sample;
    check(MFCreateSample(&sample), "create sample");
    check(sample->AddBuffer(buffer.Get()), "attach sample buffer");
    check(sample->SetSampleTime(time * 10), "sample timestamp");
    if (duration) {
        check(sample->SetSampleDuration(duration * 10), "sample duration");
    }
    return sample;
}
ComPtr<IMFSample> output_sample(IMFTransform* transform, HRESULT& status) {
    MFT_OUTPUT_STREAM_INFO info{};
    check(transform->GetOutputStreamInfo(0, &info), "output stream info");
    ComPtr<IMFSample> supplied;
    if (!(info.dwFlags & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES))) {
        check(MFCreateSample(&supplied), "allocate output sample");
        ComPtr<IMFMediaBuffer> buffer;
        check(MFCreateAlignedMemoryBuffer(info.cbSize, info.cbAlignment ? info.cbAlignment - 1 : 0, &buffer), "allocate output buffer");
        check(supplied->AddBuffer(buffer.Get()), "attach output buffer");
    }
    MFT_OUTPUT_DATA_BUFFER data{};
    data.pSample = supplied.Get();
    DWORD flags = 0;
    status = transform->ProcessOutput(0, 1, &data, &flags);
    if (data.pEvents) {
        data.pEvents->Release();
    }
    ComPtr<IMFSample> result;
    if (supplied) {
        result = supplied;
    } else {
        result.Attach(data.pSample);
    }
    if (FAILED(status) || (data.dwStatus & MFT_OUTPUT_DATA_BUFFER_NO_SAMPLE)) {
        return nullptr;
    }
    return result;
}
void begin_transform(IMFTransform* transform) {
    check(transform->ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0), "begin streaming");
    check(transform->ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0), "start stream");
}
}
