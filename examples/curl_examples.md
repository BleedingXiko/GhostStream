# GhostStream curl Examples

No Python? No problem. Here's how to use GhostStream with just `curl`.

## Quick Reference

```bash
# Set your server URL
export GHOSTSTREAM=http://localhost:8765
```

---

## 1. Health Check

```bash
curl $GHOSTSTREAM/api/health
```

Response:
```json
{
  "status": "healthy",
  "version": "0.4.0",
  "uptime_seconds": 123.4,
  "current_jobs": 0,
  "queued_jobs": 0
}
```

---

## 2. Check Hardware Capabilities

```bash
curl $GHOSTSTREAM/api/capabilities | jq
```

See what GPUs and codecs are available.

---

## 3. Start HLS Stream

```bash
curl -X POST $GHOSTSTREAM/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{
    "source": "https://example.com/video.mp4",
    "mode": "stream",
    "output": {
      "resolution": "1080p",
      "video_codec": "h264"
    }
  }'
```

Response:
```json
{
  "job_id": "abc123-...",
  "status": "queued",
  "stream_url": "http://localhost:8765/stream/abc123-.../master.m3u8"
}
```

---

## 4. Start Adaptive Bitrate (ABR) Stream

Multiple quality variants (like Netflix):

```bash
curl -X POST $GHOSTSTREAM/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{
    "source": "https://example.com/4k-video.mp4",
    "mode": "abr",
    "output": {
      "video_codec": "h264"
    }
  }'
```

---

## 5. Check Job Status

```bash
curl $GHOSTSTREAM/api/transcode/YOUR_JOB_ID/status
```

Response:
```json
{
  "job_id": "abc123-...",
  "status": "ready",
  "progress": 100.0,
  "stream_url": "http://localhost:8765/stream/abc123-.../master.m3u8",
  "hw_accel_used": "nvenc"
}
```

Status values: `queued`, `processing`, `ready`, `error`, `cancelled`

---

## 6. Cancel a Job

```bash
curl -X POST $GHOSTSTREAM/api/transcode/YOUR_JOB_ID/cancel
```

---

## 7. Delete Job (cleanup temp files)

```bash
curl -X DELETE $GHOSTSTREAM/api/transcode/YOUR_JOB_ID
```

---

## 8. Start from Specific Time (Seeking)

Resume from 30 minutes:

```bash
curl -X POST $GHOSTSTREAM/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{
    "source": "https://example.com/movie.mp4",
    "mode": "stream",
    "start_time": 1800,
    "output": {
      "resolution": "720p"
    }
  }'
```

---

## 9. Get Service Stats

```bash
curl $GHOSTSTREAM/api/stats
```

---

## 10. Play the Stream

Once status is `ready`, play the `stream_url` with any HLS player:

```bash
# VLC
vlc http://localhost:8765/stream/JOB_ID/master.m3u8

# ffplay
ffplay http://localhost:8765/stream/JOB_ID/master.m3u8

# mpv
mpv http://localhost:8765/stream/JOB_ID/master.m3u8
```

Or use it in a web player with hls.js:
```html
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<video id="video"></video>
<script>
  var video = document.getElementById('video');
  var hls = new Hls();
  hls.loadSource('http://localhost:8765/stream/JOB_ID/master.m3u8');
  hls.attachMedia(video);
</script>
```

---

## Full Workflow Example

```bash
# 1. Start transcode
JOB=$(curl -s -X POST $GHOSTSTREAM/api/transcode/start \
  -H "Content-Type: application/json" \
  -d '{"source": "https://example.com/video.mp4", "mode": "stream"}' \
  | jq -r '.job_id')

echo "Job: $JOB"

# 2. Wait for ready
while true; do
  STATUS=$(curl -s $GHOSTSTREAM/api/transcode/$JOB/status | jq -r '.status')
  echo "Status: $STATUS"
  [ "$STATUS" = "ready" ] && break
  [ "$STATUS" = "error" ] && exit 1
  sleep 2
done

# 3. Get stream URL
STREAM=$(curl -s $GHOSTSTREAM/api/transcode/$JOB/status | jq -r '.stream_url')
echo "Stream: $STREAM"

# 4. Play it
vlc "$STREAM"

# 5. Cleanup when done
curl -X DELETE $GHOSTSTREAM/api/transcode/$JOB
```
