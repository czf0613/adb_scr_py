#include "media.h"
#include <shlwapi.h>

namespace media {
RECT fitted_rect(uint32_t source_w, uint32_t source_h, uint32_t w, uint32_t h) {
    double scale = std::min(static_cast<double>(w) / source_w, static_cast<double>(h) / source_h);
    LONG fw = std::max<LONG>(2, static_cast<LONG>(source_w * scale) & ~1);
    LONG fh = std::max<LONG>(2, static_cast<LONG>(source_h * scale) & ~1);
    LONG x = (static_cast<LONG>(w) - fw) / 2 & ~1;
    LONG y = (static_cast<LONG>(h) - fh) / 2 & ~1;
    return {x, y, x + fw, y + fh};
}
ComPtr<IMFSample> convert_frame(const FramePtr& frame, REFGUID format, uint32_t w, uint32_t h, bool fit) {
    ComPtr<IMFTransform> processor;
    check(CoCreateInstance(CLSID_VideoProcessorMFT, nullptr, CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&processor)), "create Windows video processor");
    ComPtr<IMFAttributes> attributes;
    check(processor->GetAttributes(&attributes), "video processor attributes");
    check(attributes->SetUINT32(MF_XVP_DISABLE_FRC, TRUE), "disable frame rate conversion");
    if (frame->device && frame->device->manager) {
        check(processor->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER,
            reinterpret_cast<ULONG_PTR>(frame->device->manager.Get())), "processor D3D manager");
    }
    check(processor->SetInputType(0, frame->type.Get(), 0), "processor input");
    auto output = video_type(format, w, h);
    check(processor->SetOutputType(0, output.Get(), 0), "processor output");
    if (fit) {
        ComPtr<IMFVideoProcessorControl> control;
        check(processor.As(&control), "video fit control");
        RECT destination = fitted_rect(frame->width, frame->height, w, h);
        MFARGB black{0, 0, 0, 255};
        check(control->SetBorderColor(&black), "video border color");
        check(control->SetDestinationRectangle(&destination), "video destination rectangle");
    }
    begin_transform(processor.Get());
    check(processor->ProcessInput(0, frame->sample.Get(), 0), "processor input frame");
    HRESULT hr;
    auto sample = output_sample(processor.Get(), hr);
    check(hr, "process video frame");
    if (!sample) {
        throw Failure("video processor returned no frame");
    }
    processor->ProcessMessage(MFT_MESSAGE_COMMAND_FLUSH, 0);
    processor->ProcessMessage(MFT_MESSAGE_NOTIFY_END_STREAMING, 0);
    return sample;
}

void copy_bgra(IMFSample* sample, IMFMediaType* type, uint32_t w, uint32_t h, uint8_t* target) {
    ComPtr<IMFMediaBuffer> buffer;
    check(sample->ConvertToContiguousBuffer(&buffer), "get BGRA buffer");
    ComPtr<IMF2DBuffer2> two_d;
    HRESULT hr = buffer.As(&two_d);
    if (SUCCEEDED(hr)) {
        BYTE *first, *start;
        LONG stride;
        DWORD length;
        check(two_d->Lock2DSize(MF2DBuffer_LockFlags_Read, &first, &stride, &start, &length), "lock BGRA surface");
        int64_t offset = first - start;
        int64_t last = offset + static_cast<int64_t>(stride) * (h - 1);
        if (std::abs(static_cast<int64_t>(stride)) < w * 4 || std::min(offset, last) < 0 ||
            std::max(offset, last) + w * 4 > length) {
            two_d->Unlock2D();
            throw Failure("BGRA surface is shorter than its image stride");
        }
        hr = MFCopyImage(target, w * 4, first, stride, w * 4, h);
        two_d->Unlock2D();
        check(hr, "pack BGRA rows");
    } else {
        BYTE* first;
        DWORD length;
        check(buffer->Lock(&first, nullptr, &length), "lock BGRA buffer");
        UINT32 raw_stride = w * 4;
        type->GetUINT32(MF_MT_DEFAULT_STRIDE, &raw_stride);
        LONG stride = static_cast<LONG>(raw_stride);
        uint64_t span = static_cast<uint64_t>(std::abs(static_cast<int64_t>(stride))) * (h - 1) + w * 4;
        if (span > length || std::abs(static_cast<int64_t>(stride)) < w * 4) {
            buffer->Unlock();
            throw Failure("BGRA buffer is shorter than its image stride");
        }
        if (stride < 0) {
            first += static_cast<size_t>(-static_cast<int64_t>(stride)) * (h - 1);
        }
        hr = MFCopyImage(target, w * 4, first, stride, w * 4, h);
        buffer->Unlock();
        check(hr, "pack BGRA rows");
    }
    // RGB32's unused byte is not guaranteed to be opaque on every processor.
    for (size_t i = 3, size = static_cast<size_t>(w) * h * 4; i < size; i += 4) {
        target[i] = 255;
    }
}

