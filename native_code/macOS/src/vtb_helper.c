#include "vtb_helper.h"
#include <Accelerate/Accelerate.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/param.h>

bool vth_cut_sps_pps(const uint8_t *data, size_t size, uint8_t **out_sps,
                     size_t *out_sps_size, uint8_t **out_pps,
                     size_t *out_pps_size) {
  if (size <= 8) {
    return false;
  }

  // 前4个肯定是0001，所以至少从第4位开始往后找
  // 因为SPS和PPS至少都有一位，所以范围从5到n-4
  // 为了防止错误数据影响，最多搜索512个字节
  for (size_t i = 5; i < MIN(size - 4, 512); ++i) {
    if (data[i] == 0x00 && data[i + 1] == 0x00 && data[i + 2] == 0x00 &&
        data[i + 3] == 0x01) {
      *out_sps = (uint8_t *)&(data[4]);
      *out_sps_size = i - 4;

      *out_pps = (uint8_t *)&(data[i + 4]);
      *out_pps_size = size - i - 4;
      return true;
    }
  }

  return false;
}

// 判断NAL类型
static inline uint8_t get_nal_type(uint8_t header_byte) {
  return header_byte & 0x1F;
}

static inline void write_annexb(uint8_t *out_data, uint32_t payload_size) {
  // macOS arm64肯定是小端序，所以可以写死
  out_data[0] = (payload_size >> 24) & 0xFF;
  out_data[1] = (payload_size >> 16) & 0xFF;
  out_data[2] = (payload_size >> 8) & 0xFF;
  out_data[3] = payload_size & 0xFF;
}

const uint8_t *vth_reformat_nalu(const uint8_t *nalu, size_t nalu_size,
                                 size_t *out_size) {
  // 这个函数其实还有一个要做的事情，就是把混合的NALU给拆掉
  // 某些非标准的编码器，会喜欢把SEI和IDR放在一起，然后VideoToolbox解码就炸掉了
  // SEI帧其实可以忽略，丢掉问题不大
  uint8_t nal_type = get_nal_type(nalu[4]);
  uint8_t *data = NULL;

  if (nal_type != 6) {
    // 普通的帧，直接复制后返回
    data = CFAllocatorAllocate(kCFAllocatorDefault, nalu_size, 0);
    memcpy(data, nalu, nalu_size);
    *out_size = nalu_size;
  } else {
    // 处理SEI+IDR混合的情况，SEI帧一般比较短，找前512个字节基本上就可以确定了
    size_t cut_position = 4;
    for (size_t i = 5; i < MIN(512, nalu_size - 4); ++i) {
      if (nalu[i] == 0x00 && nalu[i + 1] == 0x00 && nalu[i + 2] == 0x00 &&
          nalu[i + 3] == 0x01) {
        // 找到IDR帧了，直接从这里截断
        cut_position = i;
        break;
      }
    }
    // 真实的大小就是截断位置之后的所有数据
    *out_size = nalu_size - cut_position;
    data = CFAllocatorAllocate(kCFAllocatorDefault, *out_size, 0);
    memcpy(data, nalu + cut_position, *out_size);
  }

  write_annexb(data, *out_size - 4);
  return data;
}

// 跟yuv转换有关的一些全局变量，初始化一次就行
static vImage_YpCbCrToARGB convertion_info;
static dispatch_once_t once_token;
static const uint8_t permute_map[4] = {3, 2, 1, 0}; // ARGB to BGRA

bool vth_nv12_to_bgra8(CVPixelBufferRef frame, uint8_t **out_bgra8,
                       size_t *out_width, size_t *out_height) {
  if (frame == NULL || *out_bgra8 != NULL) {
    return false;
  }

  OSType pixel_format = CVPixelBufferGetPixelFormatType(frame);
  if (pixel_format != kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange) {
    printf("pixel format is not NV12\n");
    return false;
  }

  // 初始化转换矩阵
  dispatch_once(&once_token, ^{
    vImage_YpCbCrPixelRange pixel_range = {.Yp_bias = 16,
                                           .CbCr_bias = 128,
                                           .YpRangeMax = 235,
                                           .CbCrRangeMax = 240,
                                           .YpMax = 235,
                                           .YpMin = 16,
                                           .CbCrMax = 240,
                                           .CbCrMin = 16};

    vImageConvert_YpCbCrToARGB_GenerateConversion(
        kvImage_YpCbCrToARGBMatrix_ITU_R_709_2, &pixel_range, &convertion_info,
        kvImage420Yp8_CbCr8, kvImageARGB8888, kvImageNoFlags);
  });

  // 读取nv12数据
  CVPixelBufferLockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
  size_t frame_width = CVPixelBufferGetWidth(frame),
         frame_height = CVPixelBufferGetHeight(frame);
  void *y = CVPixelBufferGetBaseAddressOfPlane(frame, 0),
       *uv = CVPixelBufferGetBaseAddressOfPlane(frame, 1);

  vImage_Buffer y_buffer = {.data = y,
                            .height = frame_height,
                            .width = frame_width,
                            .rowBytes =
                                CVPixelBufferGetBytesPerRowOfPlane(frame, 0)},
                uv_buffer = {.data = uv,
                             .height = frame_height / 2,
                             .width = frame_width / 2,
                             .rowBytes =
                                 CVPixelBufferGetBytesPerRowOfPlane(frame, 1)};

  // 准备好输出的buffer
  *out_bgra8 = malloc(frame_width * frame_height * 4);
  vImage_Buffer output_buffer = {.data = *out_bgra8,
                                 .height = frame_height,
                                 .width = frame_width,
                                 .rowBytes = frame_width * 4};

  vImage_Error result = vImageConvert_420Yp8_CbCr8ToARGB8888(
      &y_buffer, &uv_buffer, &output_buffer, &convertion_info, permute_map, 255,
      kvImageNoFlags);
  if (result != kvImageNoError) {
    printf("vImageConvert_420Yp8_CbCr8ToARGB8888 failed, result: %ld\n",
           result);

    // 清理掉临时分配的对象
    free(*out_bgra8);
    *out_bgra8 = NULL;
    CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
    return false;
  }

  // 转换成功，返回数据
  *out_width = frame_width;
  *out_height = frame_height;
  CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
  return true;
}