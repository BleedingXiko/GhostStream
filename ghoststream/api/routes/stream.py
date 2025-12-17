"""
Stream serving routes for GhostStream
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from ...config import get_config
from ...models import JobStatus
from ...jobs import get_job_manager


def _inject_endlist_if_needed(content: str, job_status: JobStatus) -> str:
    """
    Ensure playlist has correct tags based on status.
    For completed jobs, ensure ENDLIST is present.
    For processing jobs, do NOT add ENDLIST (let it be live/event).
    """
    if job_status == JobStatus.READY:
        if "#EXT-X-ENDLIST" not in content:
            content = content.rstrip() + "\n#EXT-X-ENDLIST\n"
    return content

router = APIRouter()


@router.get("/stream/{job_id}/{filename:path}")
async def stream_file(job_id: str, filename: str, request: Request):
    """Serve HLS stream files."""
    job_manager = get_job_manager()
    
    # Touch job to keep it alive while streaming
    job_manager.touch_job(job_id)
    
    config = get_config()
    temp_dir = Path(config.transcoding.temp_directory)
    file_path = temp_dir / job_id / filename
    
    # For playlist files, wait for FFmpeg to create them
    # HDR/complex files can take 10-20s to produce first segments
    if filename.endswith(".m3u8") and not file_path.exists():
        job = job_manager.get_job(job_id, touch=False)
        if job and job.status in (JobStatus.PROCESSING, JobStatus.QUEUED):
            # Wait up to 30 seconds for playlist to be created
            for i in range(60):
                await asyncio.sleep(0.5)
                if file_path.exists():
                    break
                # Re-check job status in case it failed
                if i % 10 == 9:  # Every 5 seconds
                    job = job_manager.get_job(job_id, touch=False)
                    if not job or job.status == JobStatus.ERROR:
                        break
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stream file not found")
    
    # Determine content type
    if filename.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
        # For m3u8 playlists, inject #EXT-X-ENDLIST during active transcoding
        # This makes HLS.js treat the stream as VOD (seekable from the start)
        job = job_manager.get_job(job_id, touch=False)
        job_status = job.status if job else JobStatus.READY
        content = file_path.read_text()
        content = _inject_endlist_if_needed(content, job_status)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
        )
    elif filename.endswith(".ts"):
        media_type = "video/mp2t"
    elif filename.endswith(".mp4"):
        media_type = "video/mp4"
    else:
        media_type = "application/octet-stream"
    
    # Handle range requests for seeking
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
    
    if range_header:
        # Parse range header
        range_match = range_header.replace("bytes=", "").split("-")
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] else file_size - 1
        
        if start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        
        end = min(end, file_size - 1)
        content_length = end - start + 1
        
        def file_iterator():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(8192, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": media_type,
        }
        
        return StreamingResponse(
            file_iterator(),
            status_code=206,
            headers=headers,
            media_type=media_type
        )
    
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Accept-Ranges": "bytes"}
    )


@router.get("/download/{job_id}")
async def download_file(job_id: str):
    """Download completed batch transcode file."""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != JobStatus.READY:
        raise HTTPException(status_code=400, detail="Job is not ready for download")
    
    if not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    
    return FileResponse(
        job.output_path,
        filename=Path(job.output_path).name,
        media_type="application/octet-stream"
    )
