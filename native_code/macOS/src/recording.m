#include "recording.h"
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <CoreImage/CoreImage.h>
#import <Metal/Metal.h>
#import <VideoToolbox/VideoToolbox.h>
#include <dispatch/dispatch.h>
#include <stdatomic.h>
#include <pthread.h>
#include <unistd.h>
#include <math.h>
#include <errno.h>

typedef struct {
  int64_t start;
  int64_t duration;
} audio_gap_t;

struct recording {
  atomic_uint references;
  pthread_mutex_t ingress;
  dispatch_queue_t queue;
  bool accepting;
  bool finished;
  unsigned pending_video;
  unsigned pending_audio;
  size_t pending_audio_bytes;
  char error[512];
  CVPixelBufferRef latest;
  int64_t latest_pts;
  int64_t source_pts;
  int64_t last_video_pts;
  int64_t last_audio_pts;
  int fps;
  size_t width;
  size_t height;
  VTCompressionSessionRef encoder;
  CMSampleBufferRef encoded;
  OSStatus encode_status;
  AVAssetWriter *writer;
  AVAssetWriterInput *video;
  AVAssetWriterInput *audio;
  CMAudioFormatDescriptionRef audio_format;
  int sample_rate;
  NSMutableData *first_audio;
  size_t first_audio_sizes[3];
  unsigned first_audio_count;
  int64_t first_audio_pts;
  int64_t first_audio_timestamps[3];
  NSData *last_audio;
  size_t audio_packet_count;
  audio_gap_t audio_gaps[4096];
  size_t audio_gap_count;
  int64_t audio_gap_duration;
  id<MTLCommandQueue> fit_queue;
  id<MTLComputePipelineState> fit_pipeline;
  CVMetalTextureCacheRef fit_cache;
  CIContext *color_context;
};

static void set_error(recording_t *r, const char *message) {
  pthread_mutex_lock(&r->ingress);
  if (r->error[0] == '\0') {
    snprintf(r->error, sizeof(r->error), "%s", message);
  }
  pthread_mutex_unlock(&r->ingress);
}

static bool copy_error(recording_t *r, char error[512]) {
  pthread_mutex_lock(&r->ingress);
  snprintf(error, 512, "%s", r->error);
  pthread_mutex_unlock(&r->ingress);
  return error[0] == '\0';
}

static void writer_error(recording_t *r, const char *operation) {
  NSString *detail = r->writer.error.localizedDescription;
  char message[512];
  snprintf(message, sizeof(message), "%s: %s", operation,
           detail != nil ? detail.UTF8String : "AVAssetWriter rejected sample");
  set_error(r, message);
}

static bool append_sample(recording_t *r, AVAssetWriterInput *input,
                           CMSampleBufferRef sample) {
  char prior_error[512];
  if (!copy_error(r, prior_error)) {
    return false;
  }
  // Only this recorder queue waits for writer backpressure, never the decoder.
  double deadline = CFAbsoluteTimeGetCurrent() + 5;
  while (!input.readyForMoreMediaData) {
    if (r->writer.status != AVAssetWriterStatusWriting) {
      writer_error(r, "writer stopped");
      return false;
    }
    if (CFAbsoluteTimeGetCurrent() >= deadline) {
      set_error(r, "recording writer backpressure timed out");
      return false;
    }
    usleep(1000);
  }
  if (![input appendSampleBuffer:sample]) {
    writer_error(r, "append compressed sample");
    return false;
  }
  return true;
}

static void encoded_frame(void *context, void *source, OSStatus status,
                            VTEncodeInfoFlags flags, CMSampleBufferRef sample) {
  (void)source;
  recording_t *r = context;
  r->encode_status = status;
  if ((flags & kVTEncodeInfo_FrameDropped) || sample == NULL) {
    r->encode_status = status != noErr ? status : -1;
  } else if (status == noErr) {
    r->encoded = (CMSampleBufferRef)CFRetain(sample);
  }
}

