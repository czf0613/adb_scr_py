// Device-free NV12 fitting checks; include the private implementation for pixels.
#include "recording.m"
#include <assert.h>

static CVPixelBufferRef make_frame(size_t width, size_t height) {
  CVPixelBufferRef frame = NULL;
  NSDictionary *attributes = @{
      (id)kCVPixelBufferIOSurfacePropertiesKey: @{},
      (id)kCVPixelBufferMetalCompatibilityKey: @YES};
  assert(CVPixelBufferCreate(NULL, width, height,
      kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
      (CFDictionaryRef)attributes, &frame) == kCVReturnSuccess);
  assert(CVPixelBufferLockBaseAddress(frame, 0) == kCVReturnSuccess);
  for (size_t plane = 0; plane < 2; plane++) {
    uint8_t *data = CVPixelBufferGetBaseAddressOfPlane(frame, plane);
    size_t stride = CVPixelBufferGetBytesPerRowOfPlane(frame, plane);
    for (size_t y = 0; y < CVPixelBufferGetHeightOfPlane(frame, plane); y++) {
      for (size_t x = 0; x < width; x++) {
        data[y * stride + x] = plane == 0 ? 96 : (x % 2 == 0 ? 64 : 192);
      }
    }
  }
  CVPixelBufferUnlockBaseAddress(frame, 0);
  return frame;
}

