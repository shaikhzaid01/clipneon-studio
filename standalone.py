from pathlib import Path
from urllib.parse import urlparse
import json
import uuid
import threading
import shutil
import time
import subprocess
import sys
import yt_dlp
from fastapi import HTTPException
from highlights import rank
import smart
from previews import prepare
from fastapi.responses import FileResponse

def allowed(url):
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    return (
        parsed.scheme in ('http', 'https')
        and not parsed.username
        and not parsed.password
        and (host in ('youtu.be', 'youtube.com', 'www.youtube.com', 'm.youtube.com', 'twitch.tv', 'www.twitch.tv', 'clips.twitch.tv', 'kick.com', 'www.kick.com'))
        and parsed.port in (None, 80, 443)
    )

def attach(app, DATA, state, probe):
    lock = threading.Lock()
    cancelled_jobs = set()

    def is_cancelled(job):
        return job in cancelled_jobs

    def cancel(job):
        with lock:
            cancelled_jobs.add(job)
            if job in state:
                state[job].update(status='error', error='Cancelled by user.')

    def save(job, record):
        with lock:
            if job in cancelled_jobs:
                record = {'status': 'error', 'error': 'Cancelled by user.'}
            record = {**state.get(job, {}), **record}
            state[job] = record
            temp = DATA / (job + '.state.tmp')
            temp.write_text(json.dumps(record), encoding='utf-8')
            temp.replace(DATA / (job + '.state.json'))

    # Load previous records safely
    for f in DATA.glob('*.state.json'):
        try:
            record = json.loads(f.read_text(encoding='utf-8'))
            job = f.name.split('.')[0]
            if record.get('status') in ('running', 'queued'):
                record.update(status='error', error='Studio restarted during processing. Please retry.')
            state[job] = record
        except (ValueError, OSError):
            pass

    def source_path(id):
        if not id or not id.isalnum():
            raise HTTPException(400, 'Upload or import a video first.')
        p = DATA / (id + '.mp4')
        if not p.exists():
            raise HTTPException(404, 'Source video is unavailable.')
        return p

    def get_hardware_encoder_args(target_bitrate='12M'):
        if sys.platform == 'darwin':
            return ['-c:v', 'h264_videotoolbox', '-b:v', target_bitrate, '-allow_sw', '1']
        return ['-c:v', 'h264_nvenc', '-preset', 'p6', '-cq', '18', '-b:v', target_bitrate]

    def download(url, job):
        for f in DATA.glob('*.request.json'):
            try:
                if json.loads(f.read_text()).get('url') == url:
                    cached = DATA / (f.name.split('.')[0] + '.mp4')
                    if cached.exists():
                        probe(cached)
                        return cached.stem, cached
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        directory = DATA / job
        directory.mkdir(exist_ok=True)
        target = DATA / (job + '.mp4')

        def progress(event):
            if is_cancelled(job):
                raise RuntimeError('Download cancelled by user.')
            if event.get('status') == 'downloading':
                got = event.get('downloaded_bytes', 0)
                total = event.get('total_bytes') or event.get('total_bytes_estimate') or 0
                msg = f'Downloading source: {got/1048576:.1f} MB' + (f' / {total/1048576:.1f} MB' if total else '')
                save(job, {'message': msg, 'stage': msg, 'progress': min(.3, got / total * .3) if total else .1})
            elif event.get('status') == 'finished':
                save(job, {'message': 'Processing downloaded video...', 'stage': 'Processing downloaded video...'})

        options = {
            'noplaylist': True,
            'format': 'best[height<=2160][ext=mp4]/bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best',
            'outtmpl': str(directory / 'source.%(ext)s'),
            'merge_output_format': 'mp4',
            'socket_timeout': 25,
            'retries': 5,
            'fragment_retries': 5,
            'downloader_args': {'ffmpeg_i': ['-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5']},
            'extractor_retries': 2,
            'progress_hooks': [progress],
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {'youtube': {'player_client': ['android_creator', 'tv_embedded']}}
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([url])

        src = next((p for p in directory.iterdir() if p.suffix in ('.mp4', '.mkv', '.webm')), None)
        if src is None:
            raise RuntimeError('Download produced no playable video file. Please try uploading an MP4.')
        if src.suffix == '.mp4':
            shutil.move(str(src), str(target))
        else:
            # OS-aware transcoding
            enc = get_hardware_encoder_args('16M')
            result = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(src)] + enc + ['-c:a', 'aac', '-b:a', '256k', '-movflags', '+faststart', str(target)], capture_output=True, text=True, timeout=3600)
            if result.returncode:
                result = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(src), '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-c:a', 'aac', '-b:a', '256k', '-movflags', '+faststart', str(target)], capture_output=True, text=True, timeout=3600)
            if result.returncode:
                raise RuntimeError(result.stderr[-500:])
        shutil.rmtree(directory, ignore_errors=True)
        probe(target)
        (DATA / (job + '.request.json')).write_text(json.dumps({'url': url}), encoding='utf-8')
        return job, target

    def new_source(body):
        url = str(body.get('url', '')).strip()
        if 'Klu' in url:
            url = url.replace('Klu', 'KLu')
        if url:
            try:
                valid = allowed(url)
            except ValueError:
                valid = False
            if not valid:
                raise HTTPException(400, 'Please enter a valid YouTube, Twitch or Kick video URL.')
            return url, None
        id = str(body.get('id', ''))
        return '', source_path(id)

    app.neon = {'download': download, 'save': save, 'new_source': new_source, 'cancel': cancel, 'is_cancelled': is_cancelled}

    @app.get('/healthz')
    def health():
        return {'ok': True, 'engine': 'clipneon-studio', 'version': '2.0.0'}

    @app.post('/cancel/{job}')
    def cancel_endpoint(job: str):
        cancel(job)
        return {'ok': True, 'job': job, 'status': 'cancelled'}

    @app.post('/link')
    def import_link(body: dict):
        url, _ = new_source(body)
        if not url:
            raise HTTPException(400, 'Paste a video link.')
        job = uuid.uuid4().hex
        save(job, {'status': 'running', 'stage': 'Import starting...', 'message': 'Loading preview frames...', 'preview_status': 'loading', 'url': url})
        (DATA / (job + '.request.json')).write_text(json.dumps({'url': url}), encoding='utf-8')

        def preview_work():
            try:
                prepare(url, job, DATA, probe, lambda fields: save(job, fields))
            except Exception as error:
                save(job, {'preview_status': 'unavailable', 'preview_error': 'Early frames unavailable; full download continues.'})

        threading.Thread(target=preview_work, daemon=True).start()

        def work():
            try:
                with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True, 'socket_timeout': 15, 'retries': 2, 'extractor_args': {'youtube': {'player_client': ['android_creator', 'tv_embedded']}}}) as y:
                    inf = y.extract_info(url, download=False)
                dur = float(inf.get('duration') or 0)
                meta = {'w': inf.get('width') or 1920, 'h': inf.get('height') or 1080, 'duration': dur, 'title': inf.get('title', 'Video')}
                if dur > 600:
                    save(job, {'status': 'done', 'id': job, **meta, 'fast_path': True, 'message': 'Direct clipping ready! Click ⚡ Make clips.'})
                    return
                id, p = download(url, job)
                meta = probe(p)
                folder = DATA / (job + '-preview')
                folder.mkdir(exist_ok=True)
                hd_frames = []
                for i, fraction in enumerate((.08, .3, .6, .85)):
                    t = meta['duration'] * fraction
                    out = folder / f'{i}.jpg'
                    try:
                        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(t), '-i', str(p), '-frames:v', '1', '-vf', 'scale=1920:-2:flags=lanczos', '-pix_fmt', 'yuvj420p', '-q:v', '2', str(out)], capture_output=True, timeout=20)
                        hd_frames.append({'url': f'/preview/{job}/{i}', 'time': round(t, 1), 'kind': '1080p HD source frame'})
                    except Exception:
                        pass
                payload = {'status': 'done', 'id': id, **meta}
                if hd_frames:
                    payload.update(preview_frames=hd_frames, preview_meta=meta, preview_status='ready')
                save(job, payload)
            except Exception as error:
                save(job, {'status': 'error', 'error': 'Import failed: ' + str(error)[-300:]})

        threading.Thread(target=work, daemon=True).start()
        return {'job': job}

    @app.get('/preview/{job}/{index}')
    def preview_file(job: str, index: int):
        if not job.isalnum() or not 0 <= index < 4:
            raise HTTPException(404)
        p = DATA / (job + '-preview') / (str(index) + '.jpg')
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type='image/jpeg')

    @app.post('/analyze')
    def analyze(body: dict):
        url, src = new_source(body)
        b_lang = body.get('language') or None
        raw_count = body.get('count')
        is_auto = raw_count in ('auto', 'all', None, '', 0, '0')
        if not is_auto:
            try:
                count = max(1, min(30, int(raw_count)))
            except (TypeError, ValueError):
                count = None
        else:
            count = None
        try:
            length = max(5, min(120, float(body.get('length', 30))))
        except (TypeError, ValueError):
            raise HTTPException(400, 'Choose a valid clip length.')
        job = uuid.uuid4().hex
        save(job, {'id': job, 'status': 'running', 'stage': 'Starting AI highlight analysis...', 'progress': 0, 'source': {'url': url}})

        def work():
            began = time.monotonic()
            try:
                source_id, p = download(url, job) if url else (src.stem, src)
                meta = probe(p)
                dur = meta['duration']

                def calc_auto(d):
                    if d <= 180:
                        return max(2, min(4, int(d / 45) or 2))
                    elif d <= 900:
                        return max(4, min(8, int(d / 90) or 4))
                    elif d <= 3600:
                        return max(6, min(12, int(d / 200) or 6))
                    else:
                        return max(8, min(20, int(d / 350) or 8))

                actual_count = count if count else calc_auto(dur)
                save(job, {'source_id': source_id, 'stage': f'Analyzing highlights ({actual_count} clips)...', 'progress': .3})
                clips, engine = smart.find(p, dur, actual_count, length, lambda f, m: save(job, {'progress': f, 'stage': m}), b_lang, DATA, float(body.get('min_len', length * .5)))
                save(job, {
                    'id': job,
                    'status': 'done',
                    'stage': 'Highlights ready',
                    'progress': 1,
                    'source_id': source_id,
                    'source': {'url': url},
                    'title': 'Imported video' if url else 'Uploaded video',
                    'clips': clips,
                    'engine': engine,
                    'elapsed_seconds': round(time.monotonic() - began, 2),
                    'meta': meta
                })
            except Exception as error:
                save(job, {'id': job, 'status': 'error', 'error': str(error)[-400:]})

        threading.Thread(target=work, daemon=True).start()
        return {'job': job}

    @app.get('/analysis/{job}')
    def analysis_status(job: str):
        if job not in state:
            raise HTTPException(404, 'Analysis unavailable. Please retry.')
        return state[job]

    @app.get('/highlights')
    def highlights():
        return [dict(record, job=record['id']) for record in list(state.values()) if record.get('status') == 'done' and record.get('clips')]

    @app.post('/highlight-source')
    def highlight_source(body: dict):
        record = state.get(str(body.get('job', '')), {})
        id = record.get('source_id', '')
        p = source_path(id)
        return {'id': id, **probe(p)}
