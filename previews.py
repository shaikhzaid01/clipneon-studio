"""Early selectable video frames, independent of full source download."""
from pathlib import Path
import io, json, subprocess, urllib.request
import yt_dlp
from PIL import Image

def prepare(url, job, data, probe, publish):
    if 'Klu' in url: url = url.replace('Klu', 'KLu')
    folder = data / (job + '-preview')
    folder.mkdir(exist_ok=True)
    frames = []

    def ready(file, t, meta, kind):
        frames.append({'url': f'/preview/{job}/{len(frames)}', 'time': round(t, 1), 'kind': kind})
        publish({'preview_status': 'ready', 'preview_frames': list(frames), 'preview_meta': meta})

    # 1. Own source cache enables full-resolution previews without a new download
    cached = None
    for f in data.glob('*.request.json'):
        try:
            if json.loads(f.read_text()).get('url') == url:
                p = data / (f.name.split('.')[0] + '.mp4')
                if p.exists():
                    cached = p
                    break
        except (OSError, ValueError):
            pass

    if cached:
        meta = probe(cached)
        for i, fraction in enumerate((.08, .3, .6, .85)):
            t = meta['duration'] * fraction
            out = folder / f'{i}.jpg'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(t), '-i', str(cached), '-frames:v', '1', '-vf', 'scale=1920:-2:flags=lanczos', '-pix_fmt', 'yuvj420p', '-q:v', '2', str(out)], capture_output=True, check=True, timeout=25)
            ready(out, t, meta, '1080p source frame')
        return

    # Extract metadata without downloading video (using mobile clients to bypass datacenter bot challenges)
    with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True, 'socket_timeout': 15, 'retries': 2, 'extractor_args': {'youtube': {'player_client': ['android_creator', 'tv_embedded']}}}) as y:
        info = y.extract_info(url, download=False)

    duration = float(info.get('duration') or 0)
    if not duration:
        raise RuntimeError('Replay duration unavailable. Wait for download or upload an MP4.')
    meta = {'w': info.get('width') or 1920, 'h': info.get('height') or 1080, 'duration': duration, 'title': info.get('title', 'Video')}

    # 2. Extract actual video frames from remote stream (Full HD 1080p)
    # Prefer HLS (m3u8) as it seeks instantly over HTTP without moov-atom restrictions
    formats = [f for f in info.get('formats', []) if f.get('vcodec') not in (None, 'none') and f.get('url') and f.get('height', 0) <= 1080]
    hls_formats = [f for f in formats if (f.get('protocol') or '').startswith('m3u8') and f.get('height', 0) >= 720]
    avc_formats = [f for f in formats if f.get('vcodec', '').startswith('avc1') and f.get('height', 0) >= 720]
    other_formats = [f for f in formats if f not in hls_formats and f not in avc_formats]

    candidate_streams = hls_formats + avc_formats + other_formats
    headers = ''.join(f'{k}: {v}\r\n' for k, v in info.get('http_headers', {}).items())

    # For streams / long videos, skip initial intro/standby screens (e.g. "stream starting soon")
    fractions = (.15, .35, .60, .85) if duration > 1800 else (.08, .30, .60, .85)

    for fraction in fractions:
        if len(frames) >= 4:
            break
        t = duration * fraction
        out = folder / f'{len(frames)}.jpg'
        for stream in candidate_streams[:4]:
            cmd = ['ffmpeg', '-y', '-v', 'error', '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5', '-rw_timeout', '10000000']
            if headers:
                cmd += ['-headers', headers]
            cmd += ['-ss', str(t), '-i', stream['url'], '-frames:v', '1', '-vf', 'scale=1920:-2:flags=lanczos', '-pix_fmt', 'yuvj420p', '-q:v', '2', str(out)]
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=15)
                if res.returncode == 0 and out.exists() and out.stat().st_size > 1000:
                    ready(out, t, meta, '1080p stream frame')
                    break
            except Exception:
                continue

    if frames:
        return

    # 3. Fallback to storyboards if remote stream direct seek is not supported
    boards = [f for f in info.get('formats', []) if f.get('protocol') == 'mhtml' and f.get('fragments') and f.get('fps')]
    if boards:
        board = max(boards, key=lambda f: f.get('width', 0))
        cols = board.get('columns', 1)
        rows = board.get('rows', 1)
        tiles = cols * rows
        sheets = {}
        for fraction in (.10, .35, .65, .85):
            if len(frames) >= 4:
                break
            t = duration * fraction
            index = int(t * board['fps'])
            sheet = min(index // tiles, len(board['fragments']) - 1)
            cell = index % tiles
            if sheet not in sheets:
                try:
                    req = urllib.request.Request(board['fragments'][sheet]['url'], headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        raw = response.read(12 * 1024 * 1024)
                    sheets[sheet] = Image.open(io.BytesIO(raw)).convert('RGB')
                except Exception:
                    continue
            image = sheets.get(sheet)
            if not image:
                continue
            cw = image.width // cols
            ch = image.height // rows
            x = cell % cols * cw
            y = cell // cols * ch
            out = folder / f'{len(frames)}.jpg'
            cropped = image.crop((x, y, x + cw, y + ch))
            if cropped.width < 960:
                sf = 960 / cropped.width
                cropped = cropped.resize((960, int(cropped.height * sf)), Image.Resampling.LANCZOS)
            cropped.save(out, quality=95)
            ready(out, t, meta, 'storyboard frame (preview)')

    # 4. Immediate background upgrade to 1080p Full HD Frame from video stream
    import threading
    def upgrade_to_1080p():
        try:
            t_sec = int(duration * 0.15) if duration > 1800 else int(duration * 0.08)
            snippet = folder / 'snippet.mp4'
            cmd_ytdlp = [
                'yt-dlp', '--no-playlist', '--quiet', '--no-warnings',
                '--extractor-args', 'youtube:player_client=android_creator,tv_embedded',
                '--download-sections', f'*{t_sec}-{t_sec+2}',
                '-f', 'bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]/best[height<=1080]',
                '--force-keyframes-at-cuts',
                '-o', str(snippet),
                url
            ]
            res = subprocess.run(cmd_ytdlp, capture_output=True, timeout=25)
            if res.returncode == 0 and snippet.exists() and snippet.stat().st_size > 5000:
                hd_out = folder / 'hd_0.jpg'
                subprocess.run([
                    'ffmpeg', '-y', '-v', 'error',
                    '-i', str(snippet),
                    '-frames:v', '1',
                    '-vf', 'scale=1920:1080:flags=lanczos',
                    '-q:v', '2',
                    str(hd_out)
                ], check=True, timeout=10)
                if hd_out.exists() and hd_out.stat().st_size > 10000:
                    hd_out.replace(folder / '0.jpg')
                    import time
                    tag = int(time.time())
                    if frames:
                        frames[0] = {'url': f'/preview/{job}/0?v={tag}', 'time': round(float(t_sec), 1), 'kind': '1080p HD source frame'}
                    else:
                        frames.append({'url': f'/preview/{job}/0?v={tag}', 'time': round(float(t_sec), 1), 'kind': '1080p HD source frame'})
                    publish({'preview_status': 'ready', 'preview_frames': list(frames), 'preview_meta': meta})
                snippet.unlink(missing_ok=True)
        except Exception:
            pass
    threading.Thread(target=upgrade_to_1080p, daemon=True).start()

    if frames:
        return

    # 4. Emergency fallback only if no video frames could be extracted from stream or storyboard
    thumbs = info.get('thumbnails', [])
    best_thumb = None
    for th in sorted(thumbs, key=lambda x: (x.get('width') or 0, x.get('height') or 0), reverse=True):
        u = th.get('url', '')
        if u and 'webp' not in u:
            best_thumb = u
            break
    if not best_thumb:
        best_thumb = info.get('thumbnail')

    if best_thumb:
        try:
            req = urllib.request.Request(best_thumb, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as response:
                raw = response.read(10 * 1024 * 1024)
            img = Image.open(io.BytesIO(raw)).convert('RGB')
            out0 = folder / '0.jpg'
            img.save(out0, quality=95)
            ready(out0, 0.0, meta, 'fallback cover frame')
        except Exception:
            pass

