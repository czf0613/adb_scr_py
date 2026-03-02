#ifndef VTB_HELPER_H
#define VTB_HELPER_H

#include <VideoToolbox/VideoToolbox.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/**
 * @brief
 * 从SPS和PPS中提取SPS和PPS，输出的两个指针不需要处理，本身就是指向输入的data而已
 *
 * @param data SPS和PPS数据
 * @param size SPS和PPS数据大小
 * @param out_sps 输出SPS数据
 * @param out_sps_size 输出SPS数据大小
 * @param out_pps 输出PPS数据
 * @param out_pps_size 输出PPS数据大小
 * @return 是否切割成功
 */
bool vth_cut_sps_pps(const uint8_t *data, size_t size, uint8_t **out_sps,
                     size_t *out_sps_size, uint8_t **out_pps,
                     size_t *out_pps_size);

/**
 * @brief
 * 重新格式化NALU，按照VideoToolBox的要求进行处理
 *
 * @param nalu NALU数据
 * @param nalu_size NALU数据大小
 * @return
 * 重新格式化后的NALU数据指针（指向输入的nalu），这个指针不需要释放，VideoToolbox会自动处理
 */
const uint8_t *vth_reformat_nalu(const uint8_t *nalu, size_t nalu_size,
                                 size_t *out_size);

/**
 * @brief
 * 将NV12格式的CVPixelBufferRef转换为BGRA8格式
 *
 * @param frame NV12格式的CVPixelBufferRef
 * @param out_bgra8
 * 输出BGRA8数据指针，必须给NULL进来，让函数内部分配内存，用完之后调用方自己free它
 * @param out_width 输出宽度
 * @param out_height 输出高度
 * @return 是否转换成功
 */
bool vth_nv12_to_bgra8(CVPixelBufferRef frame, uint8_t **out_bgra8,
                       size_t *out_width, size_t *out_height);

#endif
