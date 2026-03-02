#include "jpg_encoder.h"
#include <turbojpeg.h>
#include <stdio.h>

bool encode_bgra8_to_jpg(uint32_t width, uint32_t height, const uint8_t *bgra8, uint8_t quality, uint32_t *jpg_size, uint8_t *jpg)
{
  tjhandle handle = tj3Init(TJINIT_COMPRESS);
  tj3Set(handle, TJPARAM_QUALITY, quality);
  tj3Set(handle, TJPARAM_SUBSAMP, TJSAMP_420);
  tj3Set(handle, TJPARAM_NOREALLOC, 1);

  size_t jpegSize = *jpg_size;
  int result = tj3Compress8(handle, bgra8, (int)width, 0, (int)height,
                            TJPF_BGRA, &jpg, &jpegSize);
  if (result != 0)
  {
    *jpg_size = 0;
    const char *errMsg = tj3GetErrorStr(handle);
    printf("Error: %s\n", errMsg);
  }
  else
  {
    *jpg_size = (uint32_t)jpegSize;
  }

  tj3Destroy(handle);
  return result == 0;
}
