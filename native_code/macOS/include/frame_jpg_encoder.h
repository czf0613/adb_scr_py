#ifndef FRAME_JPG_ENCODER_H
#define FRAME_JPG_ENCODER_H

#include <CoreFoundation/CoreFoundation.h>
#include <CoreVideo/CoreVideo.h>
#include <stdbool.h>
#include <stdint.h>

typedef struct frame_jpg_encoder frame_jpg_encoder_t;

typedef struct {
  int quality;
  double scale;
  bool has_roi;
  int64_t x, y, width, height;
} frame_jpg_options_t;

typedef enum {
  FRAME_JPG_OK,
  FRAME_JPG_INVALID_ROI,
  FRAME_JPG_INVALID_SIZE,
  FRAME_JPG_FAILED,
} frame_jpg_status_t;

frame_jpg_encoder_t *frame_jpg_encoder_create(void);
void frame_jpg_encoder_destroy(frame_jpg_encoder_t *encoder);
frame_jpg_status_t frame_jpg_encode(frame_jpg_encoder_t *encoder,
                                  CVPixelBufferRef frame,
                                  const frame_jpg_options_t *options,
                                  CFDataRef *out_data);

#endif
