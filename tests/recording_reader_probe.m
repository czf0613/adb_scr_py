#import <AVFoundation/AVFoundation.h>
#include <stdio.h>

int main(int argc, char **argv) {
  if (argc != 2) {
    return 2;
  }
  @autoreleasepool {
    AVURLAsset *asset = [AVURLAsset assetWithURL:[NSURL fileURLWithPath:@(argv[1])]];
    AVAssetTrack *track = [asset tracksWithMediaType:AVMediaTypeAudio].firstObject;
    if (track == nil) {
      return 3;
    }
    NSMutableArray *empty = [NSMutableArray array];
    for (AVAssetTrackSegment *segment in track.segments) {
      if (segment.empty) {
        CMTimeRange range = segment.timeMapping.target;
        [empty addObject:@[@(CMTimeGetSeconds(range.start)), @(CMTimeGetSeconds(range.duration))]];
      }
    }
    NSError *error = nil;
    AVAssetReader *reader = [AVAssetReader assetReaderWithAsset:asset error:&error];
    AVAssetReaderTrackOutput *output = [AVAssetReaderTrackOutput assetReaderTrackOutputWithTrack:track outputSettings:nil];
    [reader addOutput:output];
    if (![reader startReading]) {
      return 4;
    }
    NSMutableArray *ranges = [NSMutableArray array];
    CMSampleBufferRef sample = NULL;
    while ((sample = [output copyNextSampleBuffer]) != NULL) {
      if (CMSampleBufferGetNumSamples(sample) > 0) {
        CMTime start = CMSampleBufferGetOutputPresentationTimeStamp(sample);
        CMTime duration = CMSampleBufferGetOutputDuration(sample);
        [ranges addObject:@[@(CMTimeGetSeconds(start)), @(CMTimeGetSeconds(duration))]];
      }
      CFRelease(sample);
    }
    if (reader.status != AVAssetReaderStatusCompleted) {
      return 5;
    }
    NSData *json = [NSJSONSerialization dataWithJSONObject:@{@"empty": empty, @"ranges": ranges}
                                                  options:0 error:&error];
    fwrite(json.bytes, 1, json.length, stdout);
  }
  return 0;
}
