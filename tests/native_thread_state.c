// Exercise the real Python binding and encoder, checking their call boundary.
#define encode_bgra8_to_jpg checked_encode_bgra8_to_jpg
#include "adb_scr_media.c"
#undef encode_bgra8_to_jpg
#include "jpg_encoder.c"

bool checked_encode_bgra8_to_jpg(uint32_t width, uint32_t height,
                                const uint8_t *bgra8, uint8_t quality,
                                uint32_t *jpg_size, uint8_t *jpg) {
#if PY_VERSION_HEX >= 0x030D0000
  // This accessor is safe without an attached thread state, including 3.14t.
  if (PyThreadState_GetUnchecked() != NULL) {
    return false;
  }
#else
  if (PyGILState_Check()) {
    return false;
  }
#endif
  return encode_bgra8_to_jpg(width, height, bgra8, quality, jpg_size, jpg);
}
