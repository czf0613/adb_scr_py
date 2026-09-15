#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <cmath>
#include "media.h"

using namespace media;
namespace {
constexpr const char* decoder_name = "_adb_scr_media.DecoderHandle";
constexpr const char* recorder_name = "_adb_scr_media.RecordingHandle";
struct DecoderHandle {
    std::mutex mutex;
    std::unique_ptr<Decoder> decoder;
    std::exception_ptr failure;
};
using RecordingHandle = std::shared_ptr<Recorder>;

void python_error(std::exception_ptr error) {
    try {
        std::rethrow_exception(error);
    } catch (const Failure& failure) {
        if (failure.overloaded) {
            PyObject* module = PyImport_ImportModule("adb_scr.exceptions");
            if (!module) {
                return;
            }
            PyObject* type = PyObject_GetAttrString(module, "MediaPipelineOverloadedError");
            Py_DECREF(module);
            if (!type) {
                return;
            }
            PyErr_SetString(type, failure.what());
            Py_DECREF(type);
        } else {
            PyErr_SetString(PyExc_RuntimeError, failure.what());
        }
    } catch (const std::invalid_argument& failure) {
        PyErr_SetString(PyExc_ValueError, failure.what());
    } catch (const std::bad_alloc&) {
        PyErr_NoMemory();
    } catch (const std::exception& failure) {
        PyErr_SetString(PyExc_RuntimeError, failure.what());
    }
}
template<class F> bool native(F&& work) {
    std::exception_ptr error;
    PyThreadState* state = PyEval_SaveThread();
    try {
        work();
    } catch (...) {
        error = std::current_exception();
    }
    PyEval_RestoreThread(state);
    if (error) {
        python_error(error);
        return false;
    }
    return true;
}
DecoderHandle* decoder_handle(PyObject* value) {
    return static_cast<DecoderHandle*>(PyCapsule_GetPointer(value, decoder_name));
}
RecordingHandle* recording_handle(PyObject* value) {
    return static_cast<RecordingHandle*>(PyCapsule_GetPointer(value, recorder_name));
}
void destroy_handle(PyObject* capsule) {
    auto handle = decoder_handle(capsule);
    if (!handle) {
        PyErr_Clear();
        return;
    }
    PyThreadState* state = PyEval_SaveThread();
    delete handle;
    PyEval_RestoreThread(state);
}
void destroy_recording_handle(PyObject* capsule) {
    auto handle = recording_handle(capsule);
    if (!handle) {
        PyErr_Clear();
        return;
    }
    PyThreadState* state = PyEval_SaveThread();
    try { (*handle)->stop(INT64_MIN); } catch (...) {}
    delete handle;
    PyEval_RestoreThread(state);
}
FramePtr snapshot(DecoderHandle* handle) {
    std::lock_guard<std::mutex> lock(handle->mutex);
    if (handle->failure) {
        std::rethrow_exception(handle->failure);
    }
    return handle->decoder ? handle->decoder->snapshot() : nullptr;
}
bool parse_options(PyObject* quality, PyObject* scale, PyObject* roi, ImageOptions& options) {
    if (quality) {
        if (!PyLong_Check(quality) || PyBool_Check(quality)) {
            PyErr_SetString(PyExc_TypeError, "quality must be an integer");
            return false;
        }
        long value = PyLong_AsLong(quality);
        if (PyErr_Occurred()) {
            return false;
        }
        if (value < 1 || value > 100) {
            PyErr_SetString(PyExc_ValueError, "quality must be between 1 and 100");
            return false;
        }
        options.quality = static_cast<int>(value);
    }
    if (scale) {
        if ((!PyFloat_Check(scale) && !PyLong_Check(scale)) || PyBool_Check(scale)) {
            PyErr_SetString(PyExc_TypeError, "scale must be a number");
            return false;
        }
        options.scale = PyFloat_AsDouble(scale);
        if (PyErr_Occurred()) {
            return false;
        }
        if (!std::isfinite(options.scale) || options.scale <= 0) {
            PyErr_SetString(PyExc_ValueError, "scale must be finite and positive");
            return false;
        }
    }
    if (roi != Py_None) {
        if (!PyTuple_Check(roi) || PyTuple_Size(roi) != 4) {
            PyErr_SetString(PyExc_TypeError, "roi must be an (x, y, width, height) tuple");
            return false;
        }
        int64_t* values[] = {&options.x, &options.y, &options.width, &options.height};
        for (int i = 0; i < 4; i++) {
            PyObject* value = PyTuple_GetItem(roi, i);
            if (!PyLong_Check(value) || PyBool_Check(value)) {
                PyErr_SetString(PyExc_TypeError, "roi values must be integers");
                return false;
            }
            *values[i] = PyLong_AsLongLong(value);
            if (PyErr_Occurred()) {
                return false;
            }
        }
        if (options.x < 0 || options.y < 0 || options.width <= 0 || options.height <= 0) {
            PyErr_SetString(PyExc_ValueError, "roi requires nonnegative coordinates and positive size");
            return false;
        }
        options.roi = true;
    }
    return true;
}
}

