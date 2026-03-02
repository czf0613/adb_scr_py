#include "jpg_encoder.h"
#include "vtb_decoder.h"
#include <Python.h>
#include <stdlib.h>

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
  if (encode_bgra8_to_jpg(width, height, (const uint8_t *)bgra8_data, quality,
                          &jpg_size, jpg_buffer)) {
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
  if (vtb_create_decoder((const uint8_t *)sps_and_pps_data,
                         (size_t)sps_and_pps_size, &decoder, &width,
                         &height) != 0) {
    Py_RETURN_NONE;
  }

  PyObject *capsule =
      PyCapsule_New(decoder, "_adb_scr_media.DecoderHandle", NULL);

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

  void *decoder = PyCapsule_GetPointer(capsule, "_adb_scr_media.DecoderHandle");
  vtb_destroy_decoder(&decoder);
  // 此时decoder指向NULL，capsule内部的值已经被破环
  // 前文创建capsule的时候，析构函数给的就是NULL，所以这里恰好是不会crash的
  // 无需额外处理

  Py_RETURN_NONE;
}

static PyObject *enqueue_frame(PyObject *self, PyObject *args) {
  (void)self;

  PyObject *capsule;
  PyBytesObject *nalu_bytes;
  int64_t pts;
  PyArg_ParseTuple(args, "OO!L", &capsule, &PyBytes_Type, &nalu_bytes, &pts);

  void *decoder = PyCapsule_GetPointer(capsule, "_adb_scr_media.DecoderHandle");

  char *nalu_data = NULL;
  Py_ssize_t nalu_size = 0;
  PyBytes_AsStringAndSize((PyObject *)nalu_bytes, &nalu_data, &nalu_size);

  if (vtb_enqueue_frame(decoder, (const uint8_t *)nalu_data, (size_t)nalu_size,
                        pts)) {
    Py_RETURN_TRUE;
  } else {
    Py_RETURN_FALSE;
  }
}

static PyObject *get_current_frame_bgra8(PyObject *self, PyObject *args) {
  (void)self;

  PyObject *capsule;
  PyArg_ParseTuple(args, "O", &capsule);

  void *decoder = PyCapsule_GetPointer(capsule, "_adb_scr_media.DecoderHandle");

  uint8_t *frame_data = NULL;
  size_t width = 0, height = 0;
  if (!vtb_current_frame_bgra8(decoder, &frame_data, &width, &height)) {
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

static PyMethodDef MediaExtMethods[] = {
    {"bgra8_to_jpg", bgra8_to_jpg, METH_VARARGS, NULL},
    {"create_decoder", create_decoder, METH_VARARGS, NULL},
    {"destroy_decoder", destroy_decoder, METH_VARARGS, NULL},
    {"enqueue_frame", enqueue_frame, METH_VARARGS, NULL},
    {"get_current_frame_bgra8", get_current_frame_bgra8, METH_VARARGS, NULL},
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
  return PyModule_Create(&adb_scr_media_module);
}
