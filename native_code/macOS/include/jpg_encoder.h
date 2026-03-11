#ifndef JPG_ENCODER_H
#define JPG_ENCODER_H

#include <stdbool.h>
#include <stdint.h>

/**
 * @brief 编码BGRA8格式的图像为JPEG格式
 *
 * @param width 图像宽度
 * @param height 图像高度
 * @param bgra8 输入的BGRA8格式图像数据指针
 * @param quality JPEG编码质量（0-100）
 * @param jpg_size 输出的JPEG图像数据大小指针
 * @param jpg 输出的JPEG图像数据指针（调用方提前分配好了足够的内存空间）
 * @return 编码是否成功
 */
bool encode_bgra8_to_jpg(uint32_t width, uint32_t height, const uint8_t *bgra8,
                         uint8_t quality, uint32_t *jpg_size, uint8_t *jpg);

#endif