static bool prepare_frame_fitter(recording_t *r) {
  if (r->fit_pipeline != nil) {
    return true;
  }
  id<MTLDevice> device = MTLCreateSystemDefaultDevice();
  if (device == nil) {
    set_error(r, "recording rotation requires a Metal device");
    return false;
  }
  r->fit_queue = [device newCommandQueue];
  CVReturn status = CVMetalTextureCacheCreate(NULL, NULL, device, NULL, &r->fit_cache);
  // Sample the two NV12 planes directly; no YUV/RGB round trip or CPU pixel copy.
  NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\n"
      "kernel void fit_nv12(texture2d<float, access::sample> input [[texture(0)]], "
      "texture2d<float, access::write> output [[texture(1)]], "
      "constant float4 &rect [[buffer(0)]], constant float &black [[buffer(1)]], "
      "uint2 pos [[thread_position_in_grid]]) { "
      "  if (pos.x >= output.get_width() || pos.y >= output.get_height()) { return; } "
      "  float2 uv = (float2(pos) + 0.5f - rect.xy) / rect.zw; "
      "  constexpr sampler s(coord::normalized, address::clamp_to_edge, filter::linear); "
      "  float4 value = float4(black); "
      "  if (all(uv >= 0.0f) && all(uv < 1.0f)) { value = input.sample(s, uv); } "
      "  output.write(value, pos); "
      "}";
  NSError *error = nil;
  id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&error];
  id<MTLFunction> function = [library newFunctionWithName:@"fit_nv12"];
  if (function != nil && r->fit_queue != nil && status == kCVReturnSuccess) {
    r->fit_pipeline = [device newComputePipelineStateWithFunction:function error:&error];
  }
  [function release];
  [library release];
  [device release];
  if (r->fit_pipeline == nil) {
    set_error(r, error != nil ? error.localizedDescription.UTF8String
                             : "create Metal recording frame fitter failed");
    return false;
  }
  return true;
}

static bool needs_color_conversion(CVPixelBufferRef frame) {
  CFStringRef keys[] = {kCVImageBufferYCbCrMatrixKey,
      kCVImageBufferColorPrimariesKey, kCVImageBufferTransferFunctionKey};
  CFStringRef values[] = {kCVImageBufferYCbCrMatrix_ITU_R_709_2,
      kCVImageBufferColorPrimaries_ITU_R_709_2, kCVImageBufferTransferFunction_ITU_R_709_2};
  CVAttachmentMode modes[] = {kCVAttachmentMode_ShouldPropagate,
                             kCVAttachmentMode_ShouldNotPropagate};
  for (size_t mode = 0; mode < 2; mode++) {
    CFDictionaryRef attachments = CVBufferCopyAttachments(frame, modes[mode]);
    if (attachments == NULL) {
      continue;
    }
    bool convert = false;
    for (size_t i = 0; i < 3; i++) {
      CFTypeRef value = CFDictionaryGetValue(attachments, keys[i]);
      if (value != NULL && !CFEqual(value, values[i])) {
        convert = true;
      }
    }
    CFRelease(attachments);
    if (convert) {
      return true;
    }
  }
  return false;
}

static bool fit_frame_with_color_conversion(recording_t *r,
                                            CVPixelBufferRef frame,
                                            CVPixelBufferRef fitted) {
  if (r->color_context == nil) {
    r->color_context = [[CIContext contextWithMTLCommandQueue:r->fit_queue
        options:@{kCIContextCacheIntermediates: @NO}] retain];
  }
  CIImage *image = [CIImage imageWithCVPixelBuffer:frame];
  double scale = fmin((double)r->width / CVPixelBufferGetWidth(frame),
                     (double)r->height / CVPixelBufferGetHeight(frame));
  image = [image imageByApplyingTransform:CGAffineTransformMakeScale(scale, scale)];
  image = [image imageByApplyingTransform:CGAffineTransformMakeTranslation(
      (r->width - image.extent.size.width) / 2,
      (r->height - image.extent.size.height) / 2)];
  CGRect canvas = CGRectMake(0, 0, r->width, r->height);
  CIImage *black = [[CIImage imageWithColor:[CIColor blackColor]] imageByCroppingToRect:canvas];
  image = [[image imageByCompositingOverImage:black] imageByCroppingToRect:canvas];
  CIRenderDestination *destination = [[CIRenderDestination alloc] initWithPixelBuffer:fitted];
  NSError *error = nil;
  CIRenderTask *task = [r->color_context startTaskToRender:image
      toDestination:destination error:&error];
  bool success = task != nil && [task waitUntilCompletedAndReturnError:&error] != nil;
  [destination release];
  if (!success) {
    set_error(r, error != nil ? error.localizedDescription.UTF8String
                             : "Metal recording color conversion failed");
  }
  return success;
}

