#import <VideoToolbox/VideoToolbox.h>
#import <ImageIO/ImageIO.h>
#include <assert.h>
#include <string.h>

static bool force_unavailable;
static int create_count;
static int encode_count;
static CMTime previous_pts;

static OSStatus test_create(CFAllocatorRef allocator, int32_t width,
                            int32_t height, CMVideoCodecType codec,
                            CFDictionaryRef specification,
                            CFDictionaryRef attributes,
                            CFAllocatorRef compressed_allocator,
                            VTCompressionOutputCallback callback, void *refcon,
                            VTCompressionSessionRef *session) {
  create_count++;
  previous_pts = kCMTimeInvalid;
  if (force_unavailable) {
    *session = NULL;
    return kVTCouldNotFindVideoEncoderErr;
  }
  return VTCompressionSessionCreate(allocator, width, height, codec,
      specification, attributes, compressed_allocator, callback, refcon, session);
}

static OSStatus test_encode(VTCompressionSessionRef session, CVImageBufferRef image,
                            CMTime pts, CMTime duration, CFDictionaryRef properties,
                            VTEncodeInfoFlags *flags, VTCompressionOutputHandler handler) {
  // VideoToolbox requires increasing PTS even for repeated screenshots.
  assert(!CMTIME_IS_VALID(previous_pts) || CMTimeCompare(pts, previous_pts) > 0);
  previous_pts = pts;
  encode_count++;
  return VTCompressionSessionEncodeFrameWithOutputHandler(
      session, image, pts, duration, properties, flags, handler);
}

// Intercept only the OS boundaries; exercise the production encoder unchanged.
#define VTCompressionSessionCreate test_create
#define VTCompressionSessionEncodeFrameWithOutputHandler test_encode
#include "frame_jpg_encoder.m"
#undef VTCompressionSessionCreate
#undef VTCompressionSessionEncodeFrameWithOutputHandler

int main(int argc, const char **argv) {
  force_unavailable = argc > 1 && strcmp(argv[1], "fallback") == 0;
  @autoreleasepool {
    CVPixelBufferRef frame = NULL;
    NSDictionary *attributes = @{(id)kCVPixelBufferIOSurfacePropertiesKey: @{},
                                 (id)kCVPixelBufferMetalCompatibilityKey: @YES};
    assert(CVPixelBufferCreate(NULL, 96, 64,
        kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
        (CFDictionaryRef)attributes, &frame) == kCVReturnSuccess);
    assert(CVPixelBufferLockBaseAddress(frame, 0) == kCVReturnSuccess);
    memset(CVPixelBufferGetBaseAddressOfPlane(frame, 0), 126,
           CVPixelBufferGetBytesPerRowOfPlane(frame, 0) * 64);
    memset(CVPixelBufferGetBaseAddressOfPlane(frame, 1), 128,
           CVPixelBufferGetBytesPerRowOfPlane(frame, 1) * 32);
    CVPixelBufferUnlockBaseAddress(frame, 0);

    frame_jpg_encoder_t *encoder = frame_jpg_encoder_create();
    frame_jpg_options_t options = {.quality = 75, .scale = 1.0};
    for (int i = 0; i < 3; i++) {
      CFDataRef data = NULL;
      assert(frame_jpg_encode(encoder, frame, &options, &data) == FRAME_JPG_OK);
      CGImageSourceRef source = CGImageSourceCreateWithData(data, NULL);
      assert(source != NULL);
      CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, NULL);
      assert(image != NULL && CGImageGetWidth(image) == 96 && CGImageGetHeight(image) == 64);
      CGImageRelease(image);
      CFRelease(source);
      CFRelease(data);
      if (!force_unavailable && encoder->hardware_unavailable) {
        frame_jpg_encoder_destroy(encoder);
        CVPixelBufferRelease(frame);
        return 77;
      }
    }
    assert(create_count == 1);
    assert(encode_count == (force_unavailable ? 0 : 3));
    frame_jpg_encoder_destroy(encoder);
    CVPixelBufferRelease(frame);
  }
  return 0;
}
