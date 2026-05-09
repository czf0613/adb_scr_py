#include "jpg_encoder.h"
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>
#include <dispatch/dispatch.h>
#include <string.h>

static CGColorSpaceRef color_space;
static dispatch_once_t once_token;

bool encode_bgra8_to_jpg(uint32_t width, uint32_t height, const uint8_t *bgra8,
                         uint8_t quality, uint32_t *jpg_size, uint8_t *jpg) {
  dispatch_once(&once_token, ^{
    color_space = CGColorSpaceCreateDeviceRGB();
  });
  CGDataProviderRef provider = CGDataProviderCreateWithData(
      NULL, bgra8, (size_t)width * height * 4, NULL);

  // BGRA8 in memory = little-endian 32-bit with alpha in the first byte
  CGImageRef image = CGImageCreate(
      width, height, 8, 32, width * 4, color_space,
      kCGBitmapByteOrder32Little | kCGImageAlphaNoneSkipFirst, provider, NULL,
      false, kCGRenderingIntentDefault);
  CGDataProviderRelease(provider);

  if (image == NULL) {
    *jpg_size = 0;
    return false;
  }

  CFMutableDataRef jpeg_data = CFDataCreateMutable(kCFAllocatorDefault, 0);
  CGImageDestinationRef dest = CGImageDestinationCreateWithData(
      jpeg_data, CFSTR("public.jpeg"), 1, NULL);

  if (dest == NULL) {
    CGImageRelease(image);
    CFRelease(jpeg_data);
    *jpg_size = 0;
    return false;
  }

  float q = (float)quality / 100.0f;
  CFNumberRef quality_num =
      CFNumberCreate(kCFAllocatorDefault, kCFNumberFloat32Type, &q);
  CFStringRef keys[] = {kCGImageDestinationLossyCompressionQuality};
  CFTypeRef values[] = {quality_num};
  CFDictionaryRef options = CFDictionaryCreate(
      kCFAllocatorDefault, (const void **)keys, (const void **)values, 1,
      &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);

  CGImageDestinationAddImage(dest, image, options);
  bool success = CGImageDestinationFinalize(dest);

  CFRelease(options);
  CFRelease(quality_num);
  CFRelease(dest);
  CGImageRelease(image);

  if (success) {
    CFIndex data_length = CFDataGetLength(jpeg_data);
    if ((uint32_t)data_length <= *jpg_size) {
      memcpy(jpg, CFDataGetBytePtr(jpeg_data), data_length);
      *jpg_size = (uint32_t)data_length;
    } else {
      *jpg_size = 0;
      success = false;
    }
  } else {
    *jpg_size = 0;
  }

  CFRelease(jpeg_data);
  return success;
}