static CVPixelBufferRef fit_frame(recording_t *r, CVPixelBufferRef frame) {
  if (CVPixelBufferGetWidth(frame) == r->width &&
      CVPixelBufferGetHeight(frame) == r->height) {
    return CVPixelBufferRetain(frame);
  }
  if (!prepare_frame_fitter(r)) {
    return NULL;
  }
  CVPixelBufferRef fitted = NULL;
  OSStatus status = CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault,
      VTCompressionSessionGetPixelBufferPool(r->encoder), &fitted);
  if (status != noErr || fitted == NULL) {
    set_error(r, "allocate recording rotation canvas failed");
    return NULL;
  }
  CVBufferSetAttachment(fitted, kCVImageBufferYCbCrMatrixKey,
      kCVImageBufferYCbCrMatrix_ITU_R_709_2, kCVAttachmentMode_ShouldPropagate);
  CVBufferSetAttachment(fitted, kCVImageBufferColorPrimariesKey,
      kCVImageBufferColorPrimaries_ITU_R_709_2, kCVAttachmentMode_ShouldPropagate);
  CVBufferSetAttachment(fitted, kCVImageBufferTransferFunctionKey,
      kCVImageBufferTransferFunction_ITU_R_709_2, kCVAttachmentMode_ShouldPropagate);
  // Preserve the old color-managed fitting for explicitly non-BT.709 input.
  if (needs_color_conversion(frame)) {
    if (!fit_frame_with_color_conversion(r, frame, fitted)) {
      CVPixelBufferRelease(fitted);
      return NULL;
    }
    return fitted;
  }
  CVMetalTextureRef textures[4] = {NULL};
  id<MTLCommandBuffer> command = [r->fit_queue commandBuffer];
  id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
  bool success = encoder != nil;
  if (!success) {
    set_error(r, "create Metal recording command failed");
  }
  [encoder setComputePipelineState:r->fit_pipeline];
  double scale = fmin((double)r->width / CVPixelBufferGetWidth(frame),
                     (double)r->height / CVPixelBufferGetHeight(frame));
  for (size_t plane = 0; plane < 2 && success; plane++) {
    size_t sw = CVPixelBufferGetWidthOfPlane(frame, plane);
    size_t sh = CVPixelBufferGetHeightOfPlane(frame, plane);
    size_t dw = CVPixelBufferGetWidthOfPlane(fitted, plane);
    size_t dh = CVPixelBufferGetHeightOfPlane(fitted, plane);
    MTLPixelFormat format = plane == 0 ? MTLPixelFormatR8Unorm : MTLPixelFormatRG8Unorm;
    CVReturn input_status = CVMetalTextureCacheCreateTextureFromImage(NULL,
        r->fit_cache, frame, NULL, format, sw, sh, plane, &textures[2 * plane]);
    CVReturn output_status = CVMetalTextureCacheCreateTextureFromImage(NULL,
        r->fit_cache, fitted, NULL, format, dw, dh, plane, &textures[2 * plane + 1]);
    if (input_status != kCVReturnSuccess || output_status != kCVReturnSuccess) {
      set_error(r, "map NV12 recording planes to Metal textures failed");
      success = false;
      break;
    }
    [encoder setTexture:CVMetalTextureGetTexture(textures[2 * plane]) atIndex:0];
    [encoder setTexture:CVMetalTextureGetTexture(textures[2 * plane + 1]) atIndex:1];
    float rect[4] = {(dw - sw * scale) / 2, (dh - sh * scale) / 2,
                    sw * scale, sh * scale};
    float black = (plane == 0 ? 16.0f : 128.0f) / 255;
    [encoder setBytes:rect length:sizeof(rect) atIndex:0];
    [encoder setBytes:&black length:sizeof(black) atIndex:1];
    [encoder dispatchThreads:MTLSizeMake(dw, dh, 1)
        threadsPerThreadgroup:MTLSizeMake(16, 16, 1)];
  }
  [encoder endEncoding];
  if (success) {
    [command commit];
    // The recorder queue owns these buffers until GPU writes finish. Never wait
    // in the decoder observer, and never submit an unfinished surface to VTB.
    [command waitUntilCompleted];
    success = command.status == MTLCommandBufferStatusCompleted;
    if (!success) {
      set_error(r, "Metal recording frame fitting failed");
    }
  }
  for (size_t i = 0; i < 4; i++) {
    if (textures[i] != NULL) {
      CFRelease(textures[i]);
    }
  }
  if (!success) {
    CVPixelBufferRelease(fitted);
    return NULL;
  }
  return fitted;
}

