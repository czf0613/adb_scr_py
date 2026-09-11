#include "jpg_encoder.h"
#include "frame_jpg_encoder.h"
#include "vtb_decoder.h"
#include "recording.h"
#include <Python.h>
#include <stdlib.h>
#include <pthread.h>
#include <math.h>

typedef struct {
  void *decoder;
  frame_jpg_encoder_t *jpg_encoder;
  pthread_mutex_t mutex;
} decoder_handle_t;

static void release_decoder_handle(PyObject *capsule) {
  decoder_handle_t *handle =
      PyCapsule_GetPointer(capsule, "_adb_scr_media.DecoderHandle");
  Py_BEGIN_ALLOW_THREADS
  vtb_destroy_decoder(&handle->decoder);
  frame_jpg_encoder_destroy(handle->jpg_encoder);
  pthread_mutex_destroy(&handle->mutex);
  free(handle);
  Py_END_ALLOW_THREADS
}

/**
 * @brief 将BGRA8格式的图像数据编码为JPEG格式
 * 实现pyi原型为
 * bgra8_to_jpg(width:int, height:int, bgra8:bytes, quality:int)->bytes|None
 */
static PyObject *bgra8_to_jpg(PyObject *self, PyObject *args) {
  // 处理未使用的参数警告
  (void)self;

  // 解析参数
  int width_param, height_param, quality_param;
  PyBytesObject *bgra8_bytes;
  PyArg_ParseTuple(args, "iiO!i", &width_param, &height_param, &PyBytes_Type,
                   &bgra8_bytes, &quality_param);
  uint32_t width = (uint32_t)width_param, height = (uint32_t)height_param;
  uint8_t quality = (uint8_t)quality_param;

  // quality范围检查
  if (quality > 100) {
    quality = 100;
  } else if (quality < 1) {
    quality = 1;
  }

  // 检查bytes的长度是否符合要求
  char *bgra8_data = NULL;
  Py_ssize_t bgra8_size = 0, expected_size = (Py_ssize_t)(width * height * 4);
  PyBytes_AsStringAndSize((PyObject *)bgra8_bytes, &bgra8_data, &bgra8_size);
  if (bgra8_size < expected_size) {
    Py_RETURN_NONE;
  }

  // 分配JPEG缓冲区
  uint32_t jpg_size = expected_size + 1024;
  uint8_t *jpg_buffer = (uint8_t *)malloc(jpg_size);
  bool success;
  // args keeps the immutable input bytes alive while the thread is detached.
  Py_BEGIN_ALLOW_THREADS
  success = encode_bgra8_to_jpg(width, height, (const uint8_t *)bgra8_data, quality,
                                &jpg_size, jpg_buffer);
  Py_END_ALLOW_THREADS
  if (success) {
    PyObject *result =
        PyBytes_FromStringAndSize((const char *)jpg_buffer, jpg_size);
    free(jpg_buffer);
    return result;
  } else {
    free(jpg_buffer);
    Py_RETURN_NONE;
  }
}

static PyObject *create_decoder(PyObject *self, PyObject *args) {
  (void)self;

  PyBytesObject *sps_and_pps_bytes;
  PyArg_ParseTuple(args, "O!", &PyBytes_Type, &sps_and_pps_bytes);

  char *sps_and_pps_data = NULL;
  Py_ssize_t sps_and_pps_size = 0;
  PyBytes_AsStringAndSize((PyObject *)sps_and_pps_bytes, &sps_and_pps_data,
                          &sps_and_pps_size);

  void *decoder = NULL;
  int32_t width = 0, height = 0;
  int32_t status;
  Py_BEGIN_ALLOW_THREADS
  status = vtb_create_decoder((const uint8_t *)sps_and_pps_data,
                             (size_t)sps_and_pps_size, &decoder, &width, &height);
  Py_END_ALLOW_THREADS
  if (status != 0) {
    Py_RETURN_NONE;
  }

  decoder_handle_t *handle = malloc(sizeof(decoder_handle_t));
  handle->decoder = decoder;
  handle->jpg_encoder = NULL;
  if (pthread_mutex_init(&handle->mutex, NULL) != 0) {
    Py_BEGIN_ALLOW_THREADS
    vtb_destroy_decoder(&handle->decoder);
    Py_END_ALLOW_THREADS
    free(handle);
    Py_RETURN_NONE;
  }
  PyObject *capsule = PyCapsule_New(
      handle, "_adb_scr_media.DecoderHandle", release_decoder_handle);

  PyObject *result = PyTuple_New(3);
  PyTuple_SetItem(result, 0, PyLong_FromLong(width));
  PyTuple_SetItem(result, 1, PyLong_FromLong(height));
  PyTuple_SetItem(result, 2, capsule);
  return result;
}

