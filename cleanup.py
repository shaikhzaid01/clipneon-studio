"""
ClipNeon Disk & Memory Cleanup Manager
Safely removes temporary download chunks, intermediate audio files, and orphan previews.
"""
from pathlib import Path
import time
import shutil

def cleanup_temporary_files(data_dir: Path, max_age_seconds: int = 3600) -> dict:
    """
    Cleans up temporary files in data folder.
    Leaves final .mp4, .json, .srt, .ass, and weights/feedback untouched.
    """
    data = Path(data_dir)
    if not data.exists():
        return {'deleted_files': 0, 'freed_mb': 0.0}
    
    deleted_count = 0
    freed_bytes = 0
    now = time.time()
    
    # 1. Clean up temporary audio and low-res chunk files
    temp_patterns = ['*.audio.*', 'lr_*', '*.wav', '*.state.tmp', '*.part', '*.ytdl']
    for pattern in temp_patterns:
        for p in data.glob(pattern):
            try:
                size = p.stat().st_size
                p.unlink(missing_ok=True)
                deleted_count += 1
                freed_bytes += size
            except Exception:
                pass
                
    # 2. Clean up temporary download directories older than max_age
    for item in data.iterdir():
        if item.is_dir():
            name = item.name
            if name.endswith('-preview') or (len(name) == 32 and name.isalnum()):
                try:
                    mtime = item.stat().st_mtime
                    if now - mtime > max_age_seconds:
                        dir_size = sum(f.stat().st_size for f in item.glob('**/*') if f.is_file())
                        shutil.rmtree(item, ignore_errors=True)
                        deleted_count += 1
                        freed_bytes += dir_size
                except Exception:
                    pass

    return {
        'deleted_files': deleted_count,
        'freed_mb': round(freed_bytes / (1024 * 1024), 2)
    }
