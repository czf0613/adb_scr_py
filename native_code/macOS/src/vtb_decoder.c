#include "vtb_decoder.h"
#include "vtb_helper.h"
#include <CoreFoundation/CoreFoundation.h>
#include <VideoToolbox/VideoToolbox.h>
#include <dispatch/dispatch.h>
#include <stdio.h>
#include <stdlib.h>

/**
 * @brief
 * 真正的VTB解码器结构体，不对外暴露。只要struct正确产生，除了current_frame
 * 其他字段都不会为NULL
 */
typedef struct {
  CMVideoFormatDescriptionRef format;
  VTDecompressionSessionRef session;
  CVPixelBufferRef current_frame;
  dispatch_queue_t queue;
} vtb_decoder_t;

int32_t vtb_create_decoder(const uint8_t *sps_and_pps, size_t sps_and_pps_size,
                           void **out_decoder, int32_t *out_width,
                           int32_t *out_height) {
  void *decoder_addr = *out_decoder;
  if (decoder_addr != NULL) {
    printf("decoder_addr is not NULL, cannot allocate memory for decoder\n");
    return 1;
  }

  // 分割SPS和PPS
  uint8_t *sps = NULL, *pps = NULL;
  size_t sps_size = 0, pps_size = 0;
  if (!vth_cut_sps_pps(sps_and_pps, sps_and_pps_size, &sps, &sps_size, &pps,
                       &pps_size)) {
    printf("cut_sps_pps failed\n");
    return 2;
  }

  // 解析sps/pps，拿到宽度和高度
  CMVideoFormatDescriptionRef format = NULL;
  const uint8_t *parameter_set[] = {sps, pps};
  size_t parameter_set_sizes[] = {sps_size, pps_size};
  OSStatus status = CMVideoFormatDescriptionCreateFromH264ParameterSets(
      kCFAllocatorDefault, 2, parameter_set, parameter_set_sizes, 4, &format);
  if (status != noErr || format == NULL) {
    printf("parse sps and pps failed\n");
    return 3;
  }
  CMVideoDimensions dim = CMVideoFormatDescriptionGetDimensions(format);
  *out_width = dim.width;
  *out_height = dim.height;

  // 创建解码器
  VTDecompressionSessionRef session = NULL;
  CFStringRef keys[] = {
      kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder,
      kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder};
  CFBooleanRef values[] = {kCFBooleanTrue, kCFBooleanTrue};

  CFDictionaryRef decoder_specification = CFDictionaryCreate(
      kCFAllocatorDefault, (const void **)keys, (const void **)values, 2,
      &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
  status = VTDecompressionSessionCreate(
      kCFAllocatorDefault, format, decoder_specification, NULL, NULL, &session);
  CFRelease(decoder_specification);

  if (status != noErr || session == NULL) {
    printf("create decoder session failed\n");
    // 释放format
    CFRelease(format);
    return 4;
  }
  VTSessionSetProperty(session, kVTDecompressionPropertyKey_RealTime,
                       kCFBooleanTrue);

  // 创建对象准备返回
  vtb_decoder_t *vtb_decoder = malloc(sizeof(vtb_decoder_t));
  vtb_decoder->format = format;
  vtb_decoder->session = session;
  vtb_decoder->current_frame = NULL;
  vtb_decoder->queue =
      dispatch_queue_create("com.adb_scr.vtb_decoder", DISPATCH_QUEUE_SERIAL);
  *out_decoder = vtb_decoder;
  return 0;
}

void vtb_destroy_decoder(void **decoder) {
  void *decoder_addr = *decoder;
  if (decoder_addr == NULL) {
    return;
  }

  vtb_decoder_t *vtb_decoder = (vtb_decoder_t *)decoder_addr;
  VTDecompressionSessionWaitForAsynchronousFrames(vtb_decoder->session);
  VTDecompressionSessionInvalidate(vtb_decoder->session);
  CFRelease(vtb_decoder->session);
  CFRelease(vtb_decoder->format);

  if (vtb_decoder->current_frame != NULL) {
    CVPixelBufferRelease(vtb_decoder->current_frame);
  }

  dispatch_release(vtb_decoder->queue);
  free(vtb_decoder);
  *decoder = NULL;
}

bool vtb_enqueue_frame(void *decoder, const uint8_t *frame, size_t frame_size,
                       int64_t pts) {
  if (frame_size <= 4 || decoder == NULL) {
    return false;
  }
  vtb_decoder_t *vtb_decoder = (vtb_decoder_t *)decoder;

  // 预处理NALU
  size_t nalu_size = 0;
  const uint8_t *nalu = vth_reformat_nalu(frame, frame_size, &nalu_size);
  CMBlockBufferRef block_buffer = NULL;
  OSStatus status = CMBlockBufferCreateWithMemoryBlock(
      kCFAllocatorDefault, (void *)nalu, nalu_size, kCFAllocatorDefault, NULL,
      0, nalu_size, 0, &block_buffer);
  if (status != noErr || block_buffer == NULL) {
    printf("create block buffer failed\n");
    // 这个是特殊情况需要free它，正常情况下它会被VideoToolbox自己处理
    CFAllocatorDeallocate(kCFAllocatorDefault, (void *)nalu);
    return false;
  }

  // 准备解码数据包
  CMTime time = CMTimeMake(pts, 1000000);
  CMSampleTimingInfo timing_info = {.duration = kCMTimeInvalid,
                                    .presentationTimeStamp = time,
                                    .decodeTimeStamp = kCMTimeInvalid};
  CMSampleBufferRef sample_buffer = NULL;
  status = CMSampleBufferCreateReady(kCFAllocatorDefault, block_buffer,
                                     vtb_decoder->format, 1, 1, &timing_info, 1,
                                     &nalu_size, &sample_buffer);
  if (status != noErr || sample_buffer == NULL) {
    printf("create sample buffer failed\n");
    // nalu这个已经被自动处理了，因为创建block的时候指定了block
    // allocator为kCFAllocatorDefault，它会自动去free它
    CFRelease(block_buffer);
    return false;
  }

  // 提交任务
  status = VTDecompressionSessionDecodeFrameWithOutputHandler(
      vtb_decoder->session, sample_buffer,
      kVTDecodeFrame_EnableAsynchronousDecompression, NULL,
      ^(OSStatus status, VTDecodeInfoFlags infoFlags,
        CVImageBufferRef _Nullable imageBuffer, CMTime presentationTimeStamp,
        CMTime presentationDuration) {
        if (status != noErr || imageBuffer == NULL) {
          printf("decode frame failed\n");
          return;
        }

        // 保留引用，因为回调是异步的
        CVPixelBufferRef retainedBuffer = CVPixelBufferRetain(imageBuffer);
        dispatch_async(vtb_decoder->queue, ^{
          if (vtb_decoder->current_frame != NULL) {
            CVPixelBufferRelease(vtb_decoder->current_frame);
          }

          vtb_decoder->current_frame = retainedBuffer;
        });
      });

  if (status != noErr) {
    printf("enqueue frame sample buffer failed\n");
    CFRelease(block_buffer);
    CFRelease(sample_buffer);
    return false;
  }

  CFRelease(block_buffer);
  CFRelease(sample_buffer);
  return true;
}

bool vtb_current_frame_bgra8(void *decoder, uint8_t **out_data,
                             size_t *out_width, size_t *out_height) {
  if (*out_data != NULL || decoder == NULL) {
    return false;
  }
  vtb_decoder_t *vtb_decoder = (vtb_decoder_t *)decoder;

  __block bool exec_result = false;
  dispatch_sync(vtb_decoder->queue, ^{
    if (vtb_decoder->current_frame == NULL) {
      exec_result = false;
      return;
    }

    // pixel buffer里面的是NV12格式，需要调用一个加速库来转换为bgra8
    exec_result = vth_nv12_to_bgra8(vtb_decoder->current_frame, out_data,
                                    out_width, out_height);
  });

  return exec_result;
}