static PyObject *destroy_decoder(PyObject *self, PyObject *args) {
  (void)self;

  PyObject *capsule;
  PyArg_ParseTuple(args, "O", &capsule);

  decoder_handle_t *handle =
      PyCapsule_GetPointer(capsule, "_adb_scr_media.DecoderHandle");
  if (handle == NULL) {
    return NULL;
  }
  Py_BEGIN_ALLOW_THREADS
  pthread_mutex_lock(&handle->mutex);
  vtb_destroy_decoder(&handle->decoder);
  frame_jpg_encoder_destroy(handle->jpg_encoder);
  handle->jpg_encoder = NULL;
  pthread_mutex_unlock(&handle->mutex);
  Py_END_ALLOW_THREADS

  Py_RETURN_NONE;
}

static PyObject *enqueue_frame(PyObject *self, PyObject *args) {
  (void)self;

  PyObject *capsule;
  PyBytesObject *nalu_bytes;
  int64_t pts;
  PyArg_ParseTuple(args, "OO!L", &capsule, &PyBytes_Type, &nalu_bytes, &pts);

  decoder_handle_t *handle =
      PyCapsule_GetPointer(capsule, "_adb_scr_media.DecoderHandle");
  if (handle == NULL) {
    return NULL;
  }

  char *nalu_data = NULL;
  Py_ssize_t nalu_size = 0;
  PyBytes_AsStringAndSize((PyObject *)nalu_bytes, &nalu_data, &nalu_size);

  bool success;
  Py_BEGIN_ALLOW_THREADS
  pthread_mutex_lock(&handle->mutex);
  success = vtb_enqueue_frame(handle->decoder, (const uint8_t *)nalu_data,
                              (size_t)nalu_size, pts);
  pthread_mutex_unlock(&handle->mutex);
  Py_END_ALLOW_THREADS
  if (success) {
    Py_RETURN_TRUE;
  } else {
    Py_RETURN_FALSE;
  }
}

static PyObject *get_current_frame_bgra8(PyObject *self, PyObject *args) {
  (void)self;

  PyObject *capsule;
  PyArg_ParseTuple(args, "O", &capsule);

  decoder_handle_t *handle =
      PyCapsule_GetPointer(capsule, "_adb_scr_media.DecoderHandle");
  if (handle == NULL) {
    return NULL;
  }

  uint8_t *frame_data = NULL;
  size_t width = 0, height = 0;
  bool success;
  Py_BEGIN_ALLOW_THREADS
  pthread_mutex_lock(&handle->mutex);
  success = vtb_current_frame_bgra8(handle->decoder, &frame_data, &width, &height);
  pthread_mutex_unlock(&handle->mutex);
  Py_END_ALLOW_THREADS
  if (!success) {
    Py_RETURN_NONE;
  }

  PyObject *result = PyTuple_New(3);
  PyTuple_SetItem(result, 0, PyLong_FromSize_t(width));
  PyTuple_SetItem(result, 1, PyLong_FromSize_t(height));
  PyTuple_SetItem(
      result, 2,
      PyBytes_FromStringAndSize((const char *)frame_data, width * height * 4));
  free(frame_data);
  return result;
}

static bool parse_jpg_options(PyObject *quality, PyObject *scale, PyObject *roi,
                              frame_jpg_options_t *options) {
  if (quality != NULL) {
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
    options->quality = (int)value;
  }
  if (scale != NULL) {
    if ((!PyFloat_Check(scale) && !PyLong_Check(scale)) || PyBool_Check(scale)) {
      PyErr_SetString(PyExc_TypeError, "scale must be a number");
      return false;
    }
    options->scale = PyFloat_AsDouble(scale);
    if (PyErr_Occurred()) {
      return false;
    }
    if (!isfinite(options->scale) || options->scale <= 0) {
      PyErr_SetString(PyExc_ValueError, "scale must be finite and positive");
      return false;
    }
  }
  if (roi != Py_None) {
    if (!PyTuple_Check(roi) || PyTuple_Size(roi) != 4) {
      PyErr_SetString(PyExc_TypeError, "roi must be an (x, y, width, height) tuple");
      return false;
    }
    int64_t *values[] = {&options->x, &options->y,
                        &options->width, &options->height};
    for (int i = 0; i < 4; i++) {
      PyObject *value = PyTuple_GetItem(roi, i);
      if (!PyLong_Check(value) || PyBool_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "roi values must be integers");
        return false;
      }
      *values[i] = PyLong_AsLongLong(value);
      if (PyErr_Occurred()) {
        return false;
      }
    }
    if (options->x < 0 || options->y < 0 || options->width <= 0 ||
        options->height <= 0) {
      PyErr_SetString(PyExc_ValueError,
                      "roi requires nonnegative coordinates and positive size");
      return false;
    }
    options->has_roi = true;
  }
  return true;
}

