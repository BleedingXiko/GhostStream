/**
 * GhostStream JavaScript SDK
 * 
 * @example
 * ```typescript
 * import { GhostStreamClient, TranscodeStatus } from 'ghoststream-sdk';
 * 
 * const client = new GhostStreamClient('192.168.4.2:8765');
 * 
 * const job = await client.transcode({
 *   source: 'http://pi:5000/video.mkv',
 *   resolution: '720p'
 * });
 * 
 * console.log(job.streamUrl);
 * ```
 */

export { GhostStreamClient } from './client';
export { TranscodeJob, TranscodeStatus, TranscodeOptions, ClientConfig } from './types';
