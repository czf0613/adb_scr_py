// Synthetic device-free queue ownership and failure-draining regression probe.
#include "recording.m"
#include <assert.h>
#include <objc/runtime.h>

static int copy_failure;
static NSURL *copy_target;

static BOOL fail_copy(id self, SEL selector, NSURL *source, NSURL *destination, NSError **error) {
  (void)self;
  (void)selector;
  (void)source;
  copy_target = [destination retain];
  [@"partial-or-existing" writeToURL:destination atomically:NO
                           encoding:NSUTF8StringEncoding error:NULL];
  *error = [NSError errorWithDomain:NSCocoaErrorDomain
      code:copy_failure == 1 ? NSFileWriteOutOfSpaceError : NSFileWriteFileExistsError
      userInfo:nil];
  return NO;
}

int main(int argc, char **argv) {
  assert(argc == 3);
  @autoreleasepool {
    CVPixelBufferRef frame = NULL;
    NSDictionary *attributes = @{(id)kCVPixelBufferIOSurfacePropertiesKey: @{}};
    assert(CVPixelBufferCreate(kCFAllocatorDefault, 64, 64,
        kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
        (CFDictionaryRef)attributes, &frame) == kCVReturnSuccess);
    assert(CVPixelBufferLockBaseAddress(frame, 0) == kCVReturnSuccess);
    memset(CVPixelBufferGetBaseAddressOfPlane(frame, 0), 16,
           CVPixelBufferGetBytesPerRowOfPlane(frame, 0) * 64);
    memset(CVPixelBufferGetBaseAddressOfPlane(frame, 1), 128,
           CVPixelBufferGetBytesPerRowOfPlane(frame, 1) * 32);
    CVPixelBufferUnlockBaseAddress(frame, 0);
    const uint8_t config[] = {0x11, 0x90};
    char error[512] = {0};
    recording_t *r = recording_create(frame, argv[1], config, 2, 0, 30, error);
    assert(r != NULL);
    CFTypeRef hardware = NULL;
    assert(VTSessionCopyProperty(r->encoder,
        kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,
        kCFAllocatorDefault, &hardware) == noErr);
    assert(CFEqual(hardware, kCFBooleanTrue));
    CFRelease(hardware);
    dispatch_semaphore_t gate = dispatch_semaphore_create(0);
    dispatch_async(r->queue, ^{
      dispatch_semaphore_wait(gate, DISPATCH_TIME_FOREVER);
    });
    for (int i = 1; i <= 10000; i++) {
      recording_submit_frame(r, frame, i);
    }
    assert(r->pending_video == 2);
    assert(r->latest_pts == 10000);
    NSData *packet = [NSData dataWithContentsOfFile:@(argv[2])];
    for (int i = 0; i < 256; i++) {
      assert(recording_append_audio(r, packet.bytes, packet.length,
          (int64_t)i * 1024000000 / 48000, error));
    }
    assert(!recording_append_audio(r, packet.bytes, packet.length, 9999999, error));
    assert(strstr(error, "queue full") != NULL);
    dispatch_semaphore_signal(gate);
    dispatch_sync(r->queue, ^{});
    // Every queued submission releases ownership after the first failure.
    assert(r->pending_video == 0 && r->pending_audio == 0);
    assert(r->audio_packet_count == 0 && r->first_audio == nil);
    assert(r->last_video_pts == 0);
    assert(!recording_stop(r, 1000000, error));
    assert(r->finished && r->writer == nil && r->encoder == NULL);
    recording_release(r);
    Method copy_method = class_getInstanceMethod(NSFileManager.class,
        @selector(copyItemAtURL:toURL:error:));
    IMP original_copy = method_setImplementation(copy_method, (IMP)fail_copy);
    for (copy_failure = 1; copy_failure <= 2; copy_failure++) {
      NSString *path = [NSString stringWithFormat:@"%s-copy-%d.mp4", argv[1], copy_failure];
      r = recording_create(frame, path.UTF8String, NULL, 0, 0, 30, error);
      assert(r != NULL);
      NSData *original = [NSData dataWithContentsOfFile:path];
      r->audio_gap_count = 1;
      correct_audio_timeline(r, 1000000);
      assert([original isEqualToData:[NSData dataWithContentsOfFile:path]]);
      bool exists = [[NSFileManager defaultManager] fileExistsAtPath:copy_target.path];
      assert(exists == (copy_failure == 2));
      [[NSFileManager defaultManager] removeItemAtURL:copy_target error:NULL];
      [copy_target release];
      copy_target = nil;
      assert(!recording_stop(r, 1000000, error));
      recording_release(r);
    }
    method_setImplementation(copy_method, original_copy);
    CVPixelBufferRelease(frame);
    dispatch_release(gate);
  }
  return 0;
}