static PyObject *get_current_frame_jpg(PyObject *self, PyObject *args) {
  (void)self;
  PyObject *capsule, *quality = NULL, *scale = NULL, *roi = Py_None;
  if (!PyArg_ParseTuple(args, "O|OOO", &capsule, &quality, &scale, &roi)) {
    return NULL;
  }
  frame_jpg_options_t options = {.quality = 75, .scale = 1.0};
  if (!parse_jpg_options(quality, scale, roi, &options)) {
    return NULL;
  }
  decoder_handle_t *handle =
      PyCapsule_GetPointer(capsule, "_adb_scr_media.DecoderHandle");
  if (handle == NULL) {
    return NULL;
  }

  CFDataRef data = NULL;
  frame_jpg_status_t status = FRAME_JPG_FAILED;
  // Follow the existing handle-operation lock protocol.
  Py_BEGIN_ALLOW_THREADS
  pthread_mutex_lock(&handle->mutex);
  CVPixelBufferRef frame = vtb_copy_current_frame(handle->decoder);
  if (frame != NULL) {
    if (handle->jpg_encoder == NULL) {
      handle->jpg_encoder = frame_jpg_encoder_create();
    }
    status = frame_jpg_encode(handle->jpg_encoder, frame, &options, &data);
    CVPixelBufferRelease(frame);
  }
  pthread_mutex_unlock(&handle->mutex);
  Py_END_ALLOW_THREADS

  if (status == FRAME_JPG_INVALID_ROI || status == FRAME_JPG_INVALID_SIZE) {
    PyErr_SetString(PyExc_ValueError, status == FRAME_JPG_INVALID_ROI
        ? "roi must fit entirely inside the original frame"
        : "scaled JPEG dimensions must not exceed 65535 pixels");
    return NULL;
  }
  if (data == NULL) {
    Py_RETURN_NONE;
  }
  PyObject *result = PyBytes_FromStringAndSize(
      (const char *)CFDataGetBytePtr(data), CFDataGetLength(data));
  CFRelease(data);
  return result;
}

static const vtb_frame_observer_t recording_observer = {
    .retain = recording_retain,
    .release = recording_release,
    .submit = recording_submit_frame};

static void release_recording_handle(PyObject *capsule) {
  recording_t *recording = PyCapsule_GetPointer(capsule,
                                               "_adb_scr_media.RecordingHandle");
  char error[512] = {0};
  Py_BEGIN_ALLOW_THREADS
  recording_stop(recording, INT64_MIN, error);
  recording_release(recording);
  Py_END_ALLOW_THREADS
}

static PyObject *start_recording(PyObject *self, PyObject *args) {
  (void)self;
  PyObject *capsule, *config;
  const char *path;
  long long source_pts;
  int fps;
  if (!PyArg_ParseTuple(args, "OsOLi", &capsule, &path, &config, &source_pts, &fps)) {
    return NULL;
  }
  if (fps < 1 || fps > 240 || source_pts < 0) {
    PyErr_SetString(PyExc_ValueError, "recording requires nonnegative PTS and fps 1..240");
    return NULL;
  }
  decoder_handle_t *handle = PyCapsule_GetPointer(capsule,
                                                 "_adb_scr_media.DecoderHandle");
  if (handle == NULL) {
    return NULL;
  }
  char *config_data = NULL;
  Py_ssize_t config_size = 0;
  if (config != Py_None && PyBytes_AsStringAndSize(config, &config_data, &config_size) < 0) {
    return NULL;
  }
  char error[512] = {0};
  recording_t *recording = NULL;
  Py_BEGIN_ALLOW_THREADS
  pthread_mutex_lock(&handle->mutex);
  CVPixelBufferRef frame = vtb_copy_current_frame(handle->decoder);
  if (frame == NULL) {
    snprintf(error, sizeof(error), "no decoded frame available for recording");
  } else {
    recording = recording_create(frame, path, (const uint8_t *)config_data,
                                  config_size, source_pts, fps, error);
    CVPixelBufferRelease(frame);
    if (recording != NULL) {
      vtb_set_frame_observer(handle->decoder, recording, recording_observer);
    }
  }
  pthread_mutex_unlock(&handle->mutex);
  Py_END_ALLOW_THREADS
  if (recording == NULL) {
    PyErr_SetString(PyExc_RuntimeError, error);
    return NULL;
  }
  return PyCapsule_New(recording, "_adb_scr_media.RecordingHandle",
                        release_recording_handle);
}