static PyObject* create_decoder(PyObject* self, PyObject* args) {
    (void)self;
    const char* data;
    Py_ssize_t size;
    if (!PyArg_ParseTuple(args, "y#", &data, &size)) {
        return nullptr;
    }
    std::unique_ptr<DecoderHandle> handle;
    if (!native([&] {
        handle = std::make_unique<DecoderHandle>();
        handle->decoder = std::make_unique<Decoder>(Bytes(data, data + size));
    })) {
        return nullptr;
    }
    auto width = handle->decoder->width, height = handle->decoder->height;
    PyObject* capsule = PyCapsule_New(handle.release(), decoder_name, destroy_handle);
    return Py_BuildValue("IIN", width, height, capsule);
}
static PyObject* destroy_decoder(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    if (!PyArg_ParseTuple(args, "O", &capsule)) {
        return nullptr;
    }
    auto handle = decoder_handle(capsule);
    if (!handle || !native([&] {
        std::lock_guard<std::mutex> lock(handle->mutex);
        if (handle->decoder) {
            try { handle->decoder->check_error(); } catch (...) { handle->failure = std::current_exception(); }
            handle->decoder.reset();
        }
    })) {
        return nullptr;
    }
    Py_RETURN_NONE;
}
static PyObject* enqueue_frame(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    const char* data;
    Py_ssize_t size;
    long long pts;
    if (!PyArg_ParseTuple(args, "Oy#L", &capsule, &data, &size, &pts)) {
        return nullptr;
    }
    auto handle = decoder_handle(capsule);
    bool accepted = false;
    if (!handle || !native([&] {
        std::lock_guard<std::mutex> lock(handle->mutex);
        if (handle->failure) { std::rethrow_exception(handle->failure); }
        if (handle->decoder) { accepted = handle->decoder->enqueue(reinterpret_cast<const uint8_t*>(data), size, pts); }
    })) {
        return nullptr;
    }
    return PyBool_FromLong(accepted);
}
static PyObject* check_decoder_error(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    if (!PyArg_ParseTuple(args, "O", &capsule)) { return nullptr; }
    auto handle = decoder_handle(capsule);
    if (!handle || !native([&] {
        std::lock_guard<std::mutex> lock(handle->mutex);
        if (handle->failure) { std::rethrow_exception(handle->failure); }
        if (handle->decoder) { handle->decoder->check_error(); }
    })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* get_current_frame_bgra8(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    if (!PyArg_ParseTuple(args, "O", &capsule)) { return nullptr; }
    auto handle = decoder_handle(capsule);
    FramePtr frame;
    if (!handle || !native([&] { frame = snapshot(handle); })) { return nullptr; }
    if (!frame) { Py_RETURN_NONE; }
    size_t size = static_cast<size_t>(frame->width) * frame->height * 4;
    PyObject* pixels = PyBytes_FromStringAndSize(nullptr, static_cast<Py_ssize_t>(size));
    if (!pixels) { return nullptr; }
    auto target = reinterpret_cast<uint8_t*>(PyBytes_AS_STRING(pixels));
    if (!native([&] {
        Apartment apartment;
        Runtime runtime;
        auto converted = convert_frame(frame, MFVideoFormat_ARGB32, frame->width, frame->height);
        auto type = video_type(MFVideoFormat_ARGB32, frame->width, frame->height);
        copy_bgra(converted.Get(), type.Get(), frame->width, frame->height, target);
    })) {
        Py_DECREF(pixels);
        return nullptr;
    }
    return Py_BuildValue("IIN", frame->width, frame->height, pixels);
}
static PyObject* get_current_frame_jpg(PyObject* self, PyObject* args) {
    (void)self;
    PyObject *capsule, *quality = nullptr, *scale = nullptr, *roi = Py_None;
    if (!PyArg_ParseTuple(args, "O|OOO", &capsule, &quality, &scale, &roi)) { return nullptr; }
    ImageOptions options;
    if (!parse_options(quality, scale, roi, options)) { return nullptr; }
    auto handle = decoder_handle(capsule);
    FramePtr frame;
    Bytes result;
    if (!handle || !native([&] {
        frame = snapshot(handle);
        if (frame) {
            Apartment apartment;
            Runtime runtime;
            auto converted = convert_frame(frame, MFVideoFormat_ARGB32, frame->width, frame->height);
            auto type = video_type(MFVideoFormat_ARGB32, frame->width, frame->height);
            Bytes pixels(static_cast<size_t>(frame->width) * frame->height * 4);
            copy_bgra(converted.Get(), type.Get(), frame->width, frame->height, pixels.data());
            result = jpeg(frame->width, frame->height, pixels.data(), options);
        }
    })) { return nullptr; }
    if (!frame) { Py_RETURN_NONE; }
    return PyBytes_FromStringAndSize(reinterpret_cast<const char*>(result.data()), result.size());
}
static PyObject* bgra8_to_jpg(PyObject* self, PyObject* args) {
    (void)self;
    int w, h, quality;
    const char* data;
    Py_ssize_t size;
    if (!PyArg_ParseTuple(args, "iiy#i", &w, &h, &data, &size, &quality)) { return nullptr; }
    if (w <= 0 || h <= 0 || static_cast<uint64_t>(w) * h * 4 > static_cast<uint64_t>(size)) { Py_RETURN_NONE; }
    ImageOptions options;
    options.quality = std::clamp(quality, 1, 100);
    Bytes result;
    if (!native([&] {
        Apartment apartment;
        result = jpeg(w, h, reinterpret_cast<const uint8_t*>(data), options);
    })) { return nullptr; }
    return PyBytes_FromStringAndSize(reinterpret_cast<const char*>(result.data()), result.size());
}
static PyObject* start_recording(PyObject* self, PyObject* args) {
    (void)self;
    PyObject *capsule, *path, *audio, *quality = nullptr;
    long long pts;
    int fps;
    if (!PyArg_ParseTuple(args, "OUOLi|O", &capsule, &path, &audio, &pts, &fps, &quality)) { return nullptr; }
    double q = 0.75;
    if (quality) {
        if (PyBool_Check(quality) || (!PyFloat_Check(quality) && !PyLong_Check(quality))) {
            PyErr_SetString(PyExc_TypeError, "quality must be int or float, not bool");
            return nullptr;
        }
        q = PyFloat_AsDouble(quality);
        if (PyErr_Occurred()) { return nullptr; }
    }
    if (!std::isfinite(q) || q < 0 || q > 1) {
        PyErr_SetString(PyExc_ValueError, "quality must be finite and between 0 and 1");
        return nullptr;
    }
    const char* data = nullptr;
    Py_ssize_t size = 0;
    if (audio != Py_None && PyBytes_AsStringAndSize(audio, const_cast<char**>(&data), &size) < 0) { return nullptr; }
    Py_ssize_t path_size;
    wchar_t* raw_path = PyUnicode_AsWideCharString(path, &path_size);
    if (!raw_path) { return nullptr; }
    std::wstring filename(raw_path, path_size);
    PyMem_Free(raw_path);
    if (filename.empty() || filename.find(L'\0') != std::wstring::npos) {
        PyErr_SetString(PyExc_ValueError, "output path is empty or contains NUL");
        return nullptr;
    }
    auto handle = decoder_handle(capsule);
    RecordingHandle recorder;
    if (!handle || !native([&] {
        std::lock_guard<std::mutex> lock(handle->mutex);
        if (!handle->decoder) { throw Failure("decoder is closed"); }
        auto frame = handle->decoder->snapshot();
        if (!frame) { throw Failure("no decoded frame available for recording"); }
        Bytes config;
        if (size) { config.assign(data, data + size); }
        recorder = std::make_shared<Recorder>(frame, filename, config, pts, fps, q);
        handle->decoder->attach(recorder);
    })) { return nullptr; }
    return PyCapsule_New(new RecordingHandle(std::move(recorder)), recorder_name, destroy_recording_handle);
}
static PyObject* set_decoder_recording(PyObject* self, PyObject* args) {
    (void)self;
    PyObject *capsule, *value;
    if (!PyArg_ParseTuple(args, "OO", &capsule, &value)) { return nullptr; }
    auto handle = decoder_handle(capsule);
    auto recorder = value == Py_None ? nullptr : recording_handle(value);
    if (!handle || (value != Py_None && !recorder)) { return nullptr; }
    if (!native([&] {
        std::lock_guard<std::mutex> lock(handle->mutex);
        if (handle->decoder) { handle->decoder->attach(recorder ? *recorder : nullptr); }
        else if (recorder) { throw Failure("cannot attach recording to closed decoder"); }
    })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* append_recording_audio(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    const char* data;
    Py_ssize_t size;
    long long pts;
    if (!PyArg_ParseTuple(args, "Oy#L", &capsule, &data, &size, &pts)) { return nullptr; }
    auto recorder = recording_handle(capsule);
    if (!recorder || !native([&] { (*recorder)->audio(reinterpret_cast<const uint8_t*>(data), size, pts); })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* stop_recording(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    long long pts;
    if (!PyArg_ParseTuple(args, "OL", &capsule, &pts)) { return nullptr; }
    auto recorder = recording_handle(capsule);
    if (!recorder || !native([&] { (*recorder)->stop(pts); })) { return nullptr; }
    Py_RETURN_NONE;
}
static PyObject* check_recording_error(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* capsule;
    if (!PyArg_ParseTuple(args, "O", &capsule)) { return nullptr; }
    auto recorder = recording_handle(capsule);
    if (!recorder || !native([&] { (*recorder)->check_error(); })) { return nullptr; }
    Py_RETURN_NONE;
}

#ifdef ADB_SCR_TESTING
#include "windows_probe.h"
#endif
static PyMethodDef methods[] = {
    {"create_decoder", create_decoder, METH_VARARGS, NULL},
    {"destroy_decoder", destroy_decoder, METH_VARARGS, NULL},
    {"enqueue_frame", enqueue_frame, METH_VARARGS, NULL},
    {"get_current_frame_bgra8", get_current_frame_bgra8, METH_VARARGS, NULL},
    {"get_current_frame_jpg", get_current_frame_jpg, METH_VARARGS, NULL},
    {"bgra8_to_jpg", bgra8_to_jpg, METH_VARARGS, NULL},
    {"start_recording", start_recording, METH_VARARGS, NULL},
    {"set_decoder_recording", set_decoder_recording, METH_VARARGS, NULL},
    {"append_recording_audio", append_recording_audio, METH_VARARGS, NULL},
    {"stop_recording", stop_recording, METH_VARARGS, NULL},
    {"check_decoder_error", check_decoder_error, METH_VARARGS, NULL},
    {"check_recording_error", check_recording_error, METH_VARARGS, NULL},
#ifdef ADB_SCR_TESTING
    WINDOWS_PROBE_METHODS
#endif
    {NULL, NULL, 0, NULL}
};
static PyModuleDef module = {PyModuleDef_HEAD_INIT, "_adb_scr_media", NULL, -1, methods};
PyMODINIT_FUNC PyInit__adb_scr_media(void) {
    PyObject* result = PyModule_Create(&module);
#ifdef Py_GIL_DISABLED
    if (result) { PyUnstable_Module_SetGIL(result, Py_MOD_GIL_NOT_USED); }
#endif
    return result;
}
