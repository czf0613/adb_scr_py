#ifndef VTB_DECODER_H
#define VTB_DECODER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/**
 * @brief 创建VTB解码器
 *
 * @param sps_and_pps SPS和PPS参数指针
 * @param decoder 解码器结构体指针（透明类型），必须传入NULL进来，由内部进行分配
 * @param out_width 输出宽度指针
 * @param out_height 输出高度指针
 * @return 成功时为0
 */
int32_t vtb_create_decoder(const uint8_t *sps_and_pps, size_t sps_and_pps_size,
                           void **out_decoder, int32_t *out_width,
                           int32_t *out_height);

/**
 * @brief
 * 销毁VTB解码器，销毁之后解码器结构体指针会变成NULL。这个方法不保证线程安全，
 * 调用方需要自己确保在合适的时机调用这个方法。
 *
 * @param decoder 解码器结构体指针（透明类型）
 */
void vtb_destroy_decoder(void **decoder);

/**
 * @brief 入队解码帧（非阻塞），这个方法会迅速返回
 *
 * @param decoder 解码器结构体指针（透明类型）
 * @param frame 解码帧指针
 * @param pts 解码帧的PTS值
 * @return 成功时为true
 */
bool vtb_enqueue_frame(void *decoder, const uint8_t *frame, size_t frame_size,
                       int64_t pts);

/**
 * @brief 获取当前解码帧的BGRA8格式数据（非阻塞），这个方法会迅速返回
 *
 * @param decoder 解码器结构体指针（透明类型）
 * @param out_data
 * 输出数据指针指针（必须塞一个NULL进来，方法会自动分配内存然后返回），调用方用完之后，自己需要free释放内存
 * @param out_size 输出数据大小指针
 * @return 成功时为true
 */
bool vtb_current_frame_bgra8(void *decoder, uint8_t **out_data,
                             size_t *out_width, size_t *out_height);

#endif