static bool encode_frame(recording_t *r, CVPixelBufferRef frame,
                          int64_t relative_pts, int64_t duration, bool first) {
  char prior_error[512];
  if (!copy_error(r, prior_error)) {
    return false;
  }
  CVPixelBufferRef fitted = fit_frame(r, frame);
  if (fitted == NULL) {
    return false;
  }
  r->encoded = NULL;
  r->encode_status = noErr;
  CFDictionaryRef properties = first ? (CFDictionaryRef)@{
      (id)kVTEncodeFrameOptionKey_ForceKeyFrame: @YES} : NULL;
  OSStatus status = VTCompressionSessionEncodeFrame(r->encoder, fitted,
      CMTimeMake(relative_pts, 1000000),
      duration > 0 ? CMTimeMake(duration, 1000000) : kCMTimeInvalid,
      properties, NULL, NULL);
  if (status == noErr) {
    status = VTCompressionSessionCompleteFrames(r->encoder, kCMTimeInvalid);
  }
  CVPixelBufferRelease(fitted);
  if (status != noErr || r->encode_status != noErr || r->encoded == NULL) {
    char error[128];
    snprintf(error, sizeof(error), "hardware H.264 encoding failed (%d/%d)",
             (int)status, (int)r->encode_status);
    set_error(r, error);
    if (r->encoded != NULL) {
      CFRelease(r->encoded);
      r->encoded = NULL;
    }
    return false;
  }
  if (first) {
    r->video = [[AVAssetWriterInput assetWriterInputWithMediaType:AVMediaTypeVideo
        outputSettings:nil sourceFormatHint:CMSampleBufferGetFormatDescription(r->encoded)] retain];
    r->video.expectsMediaDataInRealTime = YES;
    r->video.mediaTimeScale = 1000000;
    if (![r->writer canAddInput:r->video]) {
      set_error(r, "cannot add compressed H.264 writer input");
      CFRelease(r->encoded);
      r->encoded = NULL;
      return false;
    }
    [r->writer addInput:r->video];
    if (r->audio != nil) {
      [r->writer addInput:r->audio];
    }
    if (![r->writer startWriting]) {
      writer_error(r, "start MP4 writer");
      CFRelease(r->encoded);
      r->encoded = NULL;
      return false;
    }
    [r->writer startSessionAtSourceTime:kCMTimeZero];
  }
  CMSampleTimingInfo timing = {
    .duration = duration > 0 ? CMTimeMake(duration, 1000000) : kCMTimeInvalid,
    .presentationTimeStamp = CMTimeMake(relative_pts, 1000000),
    .decodeTimeStamp = kCMTimeInvalid};
  CMSampleBufferRef timed = NULL;
  status = CMSampleBufferCreateCopyWithNewTiming(kCFAllocatorDefault, r->encoded,
                                                 1, &timing, &timed);
  CFRelease(r->encoded);
  r->encoded = NULL;
  if (status != noErr || timed == NULL) {
    set_error(r, "set recording sample timestamp failed");
    return false;
  }
  bool success = append_sample(r, r->video, timed);
  CFRelease(timed);
  if (success) {
    r->last_video_pts = relative_pts;
  }
  return success;
}

static bool configure_audio(recording_t *r, const uint8_t *config, size_t size) {
  // Android's requested AAC encoder emits MPEG-4 AAC-LC AudioSpecificConfig.
  if (size < 2 || size > 64) {
    set_error(r, "invalid AAC AudioSpecificConfig");
    return false;
  }
  unsigned object_type = config[0] >> 3;
  unsigned frequency = ((config[0] & 7) << 1) | (config[1] >> 7);
  unsigned channels = (config[1] >> 3) & 15;
  static const int rates[] = {96000, 88200, 64000, 48000, 44100, 32000,
                              24000, 22050, 16000, 12000, 11025, 8000, 7350};
  if (object_type != 2 || frequency >= 13 || channels < 1 || channels > 7 ||
      (config[1] & 7) != 0) {
    set_error(r, "unsupported AAC AudioSpecificConfig; expected AAC-LC");
    return false;
  }
  r->sample_rate = rates[frequency];
  AudioStreamBasicDescription asbd = {0};
  asbd.mSampleRate = r->sample_rate;
  asbd.mFormatID = kAudioFormatMPEG4AAC;
  asbd.mFormatFlags = 0;
  asbd.mFramesPerPacket = 1024;
  asbd.mChannelsPerFrame = channels == 7 ? 8 : channels;
  // Core Audio's AAC magic cookie is an MPEG-4 ES descriptor, including ASC.
  uint8_t cookie[96] = {0x03, (uint8_t)(23 + size), 0, 0, 0,
      0x04, (uint8_t)(15 + size), 0x40, 0x15, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0, 0x05, (uint8_t)size};
  memcpy(cookie + 22, config, size);
  cookie[22 + size] = 0x06;
  cookie[23 + size] = 1;
  cookie[24 + size] = 2;
  OSStatus status = CMAudioFormatDescriptionCreate(kCFAllocatorDefault, &asbd,
      0, NULL, 25 + size, cookie, NULL, &r->audio_format);
  if (status != noErr) {
    set_error(r, "create compressed AAC format failed");
    return false;
  }
  r->audio = [[AVAssetWriterInput assetWriterInputWithMediaType:AVMediaTypeAudio
      outputSettings:nil sourceFormatHint:r->audio_format] retain];
  r->audio.expectsMediaDataInRealTime = YES;
  if (![r->writer canAddInput:r->audio]) {
    set_error(r, "cannot add compressed AAC writer input");
    return false;
  }
  return true;
}

