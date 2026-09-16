#pragma once
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <mftransform.h>
#include <wmcodecdsp.h>
#include <codecapi.h>
#include <d3d11.h>
#include <d3d10.h>
#include <wincodec.h>
#include <wrl/client.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace media {
using Microsoft::WRL::ComPtr;
using Bytes = std::vector<uint8_t>;
using Clock = std::chrono::steady_clock;
struct Failure : std::runtime_error {
    bool overloaded;
    explicit Failure(const std::string& message, bool overload = false)
        : std::runtime_error(message), overloaded(overload) {}
};
void check(HRESULT hr, const char* operation);
struct Apartment {
    HRESULT result;
    Apartment();
    ~Apartment();
};
struct Runtime {
    Runtime();
    ~Runtime();
};

// Budgets include executing jobs. A failed queue only runs cleanup jobs.
class Queue {
    struct Job {
        std::function<void()> run;
        std::promise<void> done;
        size_t bytes;
        Clock::time_point accepted;
        bool cleanup;
    };
    std::mutex mutex;
    std::condition_variable wake;
    std::deque<std::shared_ptr<Job>> jobs;
    std::shared_ptr<Job> active;
    std::thread thread;
    std::exception_ptr error;
    bool closing = false;
    size_t pending = 0, pending_bytes = 0;
    size_t max_jobs, max_bytes;
    std::string name;
    std::shared_ptr<Runtime> runtime;
    std::function<void()> idle;
    void loop(std::promise<void> ready);
    void expire_locked();
public:
    Queue(const char* label, size_t count, size_t bytes);
    ~Queue();
    std::future<void> post(std::function<void()> run, size_t bytes = 0, bool cleanup = false);
    void call(std::function<void()> run, bool cleanup = false);
    void check_error();
    void fail(std::exception_ptr value);
    void close();
    // Only the owning worker may install/clear its idle poll callback.
    void set_idle(std::function<void()> run) { idle = std::move(run); }
    std::shared_ptr<Runtime> lease() const { return runtime; }
};

struct Device {
    ComPtr<ID3D11Device> device;
    ComPtr<IMFDXGIDeviceManager> manager;
    Device();
};
struct Frame {
    std::shared_ptr<Runtime> runtime;
    ComPtr<IMFSample> sample;
    ComPtr<IMFMediaType> type;
    std::shared_ptr<Device> device;
    uint32_t width = 0, height = 0;
    int64_t pts = 0;
};
using FramePtr = std::shared_ptr<Frame>;
ComPtr<IMFMediaType> video_type(REFGUID format, uint32_t w, uint32_t h, uint32_t fps = 30);
ComPtr<IMFSample> make_sample(const uint8_t* data, size_t size, int64_t time, int64_t duration = 0);
ComPtr<IMFSample> output_sample(IMFTransform* transform, HRESULT& status);
void begin_transform(IMFTransform* transform);
void copy_bgra(IMFSample* sample, IMFMediaType* type, uint32_t w, uint32_t h, uint8_t* target);
ComPtr<IMFSample> convert_frame(const FramePtr& frame, REFGUID format, uint32_t w, uint32_t h, bool fit = false);
RECT fitted_rect(uint32_t source_w, uint32_t source_h, uint32_t w, uint32_t h);
std::pair<uint32_t, uint32_t> sps_dimensions(const Bytes& config);

struct ImageOptions {
    int quality = 75;
    double scale = 1.0;
    bool roi = false;
    int64_t x = 0, y = 0, width = 0, height = 0;
};
Bytes jpeg(uint32_t w, uint32_t h, const uint8_t* pixels, const ImageOptions& options);

class Recorder;
class Decoder {
    struct Input { size_t bytes; Clock::time_point accepted; };
    Queue queue{"decode", 32, 64 * 1024 * 1024};
    ComPtr<IMFTransform> transform;
    ComPtr<IMFMediaType> output_type;
    std::shared_ptr<Device> device;
    std::mutex frames_mutex;
    FramePtr latest;
    std::shared_ptr<Recorder> recorder;
    Bytes configuration;
    std::mutex inputs_mutex;
    size_t pending_inputs = 0, pending_bytes = 0;
    std::deque<Input> submitted;
    bool closed = false;
    void negotiate();
    void receive();
public:
    const uint32_t width, height;
    explicit Decoder(const Bytes& config);
    ~Decoder();
    bool enqueue(const uint8_t* data, size_t size, int64_t pts);
    FramePtr snapshot();
    void attach(std::shared_ptr<Recorder> value);
    void check_error();
    void close();
#ifdef ADB_SCR_TESTING
    unsigned delayed_output_polls = 0;
    bool stalled_output = false;
    void delay_output(unsigned polls, bool expired);
    void block(HANDLE entered, HANDLE release);
#endif
};

class Recorder {
    struct SinkBudget {
        struct Entry { uint64_t sequence; size_t bytes; Clock::time_point accepted; };
        std::deque<Entry> outstanding;
        uint64_t submitted = 0;
        size_t bytes = 0;
    };
    Queue queue{"recording", 256, 128 * 1024 * 1024};
    ComPtr<IMFSinkWriter> writer;
    ComPtr<IMFByteStream> stream;
    DWORD video_stream = 0, audio_stream = 0;
    uint32_t width = 0, height = 0, sample_rate = 0;
    FramePtr pending;
    int64_t pending_pts = 1000, origin = 0, last_audio_pts = -1, first_audio_pts = -1;
    int64_t audio_count = 0;
    bool video_extended_for_audio = false;
    std::atomic<int64_t> end_boundary{INT64_MAX};
    std::mutex ingress;
    bool accepting = true, stopped = false;
    size_t pending_video = 0, pending_audio = 0, audio_bytes = 0;
    std::mutex stop_mutex;
    SinkBudget video_budget, audio_budget;
    std::atomic<bool> status_pending{false};
#ifdef ADB_SCR_TESTING
    bool stalled_sink_statistics = false;
#endif
    void check_sink_budget(DWORD stream_index, SinkBudget& budget, size_t max_count,
                          size_t max_bytes, size_t incoming = 0);
    void write_sample(DWORD stream_index, IMFSample* sample, SinkBudget& budget,
                      size_t max_count, size_t max_bytes, size_t bytes);
    void check_sink_budgets();
    FramePtr prepare_video(const FramePtr& frame);
    void write_video(const FramePtr& frame, int64_t pts, int64_t duration);
public:
    Recorder(const FramePtr& frame, const std::wstring& path, const Bytes& audio,
             int64_t source_pts, int fps, double quality);
    ~Recorder();
    void submit(const FramePtr& frame);
    void audio(const uint8_t* data, size_t size, int64_t pts);
    void check_error();
    void stop(int64_t end_pts);
#ifdef ADB_SCR_TESTING
    void block(HANDLE entered, HANDLE release);
    void wait() { queue.call([] {}); }
    void stall_sink_statistics(bool expired);
#endif
};
}