static PyObject *set_decoder_recording(PyObject *self, PyObject *args) {
  (void)self;
  PyObject *capsule, *recording_capsule;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &recording_capsule)) {
    return NULL;
  }
  decoder_handle_t *handle = PyCapsule_GetPointer(capsule,
                                                 "_adb_scr_media.DecoderHandle");
  if (handle == NULL) {
    return NULL;
  }
  recording_t *recording = NULL;
  if (recording_capsule != Py_None) {
    recording = PyCapsule_GetPointer(recording_capsule, "_adb_scr_media.RecordingHandle");
    if (recording == NULL) {
      return NULL;
    }
  }
  bool success;
  Py_BEGIN_ALLOW_THREADS
  pthread_mutex_lock(&handle->mutex);
  success = vtb_set_frame_observer(handle->decoder, recording, recording_observer);
  pthread_mutex_unlock(&handle->mutex);
  Py_END_ALLOW_THREADS
  if (!success) {
    PyErr_SetString(PyExc_RuntimeError, "cannot attach recording to closed decoder");
    return NULL;
  }
  Py_RETURN_NONE;
}

static PyObject *append_recording_audio(PyObject *self, PyObject *args) {
  (void)self;
  PyObject *capsule, *packet;
  long long pts;
  if (!PyArg_ParseTuple(args, "OO!L", &capsule, &PyBytes_Type, &packet, &pts)) {
    return NULL;
  }
  recording_t *recording = PyCapsule_GetPointer(capsule,
                                               "_adb_scr_media.RecordingHandle");
  if (recording == NULL) {
    return NULL;
  }
  char *data;
  Py_ssize_t size;
  PyBytes_AsStringAndSize(packet, &data, &size);
  char error[512] = {0};
  bool success;
  Py_BEGIN_ALLOW_THREADS
  success = recording_append_audio(recording, (const uint8_t *)data, size, pts, error);
  Py_END_ALLOW_THREADS
  if (!success) {
    PyErr_SetString(PyExc_RuntimeError, error);
    return NULL;
  }
  Py_RETURN_NONE;
}

static PyObject *stop_recording(PyObject *self, PyObject *args) {
  (void)self;
  PyObject *capsule;
  long long end_pts;
  if (!PyArg_ParseTuple(args, "OL", &capsule, &end_pts)) {
    return NULL;
  }
  recording_t *recording = PyCapsule_GetPointer(capsule,
                                               "_adb_scr_media.RecordingHandle");
  if (recording == NULL) {
    return NULL;
  }
  char error[512] = {0};
  bool success;
  Py_BEGIN_ALLOW_THREADS
  success = recording_stop(recording, end_pts, error);
  Py_END_ALLOW_THREADS
  if (!success) {
    PyErr_SetString(PyExc_RuntimeError, error);
    return NULL;
  }
  Py_RETURN_NONE;
}

static PyMethodDef MediaExtMethods[] = {
    {"bgra8_to_jpg", bgra8_to_jpg, METH_VARARGS, NULL},
    {"create_decoder", create_decoder, METH_VARARGS, NULL},
    {"destroy_decoder", destroy_decoder, METH_VARARGS, NULL},
    {"enqueue_frame", enqueue_frame, METH_VARARGS, NULL},
    {"get_current_frame_bgra8", get_current_frame_bgra8, METH_VARARGS, NULL},
    {"get_current_frame_jpg", get_current_frame_jpg, METH_VARARGS, NULL},
    {"start_recording", start_recording, METH_VARARGS, NULL},
    {"set_decoder_recording", set_decoder_recording, METH_VARARGS, NULL},
    {"append_recording_audio", append_recording_audio, METH_VARARGS, NULL},
    {"stop_recording", stop_recording, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef adb_scr_media_module = {PyModuleDef_HEAD_INIT,
                                                  "_adb_scr_media",
                                                  NULL,
                                                  -1,
                                                  MediaExtMethods,
                                                  NULL,
                                                  NULL,
                                                  NULL,
                                                  NULL};

PyMODINIT_FUNC PyInit__adb_scr_media(void) {
  PyObject *module = PyModule_Create(&adb_scr_media_module);
  if (module == NULL) {
    return NULL;
  }
#ifdef Py_GIL_DISABLED
  PyUnstable_Module_SetGIL(module, Py_MOD_GIL_NOT_USED);
#endif
  return module;
}
