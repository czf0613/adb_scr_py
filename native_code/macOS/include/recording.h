#ifndef ADB_SCR_RECORDING_H
#define ADB_SCR_RECORDING_H

#include <CoreVideo/CoreVideo.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct recording recording_t;
recording_t *recording_create(CVPixelBufferRef first_frame, const char *path,
                              const uint8_t *audio_config, size_t config_size,
                              int64_t source_pts, int fps, char error[512]);
void recording_retain(void *recording);
void recording_release(void *recording);
void recording_submit_frame(void *recording, CVPixelBufferRef frame, int64_t pts);
bool recording_append_audio(recording_t *recording, const uint8_t *packet,
                            size_t size, int64_t pts, char error[512]);
bool recording_stop(recording_t *recording, int64_t end_pts, char error[512]);

#endif