static void destroy_resources(recording_t *r) {
  if (r->encoder != NULL) {
    VTCompressionSessionInvalidate(r->encoder);
    CFRelease(r->encoder);
    r->encoder = NULL;
  }
  if (r->audio_format != NULL) {
    CFRelease(r->audio_format);
    r->audio_format = NULL;
  }
  [r->video release];
  r->video = nil;
  [r->audio release];
  r->audio = nil;
  [r->writer release];
  r->writer = nil;
  [r->color_context release];
  r->color_context = nil;
  [r->fit_pipeline release];
  r->fit_pipeline = nil;
  [r->fit_queue release];
  r->fit_queue = nil;
  if (r->fit_cache != NULL) {
    CFRelease(r->fit_cache);
    r->fit_cache = NULL;
  }
  [r->first_audio release];
  r->first_audio = nil;
  [r->last_audio release];
  r->last_audio = nil;
}

recording_t *recording_create(CVPixelBufferRef frame, const char *path,
                              const uint8_t *config, size_t config_size,
                              int64_t source_pts, int fps, double quality, char error[512]) {
  @autoreleasepool {
    recording_t *r = calloc(1, sizeof(*r));
    atomic_init(&r->references, 1);
    pthread_mutex_init(&r->ingress, NULL);
    r->queue = dispatch_queue_create("com.adb_scr.recording", DISPATCH_QUEUE_SERIAL);
    r->source_pts = source_pts;
    r->last_video_pts = -1;
    r->last_audio_pts = -1;
    r->latest_pts = source_pts;
    r->fps = fps;
    r->width = CVPixelBufferGetWidth(frame);
    r->height = CVPixelBufferGetHeight(frame);
    r->latest = CVPixelBufferRetain(frame);
    NSError *writerError = nil;
    r->writer = [[AVAssetWriter alloc] initWithURL:[NSURL fileURLWithPath:@(path)]
        fileType:AVFileTypeMPEG4 error:&writerError];
    r->writer.movieTimeScale = 1000000;
    if (r->writer == nil) {
      set_error(r, writerError.localizedDescription.UTF8String);
    }
    if (r->error[0] == '\0' && config != NULL) {
      configure_audio(r, config, config_size);
    }
    NSDictionary *specification = @{
      (id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: @YES,
      (id)kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder: @YES};
    NSDictionary *attributes = @{
        (id)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
        (id)kCVPixelBufferMetalCompatibilityKey: @YES,
        (id)kCVPixelBufferIOSurfacePropertiesKey: @{}};
    if (r->error[0] == '\0') {
      OSStatus status = VTCompressionSessionCreate(kCFAllocatorDefault, (int)r->width,
          (int)r->height, kCMVideoCodecType_H264, (CFDictionaryRef)specification,
          (CFDictionaryRef)attributes, NULL, encoded_frame, r, &r->encoder);
      if (status != noErr) {
        set_error(r, "create hardware H.264 recording encoder failed");
      }
    }
    if (r->error[0] == '\0') {
      NSDictionary *properties = @{
        (id)kVTCompressionPropertyKey_RealTime: @YES,
        (id)kVTCompressionPropertyKey_Quality: @(quality),
        (id)kVTCompressionPropertyKey_ColorPrimaries: (id)kCVImageBufferColorPrimaries_ITU_R_709_2,
        (id)kVTCompressionPropertyKey_TransferFunction: (id)kCVImageBufferTransferFunction_ITU_R_709_2,
        (id)kVTCompressionPropertyKey_YCbCrMatrix: (id)kCVImageBufferYCbCrMatrix_ITU_R_709_2,
        (id)kVTCompressionPropertyKey_AllowFrameReordering: @NO,
        (id)kVTCompressionPropertyKey_ExpectedFrameRate: @(fps)};
      OSStatus status = VTSessionSetProperties(r->encoder, (CFDictionaryRef)properties);
      if (status == noErr) {
        status = VTCompressionSessionPrepareToEncodeFrames(r->encoder);
      }
      if (status != noErr) {
        set_error(r, "configure hardware H.264 recording encoder failed");
      }
    }
    // Prepare at start, so the first orientation change does not compile a shader.
    if (r->error[0] == '\0') {
      prepare_frame_fitter(r);
    }
    if (r->error[0] == '\0') {
      encode_frame(r, frame, 0, 0, true);
    }
    if (!copy_error(r, error)) {
      [r->writer cancelWriting];
      r->finished = true;
      destroy_resources(r);
      recording_release(r);
      return NULL;
    }
    r->accepting = true;
    return r;
  }
}

void recording_retain(void *context) {
  recording_t *r = context;
  atomic_fetch_add_explicit(&r->references, 1, memory_order_relaxed);
}

void recording_release(void *context) {
  recording_t *r = context;
  if (atomic_fetch_sub_explicit(&r->references, 1, memory_order_acq_rel) != 1) {
    return;
  }
  // Capsule destruction finishes first; decoder observer releases never wait.
  if (r->latest != NULL) {
    CVPixelBufferRelease(r->latest);
  }
  dispatch_release(r->queue);
  pthread_mutex_destroy(&r->ingress);
  free(r);
}

void recording_submit_frame(void *context, CVPixelBufferRef frame, int64_t pts) {
  recording_t *r = context;
  pthread_mutex_lock(&r->ingress);
  if (!r->accepting || pts < r->source_pts || pts < r->latest_pts) {
    pthread_mutex_unlock(&r->ingress);
    return;
  }
  CVPixelBufferRelease(r->latest);
  r->latest = CVPixelBufferRetain(frame);
  r->latest_pts = pts;
  if (r->pending_video >= 2 || r->error[0] != '\0') {
    pthread_mutex_unlock(&r->ingress);
    return;
  }
  r->pending_video++;
  CVPixelBufferRef retained = CVPixelBufferRetain(frame);
  dispatch_async(r->queue, ^{
    @autoreleasepool {
      int64_t relative = pts - r->source_pts;
      if (relative > r->last_video_pts) {
        encode_frame(r, retained, relative, 0, false);
      }
      CVPixelBufferRelease(retained);
      pthread_mutex_lock(&r->ingress);
      r->pending_video--;
      pthread_mutex_unlock(&r->ingress);
    }
  });
  pthread_mutex_unlock(&r->ingress);
}

static bool append_audio_buffer(recording_t *r, const void *data, size_t size,
                                  const size_t *sizes, size_t count, int64_t pts,
                                  int start_trim, int end_trim, const int64_t *timestamps) {
  char prior_error[512];
  if (!copy_error(r, prior_error)) {
    return false;
  }
  uint8_t *copy = malloc(size);
  memcpy(copy, data, size);
  CMBlockBufferRef block = NULL;
  OSStatus status = CMBlockBufferCreateWithMemoryBlock(kCFAllocatorDefault,
      copy, size, kCFAllocatorDefault, NULL, 0, size, 0, &block);
  if (status != noErr) {
    free(copy);
    set_error(r, "create compressed AAC block failed");
    return false;
  }
  CMTime desired = CMTimeConvertScale(CMTimeMake(pts, 1000000), r->sample_rate,
                                      kCMTimeRoundingMethod_RoundHalfAwayFromZero);
  CMTime priming = CMTimeMake(start_trim, r->sample_rate);
  CMSampleTimingInfo timing = {CMTimeMake(1024, r->sample_rate),
                              CMTimeSubtract(desired, priming), kCMTimeInvalid};
  CMSampleTimingInfo individual[3];
  if (timestamps != NULL) {
    for (size_t i = 0; i < count; i++) {
      individual[i] = timing;
      individual[i].presentationTimeStamp = CMTimeSubtract(
          CMTimeMake(timestamps[i], 1000000), priming);
    }
  }
  CMSampleBufferRef sample = NULL;
  status = CMSampleBufferCreateReady(kCFAllocatorDefault, block, r->audio_format,
      count, timestamps != NULL ? count : 1, timestamps != NULL ? individual : &timing,
      count, sizes, &sample);
  CFRelease(block);
  if (status != noErr) {
    set_error(r, "create compressed AAC sample failed");
    return false;
  }
  CFDictionaryRef trim = CMTimeCopyAsDictionary(priming, kCFAllocatorDefault);
  CMSetAttachment(sample, kCMSampleBufferAttachmentKey_TrimDurationAtStart,
                  trim, kCMAttachmentMode_ShouldPropagate);
  CFRelease(trim);
  if (end_trim > 0) {
    trim = CMTimeCopyAsDictionary(CMTimeMake(end_trim, r->sample_rate), kCFAllocatorDefault);
    CMSetAttachment(sample, kCMSampleBufferAttachmentKey_TrimDurationAtEnd,
                    trim, kCMAttachmentMode_ShouldPropagate);
    CFRelease(trim);
  }
  CMSampleBufferSetOutputPresentationTimeStamp(sample, desired);
  bool success = append_sample(r, r->audio, sample);
  CFRelease(sample);
  return success;
}

static bool track_audio_timestamp(recording_t *r, int64_t relative) {
  if (r->audio_packet_count == 0) {
    return true;
  }
  int64_t elapsed = CMTimeConvertScale(CMTimeMake((int64_t)r->audio_packet_count * 1024,
      r->sample_rate), 1000000, kCMTimeRoundingMethod_RoundHalfAwayFromZero).value;
  int64_t expected = r->first_audio_pts + elapsed + r->audio_gap_duration;
  // AAC sample tables quantize packet duration. Keep ordinary source-clock
  // jitter within one AU; preserve larger accumulated gaps with empty edits.
  int64_t tolerance = 1024000000 / r->sample_rate;
  if (relative < expected - tolerance) {
    set_error(r, "AAC source timestamp overlaps the recorded audio by more than one packet");
    return false;
  }
  if (relative > expected + tolerance) {
    if (r->audio_gap_count == sizeof(r->audio_gaps) / sizeof(r->audio_gaps[0])) {
      set_error(r, "recording audio timestamp gap limit exceeded");
      return false;
    }
    r->audio_gaps[r->audio_gap_count++] = (audio_gap_t){expected, relative - expected};
    r->audio_gap_duration += relative - expected;
  }
  return true;
}

static void correct_audio_timeline(recording_t *r, int64_t end_pts) {
  char prior_error[512];
  if (r->audio_gap_count == 0 || !copy_error(r, prior_error)) {
    return;
  }
  NSURL *output = r->writer.outputURL;
  NSString *name = [NSString stringWithFormat:@".%@.recording-%@.mp4",
      output.lastPathComponent, NSUUID.UUID.UUIDString];
  NSURL *temporary = [[output URLByDeletingLastPathComponent] URLByAppendingPathComponent:name];
  NSFileManager *files = [NSFileManager defaultManager];
  NSError *error = nil;
  if (![files copyItemAtURL:output toURL:temporary error:&error]) {
    // A failed copy may leave our partial destination. A name collision is the
    // one failure that does not confer ownership of that destination.
    if (![error.domain isEqualToString:NSCocoaErrorDomain] ||
        error.code != NSFileWriteFileExistsError) {
      [files removeItemAtURL:temporary error:NULL];
    }
    set_error(r, error.localizedDescription.UTF8String);
    return;
  }
  AVMutableMovie *movie = [[AVMutableMovie alloc] initWithURL:temporary options:nil error:&error];
  bool success = movie != nil;
  if (success) {
    movie.timescale = 1000000;
    AVMutableMovieTrack *audio = (AVMutableMovieTrack *)[movie tracksWithMediaType:AVMediaTypeAudio].firstObject;
    if (audio == nil) {
      set_error(r, "completed MP4 has no AAC track to correct");
      success = false;
    } else {
      audio.timescale = r->sample_rate;
      CMTime end = CMTimeMake(end_pts, 1000000);
      for (size_t i = 0; i < r->audio_gap_count; i++) {
        audio_gap_t gap = r->audio_gaps[i];
        if (gap.start >= end_pts) {
          break;
        }
        [audio insertEmptyTimeRange:CMTimeRangeMake(CMTimeMake(gap.start, 1000000),
                                                     CMTimeMake(gap.duration, 1000000))];
      }
      CMTime audio_end = CMTimeRangeGetEnd(audio.timeRange);
      if (CMTimeCompare(audio_end, end) > 0) {
        [audio removeTimeRange:CMTimeRangeMake(end, CMTimeSubtract(audio_end, end))];
      }
      success = [movie writeMovieHeaderToURL:temporary fileType:AVFileTypeMPEG4
          options:AVMovieWritingAddMovieHeaderToDestination error:&error];
    }
  }
  [movie release];
  if (success) {
    success = rename(temporary.path.fileSystemRepresentation, output.path.fileSystemRepresentation) == 0;
    if (!success) {
      set_error(r, strerror(errno));
    }
  } else if (error != nil) {
    set_error(r, error.localizedDescription.UTF8String);
  }
  if (!success) {
    [files removeItemAtURL:temporary error:NULL];
  }
}

static void flush_first_audio(recording_t *r) {
  if (r->first_audio == nil) {
    return;
  }
  while (r->first_audio_count < 3) {
    r->first_audio_timestamps[r->first_audio_count] =
        r->first_audio_timestamps[r->first_audio_count - 1] + 1024000000 / r->sample_rate;
    r->first_audio_sizes[r->first_audio_count++] = r->last_audio.length;
    [r->first_audio appendData:r->last_audio];
  }
  // AVAssetWriter applies the conventional 2112-sample AAC priming interval.
  // Explicitly describe it across three compressed AUs to keep the original
  // first AU at its requested presentation time, including 1-2 AU recordings.
  append_audio_buffer(r, r->first_audio.bytes, r->first_audio.length,
                      r->first_audio_sizes, 3, r->first_audio_pts, 2112, 0, r->first_audio_timestamps);
  [r->first_audio release];
  r->first_audio = nil;
}

bool recording_append_audio(recording_t *r, const uint8_t *packet,
                            size_t size, int64_t pts, char error[512]) {
  pthread_mutex_lock(&r->ingress);
  if (!r->accepting) {
    snprintf(error, 512, "recording has stopped");
    pthread_mutex_unlock(&r->ingress);
    return false;
  }
  if (r->audio == nil || size == 0 || size > 1024 * 1024 ||
      r->pending_audio >= 256 || r->pending_audio_bytes + size > 4 * 1024 * 1024) {
    if (r->error[0] == '\0') {
      snprintf(r->error, 512, "AAC unavailable, invalid packet, or recording audio queue full");
    }
  }
  if (r->error[0] != '\0') {
    snprintf(error, 512, "%s", r->error);
    pthread_mutex_unlock(&r->ingress);
    return false;
  }
  if (pts < r->source_pts) {
    pthread_mutex_unlock(&r->ingress);
    return true;
  }
  uint8_t *copy = malloc(size);
  memcpy(copy, packet, size);
  r->pending_audio++;
  r->pending_audio_bytes += size;
  dispatch_async(r->queue, ^{
    @autoreleasepool {
      char prior_error[512];
      int64_t relative = pts - r->source_pts;
      if (!copy_error(r, prior_error)) {
        // The first failure is persistent; queued packets only release ownership.
      } else if (relative <= r->last_audio_pts) {
        set_error(r, "AAC timestamps must increase");
      } else if (!track_audio_timestamp(r, relative)) {
        // Retain the first timestamp error without processing further packets.
      } else {
        [r->last_audio release];
        r->last_audio = [[NSData alloc] initWithBytes:copy length:size];
        r->last_audio_pts = relative;
        r->audio_packet_count++;
        if (r->first_audio_count < 3) {
          if (r->first_audio == nil) {
            r->first_audio = [[NSMutableData alloc] init];
            r->first_audio_pts = relative;
          }
          [r->first_audio appendBytes:copy length:size];
          r->first_audio_timestamps[r->first_audio_count] = relative;
          r->first_audio_sizes[r->first_audio_count++] = size;
          if (r->first_audio_count == 3) {
            flush_first_audio(r);
          }
        } else {
          append_audio_buffer(r, copy, size, &size, 1, relative, 0, 0, NULL);
        }
      }
      free(copy);
      pthread_mutex_lock(&r->ingress);
      r->pending_audio--;
      r->pending_audio_bytes -= size;
      pthread_mutex_unlock(&r->ingress);
    }
  });
  pthread_mutex_unlock(&r->ingress);
  return true;
}

bool recording_stop(recording_t *r, int64_t end_pts, char error[512]) {
  pthread_mutex_lock(&r->ingress);
  r->accepting = false;
  pthread_mutex_unlock(&r->ingress);
  dispatch_sync(r->queue, ^{
    @autoreleasepool {
      if (r->finished) {
        return;
      }
      int64_t end = end_pts == INT64_MIN
          ? r->latest_pts - r->source_pts + 1000000 / r->fps
          : end_pts - r->source_pts;
      if (end < 1) {
        end = 1;
      }
      int64_t tail = end - 1000000 / r->fps;
      if (tail <= r->last_video_pts) {
        tail = r->last_video_pts + 1;
      }
      if (tail < end) {
        encode_frame(r, r->latest, tail, end - tail, false);
      }
      if (r->writer.status == AVAssetWriterStatusWriting) {
        if (r->last_audio != nil) {
          flush_first_audio(r);
          // Three copied completion AUs supply 3072 samples; trim the unused
          // 960 (3072 - 2112). Container edits hide all completion copies.
          NSMutableData *padding = [NSMutableData data];
          for (int i = 0; i < 3; i++) {
            [padding appendData:r->last_audio];
          }
          size_t sizes[] = {r->last_audio.length, r->last_audio.length, r->last_audio.length};
          int end_trim = 960 + (r->audio_packet_count < 3 ? (3 - (int)r->audio_packet_count) * 1024 : 0);
          append_audio_buffer(r, padding.bytes, padding.length, sizes, 3,
              r->last_audio_pts + 1024000000 / r->sample_rate, 0, end_trim, NULL);
        }
        [r->writer endSessionAtSourceTime:CMTimeMake(end, 1000000)];
        [r->video markAsFinished];
        [r->audio markAsFinished];
        dispatch_semaphore_t complete = dispatch_semaphore_create(0);
        [r->writer finishWritingWithCompletionHandler:^{
          dispatch_semaphore_signal(complete);
        }];
        dispatch_semaphore_wait(complete, DISPATCH_TIME_FOREVER);
        dispatch_release(complete);
      }
      if (r->writer.status != AVAssetWriterStatusCompleted) {
        writer_error(r, "finish MP4 writer");
      }
      if (r->writer.status == AVAssetWriterStatusCompleted) {
        correct_audio_timeline(r, end);
      }
      r->finished = true;
      destroy_resources(r);
    }
  });
  return copy_error(r, error);
}
