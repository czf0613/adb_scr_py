// Test-only methods included in the binding translation unit of a separate extension.
// Generate and inspect media with Windows codecs; never start ADB or access a device.
static Bytes probe_bytes(IMFSample* sample) {
    ComPtr<IMFMediaBuffer> buffer;
    check(sample->ConvertToContiguousBuffer(&buffer), "probe sample buffer");
    BYTE* data;
    DWORD size;
    check(buffer->Lock(&data, nullptr, &size), "probe sample lock");
    Bytes result(data, data + size);
    buffer->Unlock();
    return result;
}
static PyObject* test_make_h264(PyObject* self, PyObject* args) {
    (void)self;
    unsigned w, h, count = 4;
    int all_idr = 1;
    if (!PyArg_ParseTuple(args, "II|Ip", &w, &h, &count, &all_idr)) { return nullptr; }
    Bytes header;
    std::vector<Bytes> packets;
    if (!native([&] {
        Apartment apartment;
        Runtime runtime;
        ComPtr<IMFTransform> encoder;
        check(CoCreateInstance(CLSID_CMSH264EncoderMFT, nullptr, CLSCTX_INPROC_SERVER,
            IID_PPV_ARGS(&encoder)), "probe H.264 encoder");
        ComPtr<ICodecAPI> codec;
        check(encoder.As(&codec), "probe encoder codec API");
        VARIANT low;
        VariantInit(&low);
        low.vt = VT_BOOL;
        low.boolVal = VARIANT_TRUE;
        check(codec->SetValue(&CODECAPI_AVLowLatencyMode, &low), "probe low latency encoding");
        low.vt = VT_UI4;
        low.ulVal = 0;
        check(codec->SetValue(&CODECAPI_AVEncMPVDefaultBPictureCount, &low), "probe no B pictures");
        auto output = video_type(MFVideoFormat_H264, w, h);
        output->SetUINT32(MF_MT_AVG_BITRATE, 12000000);
        output->SetUINT32(MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_Main);
        check(encoder->SetOutputType(0, output.Get(), 0), "probe encoder output");
        auto input = video_type(MFVideoFormat_NV12, w, h);
        check(encoder->SetInputType(0, input.Get(), 0), "probe encoder input");
        begin_transform(encoder.Get());
        auto receive = [&] {
            while (true) {
                HRESULT hr;
                auto sample = output_sample(encoder.Get(), hr);
                if (hr == MF_E_TRANSFORM_NEED_MORE_INPUT) { return; }
                check(hr, "probe encode output");
                if (sample) { packets.push_back(probe_bytes(sample.Get())); }
            }
        };
        Bytes pixels(static_cast<size_t>(w) * h * 3 / 2);
        const uint8_t colors[4][3] = {{63, 102, 240}, {173, 42, 26}, {32, 240, 118}, {235, 128, 128}};
        for (unsigned y = 0; y < h; y++) {
            for (unsigned x = 0; x < w; x++) {
                pixels[y * w + x] = colors[(y >= h / 2) * 2 + (x >= w / 2)][0];
            }
        }
        for (unsigned y = 0; y < h / 2; y++) {
            for (unsigned x = 0; x < w; x += 2) {
                unsigned color = (y >= h / 4) * 2 + (x >= w / 2);
                pixels[w * h + y * w + x] = colors[color][1];
                pixels[w * h + y * w + x + 1] = colors[color][2];
            }
        }
        for (unsigned i = 0; i < count; i++) {
            VARIANT key;
            VariantInit(&key);
            key.vt = VT_UI4;
            key.ulVal = 1;
            if (all_idr || i == 0) { check(codec->SetValue(&CODECAPI_AVEncVideoForceKeyFrame, &key), "probe force IDR"); }
            auto sample = make_sample(pixels.data(), pixels.size(), i * 33333LL, 33333);
            HRESULT hr = encoder->ProcessInput(0, sample.Get(), 0);
            if (hr == MF_E_NOTACCEPTING) { receive(); hr = encoder->ProcessInput(0, sample.Get(), 0); }
            check(hr, "probe encode input");
            receive();
        }
        check(encoder->ProcessMessage(MFT_MESSAGE_COMMAND_DRAIN, 0), "probe drain");
        receive();
        check(encoder->GetOutputCurrentType(0, &output), "probe final format");
        UINT32 size;
        check(output->GetBlobSize(MF_MT_MPEG_SEQUENCE_HEADER, &size), "probe SPS/PPS size");
        header.resize(size);
        check(output->GetBlob(MF_MT_MPEG_SEQUENCE_HEADER, header.data(), size, nullptr), "probe SPS/PPS");
    })) { return nullptr; }
    PyObject* frames = PyList_New(0);
    for (const auto& packet : packets) {
        PyObject* value = PyBytes_FromStringAndSize(reinterpret_cast<const char*>(packet.data()), packet.size());
        PyList_Append(frames, value);
        Py_DECREF(value);
    }
    return Py_BuildValue("y#N", header.data(), static_cast<Py_ssize_t>(header.size()), frames);
}
static PyObject* test_make_aac(PyObject* self, PyObject* args) {
    (void)self;
    unsigned count = 24;
    if (!PyArg_ParseTuple(args, "|I", &count)) { return nullptr; }
    Bytes config;
    std::vector<Bytes> packets;
    if (!native([&] {
        Apartment apartment;
        Runtime runtime;
        ComPtr<IMFTransform> encoder;
        check(CoCreateInstance(CLSID_AACMFTEncoder, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&encoder)), "probe AAC encoder");
        ComPtr<IMFMediaType> input;
        MFCreateMediaType(&input);
        input->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Audio);
        input->SetGUID(MF_MT_SUBTYPE, MFAudioFormat_PCM);
        input->SetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, 16);
        input->SetUINT32(MF_MT_AUDIO_NUM_CHANNELS, 2);
        input->SetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, 48000);
        check(encoder->SetInputType(0, input.Get(), 0), "probe AAC input");
        ComPtr<IMFMediaType> output;
        MFCreateMediaType(&output);
        input->CopyAllItems(output.Get());
        output->SetGUID(MF_MT_SUBTYPE, MFAudioFormat_AAC);
        output->SetUINT32(MF_MT_AUDIO_AVG_BYTES_PER_SECOND, 16000);
        output->SetUINT32(MF_MT_AUDIO_BLOCK_ALIGNMENT, 1);
        output->SetUINT32(MF_MT_AAC_PAYLOAD_TYPE, 0);
        check(encoder->SetOutputType(0, output.Get(), 0), "probe AAC output");
        begin_transform(encoder.Get());
        auto receive = [&] {
            while (true) {
                HRESULT hr;
                auto sample = output_sample(encoder.Get(), hr);
                if (hr == MF_E_TRANSFORM_NEED_MORE_INPUT) { return; }
                check(hr, "probe AAC encode");
                if (sample) { packets.push_back(probe_bytes(sample.Get())); }
            }
        };
        std::vector<int16_t> pcm(2048);
        for (unsigned i = 0; i < count; i++) {
            for (unsigned j = 0; j < 1024; j++) {
                int16_t value = static_cast<int16_t>(12000 * sin((i * 1024 + j) * 440.0 * 6.283185307 / 48000));
                pcm[j * 2] = value;
                pcm[j * 2 + 1] = value;
            }
            auto sample = make_sample(reinterpret_cast<const uint8_t*>(pcm.data()), pcm.size() * 2,
                i * 1024000000LL / 48000, 21333);
            check(encoder->ProcessInput(0, sample.Get(), 0), "probe AAC PCM");
            receive();
        }
        check(encoder->ProcessMessage(MFT_MESSAGE_COMMAND_DRAIN, 0), "probe drain AAC");
        receive();
        encoder->GetOutputCurrentType(0, &output);
        UINT size;
        check(output->GetBlobSize(MF_MT_USER_DATA, &size), "probe AAC config size");
        Bytes info(size);
        check(output->GetBlob(MF_MT_USER_DATA, info.data(), size, nullptr), "probe AAC config");
        if (size < 14) { throw Failure("probe AAC config lacks ASC"); }
        config.assign(info.begin() + 12, info.end());
    })) { return nullptr; }
    PyObject* result = PyList_New(0);
    for (const auto& packet : packets) {
        PyObject* value = PyBytes_FromStringAndSize(reinterpret_cast<const char*>(packet.data()), packet.size());
        PyList_Append(result, value);
        Py_DECREF(value);
    }
    return Py_BuildValue("y#N", config.data(), static_cast<Py_ssize_t>(config.size()), result);
}
static PyObject* test_read_jpeg(PyObject* self, PyObject* args) {
    (void)self;
    const char* data;
    Py_ssize_t size;
    if (!PyArg_ParseTuple(args, "y#", &data, &size)) { return nullptr; }
    UINT w = 0, h = 0;
    Bytes pixels;
    if (!native([&] {
        Apartment apartment;
        ComPtr<IWICImagingFactory> factory;
        check(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory)), "probe WIC");
        ComPtr<IWICStream> stream;
        check(factory->CreateStream(&stream), "probe WIC stream");
        check(stream->InitializeFromMemory(reinterpret_cast<BYTE*>(const_cast<char*>(data)), static_cast<DWORD>(size)), "probe JPEG memory");
        ComPtr<IWICBitmapDecoder> decoder;
        check(factory->CreateDecoderFromStream(stream.Get(), nullptr, WICDecodeMetadataCacheOnLoad, &decoder), "probe JPEG decode");
        ComPtr<IWICBitmapFrameDecode> frame;
        check(decoder->GetFrame(0, &frame), "probe JPEG frame");
        check(frame->GetSize(&w, &h), "probe JPEG size");
        ComPtr<IWICFormatConverter> converter;
        check(factory->CreateFormatConverter(&converter), "probe converter");
        check(converter->Initialize(frame.Get(), GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone,
            nullptr, 0, WICBitmapPaletteTypeCustom), "probe BGRA conversion");
        pixels.resize(static_cast<size_t>(w) * h * 4);
        check(converter->CopyPixels(nullptr, w * 4, static_cast<UINT>(pixels.size()), pixels.data()), "probe JPEG pixels");
    })) { return nullptr; }
    return Py_BuildValue("IIy#", w, h, pixels.data(), static_cast<Py_ssize_t>(pixels.size()));
}
static PyObject* test_read_mp4(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* path;
    int audio = 0;
    if (!PyArg_ParseTuple(args, "U|p", &path, &audio)) { return nullptr; }
    wchar_t* name = PyUnicode_AsWideCharString(path, nullptr);
    if (!name) { return nullptr; }
    std::wstring filename(name);
    PyMem_Free(name);
    struct Packet { int64_t pts, duration; Bytes bytes; };
    std::vector<Packet> packets;
    if (!native([&] {
        Apartment apartment;
        Runtime runtime;
        ComPtr<IMFSourceReader> reader;
        check(MFCreateSourceReaderFromURL(filename.c_str(), nullptr, &reader), "probe read MP4");
        DWORD stream = audio ? MF_SOURCE_READER_FIRST_AUDIO_STREAM : MF_SOURCE_READER_FIRST_VIDEO_STREAM;
        check(reader->SetStreamSelection(MF_SOURCE_READER_ALL_STREAMS, FALSE), "probe deselect streams");
        check(reader->SetStreamSelection(stream, TRUE), "probe select stream");
        while (true) {
            DWORD actual, flags;
            LONGLONG time;
            ComPtr<IMFSample> sample;
            check(reader->ReadSample(stream, 0, &actual, &flags, &time, &sample), "probe read packet");
            if (flags & MF_SOURCE_READERF_ENDOFSTREAM) { break; }
            if (sample) {
                LONGLONG duration = 0;
                sample->GetSampleDuration(&duration);
                packets.push_back({time, duration, probe_bytes(sample.Get())});
            }
        }
    })) { return nullptr; }
    PyObject* result = PyList_New(0);
    for (const auto& packet : packets) {
        PyObject* value = Py_BuildValue("LLy#", packet.pts, packet.duration, packet.bytes.data(), static_cast<Py_ssize_t>(packet.bytes.size()));
        PyList_Append(result, value);
        Py_DECREF(value);
    }
    return result;
}
static PyObject* test_decode_mp4_summary(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* path;
    int audio = 0;
    if (!PyArg_ParseTuple(args, "U|p", &path, &audio)) { return nullptr; }
    wchar_t* name = PyUnicode_AsWideCharString(path, nullptr);
    if (!name) { return nullptr; }
    std::wstring filename(name);
    PyMem_Free(name);
    uint64_t count = 0, bytes = 0;
    int64_t first = 0, last = 0, end = 0;
    unsigned peak = 0;
    if (!native([&] {
        Apartment apartment;
        Runtime runtime;
        ComPtr<IMFSourceReader> reader;
        check(MFCreateSourceReaderFromURL(filename.c_str(), nullptr, &reader), "probe decode MP4");
        DWORD stream = audio ? MF_SOURCE_READER_FIRST_AUDIO_STREAM : MF_SOURCE_READER_FIRST_VIDEO_STREAM;
        check(reader->SetStreamSelection(MF_SOURCE_READER_ALL_STREAMS, FALSE), "probe deselect streams");
        check(reader->SetStreamSelection(stream, TRUE), "probe select stream");
        ComPtr<IMFMediaType> type;
        check(MFCreateMediaType(&type), "probe decoded media type");
        type->SetGUID(MF_MT_MAJOR_TYPE, audio ? MFMediaType_Audio : MFMediaType_Video);
        type->SetGUID(MF_MT_SUBTYPE, audio ? MFAudioFormat_PCM : MFVideoFormat_NV12);
        if (audio) { type->SetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, 16); }
        check(reader->SetCurrentMediaType(stream, nullptr, type.Get()), "probe enable system decoder");
        while (true) {
            DWORD actual, flags;
            LONGLONG time;
            ComPtr<IMFSample> sample;
            check(reader->ReadSample(stream, 0, &actual, &flags, &time, &sample), "probe decode sample");
            if (sample) {
                if (count && time < last) { throw Failure("probe decoded timestamps went backwards"); }
                auto data = probe_bytes(sample.Get());
                if (data.empty()) { throw Failure("probe empty decoded sample"); }
                if (audio) {
                    for (size_t i = 0; i + 1 < data.size(); i += 2) {
                        int value = static_cast<int16_t>(data[i] | (data[i + 1] << 8));
                        peak = std::max(peak, static_cast<unsigned>(value < 0 ? -value : value));
                    }
                }
                LONGLONG duration = 0;
                sample->GetSampleDuration(&duration);
                if (!count) { first = time; }
                last = time;
                end = std::max(end, static_cast<int64_t>(time + duration));
                bytes += data.size();
                ++count;
            }
            if (flags & MF_SOURCE_READERF_ENDOFSTREAM) { break; }
        }
    })) { return nullptr; }
    return Py_BuildValue("{s:K,s:K,s:L,s:L,s:L,s:I}", "samples", count, "bytes", bytes,
        "first_pts_100ns", first, "last_pts_100ns", last, "end_100ns", end, "pcm_peak", peak);
}
static PyObject* test_delay_decoder_output(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    unsigned polls;
    int expired = 0;
    if (!PyArg_ParseTuple(args, "OI|p", &capsule, &polls, &expired)) { return nullptr; }
    auto handle = decoder_handle(capsule);
    if (!handle || !native([&] {
        std::lock_guard<std::mutex> lock(handle->mutex);
        handle->decoder->delay_output(polls, expired != 0);
    })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* test_block(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    unsigned long long entered, release;
    int recording = 0;
    if (!PyArg_ParseTuple(args, "OKK|p", &capsule, &entered, &release, &recording)) { return nullptr; }
    if (!native([&] {
        if (recording) {
            // Capsule pointer is read before detaching in real entry points.
            throw Failure("use test_block_recording");
        }
    })) { return nullptr; }
    auto handle = decoder_handle(capsule);
    if (!handle || !native([&] {
        std::lock_guard<std::mutex> lock(handle->mutex);
        handle->decoder->block(reinterpret_cast<HANDLE>(entered), reinterpret_cast<HANDLE>(release));
    })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* test_block_recording(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    unsigned long long entered, release;
    if (!PyArg_ParseTuple(args, "OKK", &capsule, &entered, &release)) { return nullptr; }
    auto recorder = recording_handle(capsule);
    if (!recorder || !native([&] { (*recorder)->block(reinterpret_cast<HANDLE>(entered), reinterpret_cast<HANDLE>(release)); })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* test_wait_recording(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    if (!PyArg_ParseTuple(args, "O", &capsule)) { return nullptr; }
    auto recorder = recording_handle(capsule);
    if (!recorder || !native([&] { (*recorder)->wait(); })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* test_stall_sink_statistics(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    int expired = 0;
    if (!PyArg_ParseTuple(args, "O|p", &capsule, &expired)) { return nullptr; }
    auto recorder = recording_handle(capsule);
    if (!recorder || !native([&] { (*recorder)->stall_sink_statistics(expired != 0); })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* test_copy_rows(PyObject* self, PyObject* args) {
    (void)self;
    unsigned w, h;
    int bottom_up = 0;
    if (!PyArg_ParseTuple(args, "II|p", &w, &h, &bottom_up)) { return nullptr; }
    Bytes packed(static_cast<size_t>(w) * h * 4);
    LONG stride = 0;
    if (!native([&] {
        Apartment apartment;
        Runtime runtime;
        ComPtr<IMFMediaBuffer> buffer;
        check(MFCreate2DMediaBuffer(w, h, MFVideoFormat_ARGB32.Data1, bottom_up, &buffer), "probe padded buffer");
        ComPtr<IMF2DBuffer> image;
        check(buffer.As(&image), "probe 2D buffer");
        BYTE* row;
        check(image->Lock2D(&row, &stride), "probe lock padded image");
        for (unsigned y = 0; y < h; y++) {
            for (unsigned x = 0; x < w; x++) {
                row[x * 4] = static_cast<uint8_t>(x);
                row[x * 4 + 1] = static_cast<uint8_t>(y);
                row[x * 4 + 2] = 31;
                row[x * 4 + 3] = 0;
            }
            row += stride;
        }
        image->Unlock2D();
        ComPtr<IMFSample> sample;
        check(MFCreateSample(&sample), "probe row sample");
        sample->AddBuffer(buffer.Get());
        auto type = video_type(MFVideoFormat_ARGB32, w, h);
        copy_bgra(sample.Get(), type.Get(), w, h, packed.data());
    })) { return nullptr; }
    return Py_BuildValue("ly#", stride, packed.data(), static_cast<Py_ssize_t>(packed.size()));
}
#define WINDOWS_PROBE_METHODS \
    {"test_delay_decoder_output", test_delay_decoder_output, METH_VARARGS, NULL}, \
    {"test_make_aac", test_make_aac, METH_VARARGS, NULL}, \
    {"test_make_h264", test_make_h264, METH_VARARGS, NULL}, \
    {"test_read_jpeg", test_read_jpeg, METH_VARARGS, NULL}, \
    {"test_read_mp4", test_read_mp4, METH_VARARGS, NULL}, \
    {"test_decode_mp4_summary", test_decode_mp4_summary, METH_VARARGS, NULL}, \
    {"test_block", test_block, METH_VARARGS, NULL}, \
    {"test_block_recording", test_block_recording, METH_VARARGS, NULL}, \
    {"test_wait_recording", test_wait_recording, METH_VARARGS, NULL}, \
    {"test_stall_sink_statistics", test_stall_sink_statistics, METH_VARARGS, NULL}, \
    {"test_copy_rows", test_copy_rows, METH_VARARGS, NULL},