Bytes jpeg(uint32_t w, uint32_t h, const uint8_t* pixels, const ImageOptions& options) {
    int64_t rw = options.roi ? options.width : w, rh = options.roi ? options.height : h;
    if (options.roi && (options.x >= w || options.y >= h || rw > w - options.x || rh > h - options.y)) {
        throw std::invalid_argument("roi must fit entirely inside the original frame");
    }
    double out_w = std::max(1.0, std::floor(rw * options.scale + 0.5));
    double out_h = std::max(1.0, std::floor(rh * options.scale + 0.5));
    if (!std::isfinite(out_w) || !std::isfinite(out_h) || out_w > 65535 || out_h > 65535) {
        throw std::invalid_argument("scaled JPEG dimensions must not exceed 65535 pixels");
    }
    if (out_w * out_h * 4 > MAXDWORD || static_cast<uint64_t>(w) * h * 4 > MAXDWORD) {
        throw std::invalid_argument("image exceeds the Windows buffer size limit");
    }
    ComPtr<IWICImagingFactory> factory;
    check(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&factory)), "create WIC factory");
    ComPtr<IWICBitmap> bitmap;
    check(factory->CreateBitmapFromMemory(w, h, GUID_WICPixelFormat32bppBGRA, w * 4,
        w * h * 4, const_cast<BYTE*>(pixels), &bitmap), "create WIC bitmap");
    ComPtr<IWICBitmapSource> source = bitmap;
    if (options.roi) {
        ComPtr<IWICBitmapClipper> clipper;
        check(factory->CreateBitmapClipper(&clipper), "create WIC clipper");
        WICRect rect{static_cast<INT>(options.x), static_cast<INT>(options.y), static_cast<INT>(rw), static_cast<INT>(rh)};
        check(clipper->Initialize(source.Get(), &rect), "crop image");
        source = clipper;
    }
    if (out_w != rw || out_h != rh) {
        ComPtr<IWICBitmapScaler> scaler;
        check(factory->CreateBitmapScaler(&scaler), "create WIC scaler");
        check(scaler->Initialize(source.Get(), static_cast<UINT>(out_w), static_cast<UINT>(out_h),
            WICBitmapInterpolationModeFant), "scale image");
        source = scaler;
    }
    ComPtr<IStream> stream;
    check(CreateStreamOnHGlobal(nullptr, TRUE, &stream), "create JPEG stream");
    ComPtr<IWICBitmapEncoder> encoder;
    check(CoCreateInstance(CLSID_WICJpegEncoder, nullptr, CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&encoder)), "create WIC JPEG encoder");
    check(encoder->Initialize(stream.Get(), WICBitmapEncoderNoCache), "initialize JPEG encoder");
    ComPtr<IWICBitmapFrameEncode> frame;
    ComPtr<IPropertyBag2> properties;
    check(encoder->CreateNewFrame(&frame, &properties), "create JPEG frame");
    PROPBAG2 property{};
    property.pstrName = const_cast<LPOLESTR>(L"ImageQuality");
    VARIANT quality;
    VariantInit(&quality);
    quality.vt = VT_R4;
    quality.fltVal = options.quality / 100.0f;
    check(properties->Write(1, &property, &quality), "set JPEG quality");
    check(frame->Initialize(properties.Get()), "initialize JPEG frame");
    check(frame->SetSize(static_cast<UINT>(out_w), static_cast<UINT>(out_h)), "JPEG dimensions");
    WICPixelFormatGUID format = GUID_WICPixelFormat24bppBGR;
    check(frame->SetPixelFormat(&format), "JPEG pixel format");
    ComPtr<IWICFormatConverter> converter;
    check(factory->CreateFormatConverter(&converter), "create WIC converter");
    check(converter->Initialize(source.Get(), format, WICBitmapDitherTypeNone, nullptr, 0,
        WICBitmapPaletteTypeCustom), "convert JPEG pixels");
    check(frame->WriteSource(converter.Get(), nullptr), "encode JPEG");
    check(frame->Commit(), "commit JPEG frame");
    check(encoder->Commit(), "commit JPEG");
    STATSTG stat{};
    check(stream->Stat(&stat, STATFLAG_NONAME), "JPEG length");
    if (stat.cbSize.QuadPart > MAXDWORD) {
        throw Failure("JPEG exceeds stream size limit");
    }
    Bytes result(static_cast<size_t>(stat.cbSize.QuadPart));
    LARGE_INTEGER zero{};
    check(stream->Seek(zero, STREAM_SEEK_SET, nullptr), "rewind JPEG");
    ULONG read = 0;
    check(stream->Read(result.data(), static_cast<ULONG>(result.size()), &read), "read JPEG");
    if (read != result.size()) {
        throw Failure("incomplete JPEG stream");
    }
    return result;
}
}