static void check_fit(size_t width, size_t height) {
  recording_t r = {0};
  pthread_mutex_init(&r.ingress, NULL);
  r.width = height;
  r.height = width;
  NSDictionary *attributes = @{
      (id)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
      (id)kCVPixelBufferIOSurfacePropertiesKey: @{},
      (id)kCVPixelBufferMetalCompatibilityKey: @YES};
  assert(VTCompressionSessionCreate(NULL, (int)r.width, (int)r.height,
      kCMVideoCodecType_H264, NULL, (CFDictionaryRef)attributes, NULL,
      encoded_frame, &r, &r.encoder) == noErr);

  CVPixelBufferRef same = make_frame(r.width, r.height);
  CVPixelBufferRef retained = fit_frame(&r, same);
  assert(retained == same);
  CVPixelBufferRelease(retained);
  CVPixelBufferRelease(same);

  CVPixelBufferRef input = make_frame(width, height);
  // Repeated calls must reuse resources without retaining an ever-growing queue.
  for (int iteration = 0; iteration < 4; iteration++) {
    CVPixelBufferRef output = fit_frame(&r, input);
    assert(output != NULL);
    assert(CVPixelBufferGetPixelFormatType(output) == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange);
    assert(CVPixelBufferLockBaseAddress(output, kCVPixelBufferLock_ReadOnly) == kCVReturnSuccess);
    double scale = fmin((double)r.width / width, (double)r.height / height);
    for (size_t plane = 0; plane < 2; plane++) {
      size_t sw = CVPixelBufferGetWidthOfPlane(input, plane);
      size_t sh = CVPixelBufferGetHeightOfPlane(input, plane);
      size_t dw = CVPixelBufferGetWidthOfPlane(output, plane);
      size_t dh = CVPixelBufferGetHeightOfPlane(output, plane);
      size_t stride = CVPixelBufferGetBytesPerRowOfPlane(output, plane);
      uint8_t *data = CVPixelBufferGetBaseAddressOfPlane(output, plane);
      for (size_t y = 0; y < dh; y++) {
        for (size_t x = 0; x < dw; x++) {
          double u = (x + 0.5 - (dw - sw * scale) / 2) / (sw * scale);
          double v = (y + 0.5 - (dh - sh * scale) / 2) / (sh * scale);
          bool inside = u >= 0 && u < 1 && v >= 0 && v < 1;
          if (plane == 0) {
            assert(data[y * stride + x] == (inside ? 96 : 16));
          } else {
            assert(data[y * stride + 2 * x] == (inside ? 64 : 128));
            assert(data[y * stride + 2 * x + 1] == (inside ? 192 : 128));
          }
        }
      }
    }
    CVPixelBufferUnlockBaseAddress(output, kCVPixelBufferLock_ReadOnly);
    CVPixelBufferRelease(output);
  }
  // Linear ramps catch swapped planes, vertical flips and sampling offsets.
  assert(CVPixelBufferLockBaseAddress(input, 0) == kCVReturnSuccess);
  for (size_t plane = 0; plane < 2; plane++) {
    uint8_t *data = CVPixelBufferGetBaseAddressOfPlane(input, plane);
    size_t stride = CVPixelBufferGetBytesPerRowOfPlane(input, plane);
    size_t pw = CVPixelBufferGetWidthOfPlane(input, plane);
    size_t ph = CVPixelBufferGetHeightOfPlane(input, plane);
    for (size_t y = 0; y < ph; y++) {
      for (size_t x = 0; x < pw; x++) {
        if (plane == 0) {
          data[y * stride + x] = 32 + x + y / 2;
        } else {
          data[y * stride + 2 * x] = 64 + x;
          data[y * stride + 2 * x + 1] = 96 + y;
        }
      }
    }
  }
  CVPixelBufferUnlockBaseAddress(input, 0);
  CVPixelBufferRef output = fit_frame(&r, input);
  assert(output != NULL);
  assert(CVPixelBufferLockBaseAddress(output, kCVPixelBufferLock_ReadOnly) == kCVReturnSuccess);
  double scale = fmin((double)r.width / width, (double)r.height / height);
  for (size_t plane = 0; plane < 2; plane++) {
    size_t sw = CVPixelBufferGetWidthOfPlane(input, plane);
    size_t sh = CVPixelBufferGetHeightOfPlane(input, plane);
    size_t dw = CVPixelBufferGetWidthOfPlane(output, plane);
    size_t dh = CVPixelBufferGetHeightOfPlane(output, plane);
    size_t stride = CVPixelBufferGetBytesPerRowOfPlane(output, plane);
    uint8_t *data = CVPixelBufferGetBaseAddressOfPlane(output, plane);
    for (size_t y = 0; y < dh; y++) {
      for (size_t x = 0; x < dw; x++) {
        double sx = (x + 0.5 - (dw - sw * scale) / 2) / scale - 0.5;
        double sy = (y + 0.5 - (dh - sh * scale) / 2) / scale - 0.5;
        if (sx < 0 || sx >= sw - 1 || sy < 0 || sy >= sh - 1) {
          continue;
        }
        if (plane == 0) {
          double fy = floor(sy);
          double expected = 32 + sx + floor(fy / 2) * (1 - (sy - fy))
              + floor((fy + 1) / 2) * (sy - fy);
          assert(fabs(data[y * stride + x] - expected) <= 1);
        } else {
          assert(fabs(data[y * stride + 2 * x] - (64 + sx)) <= 1);
          assert(fabs(data[y * stride + 2 * x + 1] - (96 + sy)) <= 1);
        }
      }
    }
  }
  CVPixelBufferUnlockBaseAddress(output, kCVPixelBufferLock_ReadOnly);
  CVPixelBufferRelease(output);
  // Explicit BT.601 input still needs the previous conversion to BT.709.
  assert(CVPixelBufferLockBaseAddress(input, 0) == kCVReturnSuccess);
  memset(CVPixelBufferGetBaseAddressOfPlane(input, 0), 81,
         CVPixelBufferGetBytesPerRowOfPlane(input, 0) * height);
  uint8_t *uv = CVPixelBufferGetBaseAddressOfPlane(input, 1);
  size_t uv_stride = CVPixelBufferGetBytesPerRowOfPlane(input, 1);
  for (size_t y = 0; y < height / 2; y++) {
    for (size_t x = 0; x < width; x += 2) {
      uv[y * uv_stride + x] = 90;
      uv[y * uv_stride + x + 1] = 240;
    }
  }
  CVPixelBufferUnlockBaseAddress(input, 0);
  CVBufferSetAttachment(input, kCVImageBufferYCbCrMatrixKey,
      kCVImageBufferYCbCrMatrix_ITU_R_601_4, kCVAttachmentMode_ShouldPropagate);
  CVBufferSetAttachment(input, kCVImageBufferColorPrimariesKey,
      kCVImageBufferColorPrimaries_ITU_R_709_2, kCVAttachmentMode_ShouldPropagate);
  CVBufferSetAttachment(input, kCVImageBufferTransferFunctionKey,
      kCVImageBufferTransferFunction_ITU_R_709_2, kCVAttachmentMode_ShouldPropagate);
  output = fit_frame(&r, input);
  assert(output != NULL);
  assert(CVPixelBufferLockBaseAddress(output, kCVPixelBufferLock_ReadOnly) == kCVReturnSuccess);
  uint8_t *luma = CVPixelBufferGetBaseAddressOfPlane(output, 0);
  size_t y_center = r.height / 2 * CVPixelBufferGetBytesPerRowOfPlane(output, 0) + r.width / 2;
  uv = CVPixelBufferGetBaseAddressOfPlane(output, 1);
  size_t uv_center = r.height / 4 * CVPixelBufferGetBytesPerRowOfPlane(output, 1) + (r.width / 2 & ~1);
  assert(abs(luma[y_center] - 62) <= 2);
  assert(abs(uv[uv_center] - 102) <= 2);
  assert(abs(uv[uv_center + 1] - 240) <= 2);
  CVPixelBufferUnlockBaseAddress(output, kCVPixelBufferLock_ReadOnly);
  CVPixelBufferRelease(output);
  CVPixelBufferRelease(input);
  destroy_resources(&r);
  pthread_mutex_destroy(&r.ingress);
}

int main(void) {
  @autoreleasepool {
    check_fit(64, 128);
    check_fit(128, 64);
    check_fit(66, 130);
  }
  return 0;
}
