#include "frame_jpg_encoder.h"
#import <CoreImage/CoreImage.h>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <ImageIO/ImageIO.h>
#include <VideoToolbox/VideoToolbox.h>
#include <math.h>

struct frame_jpg_encoder {
  CIContext *context;
  CGColorSpaceRef color_space;
  VTCompressionSessionRef session;
  size_t width, height;
  int64_t next_pts;
  bool hardware_unavailable;
};

frame_jpg_encoder_t *frame_jpg_encoder_create(void) {
  return calloc(1, sizeof(frame_jpg_encoder_t));
}

static void close_session(frame_jpg_encoder_t *encoder) {
  if (encoder->session != NULL) {
    VTCompressionSessionCompleteFrames(encoder->session, kCMTimeInvalid);
    VTCompressionSessionInvalidate(encoder->session);
    CFRelease(encoder->session);
    encoder->session = NULL;
  }
}

void frame_jpg_encoder_destroy(frame_jpg_encoder_t *encoder) {
  if (encoder == NULL) {
    return;
  }
  close_session(encoder);
  [encoder->context release];
  if (encoder->color_space != NULL) {
    CGColorSpaceRelease(encoder->color_space);
  }
  free(encoder);
}

static CFDataRef encode_hardware(frame_jpg_encoder_t *encoder,
                                 CVPixelBufferRef frame, int quality) {
  size_t width = CVPixelBufferGetWidth(frame);
  size_t height = CVPixelBufferGetHeight(frame);
  if (width != encoder->width || height != encoder->height) {
    close_session(encoder);
    encoder->width = width;
    encoder->height = height;
    encoder->hardware_unavailable = false;
  }
  if (encoder->hardware_unavailable) {
    return NULL;
  }
  if (encoder->session == NULL) {
    encoder->next_pts = 0;
    NSDictionary *specification = @{
      (id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: @YES
    };
    OSStatus status = VTCompressionSessionCreate(
        kCFAllocatorDefault, (int32_t)width, (int32_t)height,
        kCMVideoCodecType_JPEG, (CFDictionaryRef)specification,
        NULL, NULL, NULL, NULL, &encoder->session);
    if (status != noErr || encoder->session == NULL) {
      close_session(encoder);
      encoder->hardware_unavailable = true;
      return NULL;
    }
  }

  NSNumber *compression_quality = @(quality / 100.0);
  OSStatus status = VTSessionSetProperty(
      encoder->session, kVTCompressionPropertyKey_Quality,
      (CFNumberRef)compression_quality);
  if (status != noErr) {
    close_session(encoder);
    encoder->hardware_unavailable = true;
    return NULL;
  }

  __block CFDataRef data = NULL;
  status = VTCompressionSessionEncodeFrameWithOutputHandler(
      encoder->session, frame, CMTimeMake(encoder->next_pts++, 1),
      kCMTimeInvalid, NULL, NULL,
      ^(OSStatus result, VTEncodeInfoFlags flags, CMSampleBufferRef sample) {
        if (result != noErr || sample == NULL) {
          return;
        }
        CMBlockBufferRef block = CMSampleBufferGetDataBuffer(sample);
        if (block == NULL) {
          return;
        }
        size_t size = CMBlockBufferGetDataLength(block);
        CFMutableDataRef bytes = CFDataCreateMutable(kCFAllocatorDefault, size);
        CFDataSetLength(bytes, size);
        if (CMBlockBufferCopyDataBytes(block, 0, size,
                                      CFDataGetMutableBytePtr(bytes)) == noErr) {
          data = bytes;
        } else {
          CFRelease(bytes);
        }
      });
  // CompleteFrames waits for the output handler; no callback may outlive data.
  OSStatus completed = VTCompressionSessionCompleteFrames(encoder->session,
                                                         kCMTimeInvalid);
  if (status != noErr || completed != noErr || data == NULL) {
    close_session(encoder);
    encoder->hardware_unavailable = true;
    if (data != NULL) {
      CFRelease(data);
    }
    return NULL;
  }
  return data;
}

static CFDataRef encode_core_image(frame_jpg_encoder_t *encoder,
                                   CVPixelBufferRef frame, CGRect crop,
                                   size_t width, size_t height, int quality) {
  if (encoder->context == nil) {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device != nil) {
      encoder->context = [[CIContext contextWithMTLDevice:device] retain];
      [device release];
    } else {
      encoder->context = [[CIContext contextWithOptions:@{
        kCIContextUseSoftwareRenderer: @YES
      }] retain];
    }
    encoder->color_space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
  }

  // Core Image uses a bottom-left origin; the API accepts top-left coordinates.
  CIImage *image = [CIImage imageWithCVPixelBuffer:frame];
  image = [image imageByCroppingToRect:crop];
  image = [image imageByApplyingTransform:
      CGAffineTransformMakeTranslation(-crop.origin.x, -crop.origin.y)];
  // Clamp before resampling so interpolation cannot blend transparent borders.
  image = [image imageByClampingToExtent];
  image = [image imageByApplyingTransform:
      CGAffineTransformMakeScale(width / crop.size.width,
                                 height / crop.size.height)];
  image = [image imageByCroppingToRect:CGRectMake(0, 0, width, height)];
  NSData *data = [encoder->context JPEGRepresentationOfImage:image
      colorSpace:encoder->color_space
      options:@{(id)kCGImageDestinationLossyCompressionQuality: @(quality / 100.0)}];
  return data == nil ? NULL : (CFDataRef)[data retain];
}

frame_jpg_status_t frame_jpg_encode(frame_jpg_encoder_t *encoder,
                                  CVPixelBufferRef frame,
                                  const frame_jpg_options_t *options,
                                  CFDataRef *out_data) {
  size_t source_width = CVPixelBufferGetWidth(frame);
  size_t source_height = CVPixelBufferGetHeight(frame);
  CGRect crop = CGRectMake(0, 0, source_width, source_height);
  if (options->has_roi) {
    if (options->x < 0 || options->y < 0 || options->width <= 0 ||
        options->height <= 0 || (uint64_t)options->x >= source_width ||
        (uint64_t)options->y >= source_height ||
        (uint64_t)options->width > source_width - (uint64_t)options->x ||
        (uint64_t)options->height > source_height - (uint64_t)options->y) {
      return FRAME_JPG_INVALID_ROI;
    }
    crop = CGRectMake(options->x,
                      source_height - options->y - options->height,
                      options->width, options->height);
  }
  double width = floor(crop.size.width * options->scale + 0.5);
  double height = floor(crop.size.height * options->scale + 0.5);
  if (!isfinite(width) || !isfinite(height) || width > 65535 || height > 65535) {
    return FRAME_JPG_INVALID_SIZE;
  }
  width = fmax(1, width);
  height = fmax(1, height);

  @autoreleasepool {
    if (!options->has_roi && options->scale == 1.0) {
      *out_data = encode_hardware(encoder, frame, options->quality);
    }
    if (*out_data == NULL) {
      *out_data = encode_core_image(encoder, frame, crop, (size_t)width,
                                    (size_t)height, options->quality);
    }
  }
  return *out_data != NULL ? FRAME_JPG_OK : FRAME_JPG_FAILED;
}
