from pathlib import Path
import json, subprocess, uuid, threading, shutil, sys, time, os, math
import yt_dlp
from standalone import attach
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn

try:
    import av
    _orig_av_open = av.open
    def _safe_av_open(*args, **kwargs):
        try:
            return _orig_av_open(*args, **kwargs)
        except TypeError as e:
            if 'metadata_errors' in str(e):
                kwargs.pop('metadata_errors', None)
                return _orig_av_open(*args, **kwargs)
            raise
    av.open = _safe_av_open
except Exception:
    pass

from captions import transcribe, large_v3, write as write_captions
from cleanup import cleanup_temporary_files
from metadata_gen import generate_social_metadata

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)

app = FastAPI(title="ClipNeon Studio", version="2.0.0")
state = {}
state_lock = threading.RLock()

def probe(p):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(p)], capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    v = next(x for x in j['streams'] if x['codec_type'] == 'video')
    return {'w': v['width'], 'h': v['height'], 'duration': float(j['format']['duration'])}

@app.get('/')
def home():
    return HTMLResponse((ROOT / 'index.html').read_text(encoding='utf-8'))

attach(app, DATA, state, probe)

@app.post('/cleanup')
def run_cleanup():
    res = cleanup_temporary_files(DATA)
    return {'ok': True, **res}

@app.post('/upload')
async def upload(file: UploadFile = File(...)):
    id = uuid.uuid4().hex
    p = DATA / (id + '.mp4')
    with p.open('wb') as out:
        while b := await file.read(4 * 1024 * 1024):
            out.write(b)
    try:
        m = probe(p)
    except Exception:
        p.unlink(missing_ok=True)
        raise HTTPException(400, 'Please upload a valid, playable video file.')
    file_stem = Path(file.filename or 'Video').stem
    try:
        (DATA / (id + '.meta')).write_text(json.dumps({'title': file_stem}), encoding='utf-8')
    except Exception:
        pass
    return {'id': id, 'filename': file_stem, **m}

@app.get('/media/{id}')
def media(id: str):
    if not id.isalnum():
        raise HTTPException(404)
    p = DATA / (id + '.mp4')
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type='video/mp4')

@app.get('/assets/{name}')
def assets(name: str):
    if name not in ('app.js', 'style.css'):
        raise HTTPException(404)
    return FileResponse(ROOT / name)

@app.get('/text/{id}/{kind}')
def text_file(id: str, kind: str):
    if not id.isalnum() or kind not in ('srt', 'ass'):
        raise HTTPException(404)
    p = DATA / (id + '.' + kind)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename='clip-' + id + '.' + kind)

@app.get('/library')
def library():
    records = []
    for f in DATA.glob('*.json'):
        if f.name.endswith(('.state.json', '.request.json', '.words.json')):
            continue
        try:
            j = json.loads(f.read_text(encoding='utf-8'))
            result = j.get('result', {})
            mp4_file = DATA / (f.stem + '.mp4')
            if result.get('status') == 'done' and mp4_file.exists():
                settings = j.get('settings', {})
                records.append({
                    'id': f.stem,
                    'title': settings.get('title') or 'Viral Clip',
                    'created': f.stat().st_mtime,
                    'meta': result.get('meta', {}),
                    'settings': settings,
                    'social_metadata': settings.get('social_metadata') or {},
                    'srt': (DATA / (f.stem + '.srt')).exists()
                })
        except (ValueError, OSError):
            pass
    return sorted(records, key=lambda j: j['created'], reverse=True)

@app.delete('/clip/{id}')
@app.post('/clip/{id}/delete')
def delete_clip(id: str):
    if not id.isalnum():
        raise HTTPException(400, 'Invalid clip ID.')
    deleted = False
    for ext in ('.mp4', '.json', '.srt', '.ass'):
        p = DATA / (id + ext)
        if p.exists():
            try:
                p.unlink(missing_ok=True)
                deleted = True
            except OSError:
                pass
    with state_lock:
        state.pop(id, None)
    if not deleted:
        raise HTTPException(404, 'Clip not found.')
    return {'ok': True, 'id': id}

PAD = 4.0
FAST_OVER = 600

def _yt(opts):
    base_dl = {'ffmpeg_i': ['-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5']}
    user_dl = opts.pop('downloader_args', {})
    dl_args = {**base_dl, **user_dl}
    ext_args = opts.pop('extractor_args', {})
    yt_ext = ext_args.get('youtube', {})
    yt_clients = yt_ext.get('player_client', ['android_creator', 'tv_embedded'])
    ext_args['youtube'] = {**yt_ext, 'player_client': yt_clients}
    return yt_dlp.YoutubeDL({'noplaylist': True, 'quiet': True, 'no_warnings': True, 'socket_timeout': 25, 'retries': 10, 'fragment_retries': 10, 'downloader_args': dl_args, 'extractor_args': ext_args, **opts})

def _dur(p):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(p)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())

def _pcm(path, start=None, length=None):
    import numpy as np
    cmd = ['ffmpeg', '-v', 'error'] + (['-ss', f'{max(0, start):.2f}'] if start is not None else []) + (['-t', f'{length:.2f}'] if length else []) + ['-i', str(path), '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', 'pipe:1']
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, dtype=np.int16).astype('float32')

def section_start(audio_file, section_file, expected):
    import numpy as np
    needle = _pcm(section_file, 0, 25)
    if len(needle) < 8000 * 5 or float(np.abs(needle).mean()) < 20:
        return expected
    a0 = max(0, expected - 20)
    hay = _pcm(audio_file, a0, 20 + 20 + len(needle) / 8000 + 2)
    if len(hay) < len(needle):
        return expected
    size = 1 << (len(hay) + len(needle)).bit_length()
    corr = np.fft.irfft(np.fft.rfft(hay, size) * np.conj(np.fft.rfft(needle, size)), size)[:len(hay) - len(needle) + 1]
    return a0 + int(np.argmax(corr)) / 8000

def source_range(b):
    id = str(b.get('id', ''))
    src = DATA / (id + '.mp4')
    if not src.exists():
        url = state.get(id, {}).get('url') or b.get('url')
        if not url:
            req_file = DATA / (id + '.request.json')
            if req_file.exists():
                try:
                    url = json.loads(req_file.read_text()).get('url')
                except Exception:
                    pass
        if url:
            try:
                start_req = float(b.get('start', 0))
                end_req = float(b.get('end', start_req + 30))
            except Exception:
                start_req = 0.0
                end_req = 30.0
            from yt_dlp.utils import download_range_func
            section_name = uuid.uuid4().hex
            with _yt({'format': 'bv*[height<=2160][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=2160]+ba/b[height<=2160]/b',
                      'merge_output_format': 'mp4', 'outtmpl': str(DATA / (section_name + '.%(ext)s')),
                      'download_ranges': download_range_func(None, [(max(0, start_req - PAD), end_req + 1)])}) as y:
                y.download([url])
            sec_file = DATA / (section_name + '.mp4')
            if sec_file.exists():
                m = probe(sec_file)
                dur = m['duration']
                start = min(PAD, max(0.0, dur - 1.0)) if start_req >= PAD else start_req
                end = max(start + 1.0, min(dur, start + (end_req - start_req)))
                return section_name, sec_file, m, start, end
    if not id.isalnum() or not src.exists():
        raise HTTPException(400, 'Source video not ready. Please wait for download or upload a video file.')
    m = probe(src)
    try:
        start = float(b.get('start', 0))
        end = float(b.get('end', min(30, m['duration'])))
    except (TypeError, ValueError):
        raise HTTPException(400, 'Invalid time range.')
    if not all(map(math.isfinite, (start, end))) or not 0 <= start < end <= m['duration'] + .1:
        raise HTTPException(400, 'Check start and end times.')
    if end - start > 600:
        raise HTTPException(400, 'Please choose a clip shorter than 10 minutes.')
    return id, src, m, start, end

@app.post('/transcribe')
def caption_job(b: dict):
    id, src, m, start, end = source_range(b)
    job = uuid.uuid4().hex
    with state_lock:
        state[job] = {'status': 'running', 'message': 'Generating local AI subtitles...'}
    language = b.get('language') or None
    if language not in (None, 'hi', 'en', 'hinglish'):
        raise HTTPException(400, 'Choose Auto, Hinglish, Hindi or English.')

    def work():
        try:
            result = transcribe(src, start, end, DATA, lambda msg: state[job].update(message=msg), language)
            with state_lock:
                state[job] = {'status': 'done', **result, 'source_id': id, 'start': start, 'end': end}
        except Exception as e:
            with state_lock:
                state[job] = {'status': 'error', 'error': 'Subtitles generation failed: ' + str(e)[-400:]}

    threading.Thread(target=work, daemon=True).start()
    return {'job': job}

def get_target_dimensions(aspect='9:16', resolution='4k'):
    # Resolution definitions
    if resolution == '4k':
        if aspect == '9:16': return 2160, 3840, '35M'
        if aspect == '1:1': return 2160, 2160, '28M'
        return 3840, 2160, '40M'
    elif resolution == '2k':
        if aspect == '9:16': return 1440, 2560, '22M'
        if aspect == '1:1': return 1440, 1440, '18M'
        return 2560, 1440, '24M'
    elif resolution == '720p':
        if aspect == '9:16': return 720, 1280, '6M'
        if aspect == '1:1': return 720, 720, '5M'
        return 1280, 720, '6M'
    else: # 1080p (Default high-def)
        if aspect == '9:16': return 1080, 1920, '14M'
        if aspect == '1:1': return 1080, 1080, '12M'
        return 1920, 1080, '16M'

def get_os_encoders(bitrate='14M'):
    """Hardware accelerated encoders tailored for each operating system."""
    if sys.platform == 'darwin':
        return [
            ['-c:v', 'h264_videotoolbox', '-b:v', bitrate, '-allow_sw', '1'],
            ['-c:v', 'libx264', '-crf', '17', '-preset', 'faster']
        ]
    # Windows & Linux: Prioritize NVIDIA NVENC, then AMF, then CPU (limited threads for server stability)
    return [
        ['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '18', '-b:v', bitrate, '-maxrate', f'{int(bitrate[:-1])*1.5:.0f}M', '-bufsize', f'{int(bitrate[:-1])*2:.0f}M'],
        ['-c:v', 'h264_amf', '-b:v', bitrate],
        ['-c:v', 'libx264', '-crf', '18', '-preset', 'faster', '-threads', '2']
    ]

@app.post('/export')
def export(b: dict):
    id, src, m, start, end = source_range(b)
    layout = b.get('layout', 'stack')
    if layout not in ('stack', 'blur', 'fill', 'game'):
        raise HTTPException(400, 'Choose a valid layout.')
    style = b.get('caption_style', 'gta')
    if style not in ('gta', 'bold', 'clean', 'hormozi', 'mrbeast'):
        raise HTTPException(400, 'Choose a valid caption style.')

    aspect = b.get('aspect', '9:16')
    resolution = b.get('resolution', '4k') # Default to Ultra High Quality 4K
    target_w, target_h, target_bitrate = get_target_dimensions(aspect, resolution)

    try:
        top_ratio = float(b.get('top', .32))
        position = int(b.get('caption_y', 1450))
        if not math.isfinite(top_ratio) or not .2 <= top_ratio <= .5 or not 200 <= position <= 3600:
            raise ValueError()
        rects = []
        for key in ['cam', 'game']:
            r = b[key]
            values = [float(r[k]) for k in ('x', 'y', 'w', 'h')]
            if not all(map(math.isfinite, values)) or min(values) < 0 or values[2] < .01 or values[3] < .01:
                raise ValueError()
            x = max(0, min(m['w'] - 2, int(values[0] * m['w'])))
            y = max(0, min(m['h'] - 2, int(values[1] * m['h'])))
            w = max(2, min(m['w'] - x, int(values[2] * m['w'])))
            h = max(2, min(m['h'] - y, int(values[3] * m['h'])))
            rects.append((x, y, w, h))
    except (KeyError, ValueError, TypeError, OverflowError):
        raise HTTPException(400, 'Invalid crop areas or position settings.')

    # Build dynamic filter graph
    if layout == 'stack':
        top_h = int(top_ratio * target_h) // 2 * 2
        bot_h = target_h - top_h
        parts = [
            f'[s0]crop={rects[0][2]}:{rects[0][3]}:{rects[0][0]}:{rects[0][1]},scale={target_w}:{top_h}:flags=lanczos:force_original_aspect_ratio=increase,crop={target_w}:{top_h},setsar=1[p0]',
            f'[s1]crop={rects[1][2]}:{rects[1][3]}:{rects[1][0]}:{rects[1][1]},scale={target_w}:{bot_h}:flags=lanczos:force_original_aspect_ratio=increase,crop={target_w}:{bot_h},setsar=1[p1]'
        ]
        graph = '[0:v]split=2[s0][s1];' + ';'.join(parts) + ';[p0][p1]vstack=inputs=2[base]'
    elif layout == 'blur':
        half_w = target_w // 2
        half_h = target_h // 2
        graph = (
            f'[0:v]split=2[b][f];'
            f'[b]scale={half_w}:{half_h}:flags=lanczos:force_original_aspect_ratio=increase,crop={half_w}:{half_h},boxblur=12:4,scale={target_w}:{target_h}:flags=lanczos[bg];'
            f'[f]scale={target_w}:{target_h}:flags=lanczos:force_original_aspect_ratio=decrease[fg];'
            f'[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]'
        )
    else:
        crop = '' if layout == 'fill' else 'crop=%d:%d:%d:%d,' % (rects[1][2], rects[1][3], rects[1][0], rects[1][1])
        graph = f'[0:v]{crop}scale={target_w}:{target_h}:flags=lanczos:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1[base]'

    job = uuid.uuid4().hex
    out = DATA / (job + '.mp4')
    with state_lock:
        state[job] = {'status': 'running', 'message': f'Preparing {resolution.upper()} export ({aspect})...'}

    def work():
        try:
            words = []
            if b.get('captions', False):
                if isinstance(b.get('words'), list):
                    for word in b['words'][:2000]:
                        a = float(word['start'])
                        z = float(word['end'])
                        text = str(word['text'])[:100]
                        if math.isfinite(a) and math.isfinite(z) and 0 <= a < z <= end - start + .1:
                            words.append({'start': a, 'end': z, 'text': text})
                else:
                    words = transcribe(src, start, end, DATA, lambda msg: state[job].update(message=msg), b.get('language') or None)['words']

            hook_y = int(top_ratio * target_h + 40) if layout == 'stack' else int(target_h * 0.15)
            ass_name, srt_name = write_captions(
                words, style, DATA, job, str(b.get('hook', '')),
                position=position, hook_y=hook_y, target_w=target_w, target_h=target_h
            )
            ass_file = DATA / ass_name
            # Cross-platform sanitize for FFmpeg ass filter
            clean_ass = str(ass_file).replace('\\', '/').replace(':', r'\:')
            fc = graph + f";[base]ass='{clean_ass}'[v]" if words else graph + ";[base]null[v]"

            with state_lock:
                state[job].update(message=f'Rendering {resolution.upper()} Ultra Quality clip ({target_w}x{target_h})...')

            # EBU R128 audio normalization filter for punchy sound
            audio_filter = ['-af', 'loudnorm=I=-16:TP=-1.5:LRA=11']
            base_cmd = [
                'ffmpeg', '-y', '-v', 'error', '-threads', '2',
                '-ss', str(start), '-t', str(end - start),
                '-i', str(src),
                '-filter_complex', fc,
                '-map', '[v]', '-map', '0:a?'
            ] + audio_filter + [
                '-c:a', 'aac', '-b:a', '320k', '-ar', '48000',
                '-movflags', '+faststart'
            ]

            encoders = get_os_encoders(target_bitrate)
            r = None
            used_enc = 'libx264'
            for encoder in encoders:
                r = subprocess.run(base_cmd + encoder + ['-pix_fmt', 'yuv420p', str(out)], capture_output=True, text=True, cwd=str(DATA))
                if r.returncode == 0:
                    used_enc = encoder[1]
                    break

            if r.returncode != 0:
                raise RuntimeError(r.stderr[-800:])

            # Generate and attach complete social media metadata
            transcript_text = ' '.join(w.get('text', '') for w in words) if words else str(b.get('title', ''))
            social_meta = generate_social_metadata(transcript_text, str(b.get('title', '')), str(b.get('hook', '')))

            with state_lock:
                state[job] = {
                    'status': 'done',
                    'file': job,
                    'meta': probe(out),
                    'encoder': used_enc,
                    'resolution': f'{target_w}x{target_h}',
                    'aspect': aspect,
                    'caption_words': len(words),
                    'social_metadata': social_meta,
                    'srt': f'/text/{job}/srt' if words else None
                }
            (DATA / (job + '.json')).write_text(json.dumps({'source': id, 'settings': {**b, 'social_metadata': social_meta}, 'result': state[job]}, indent=2), encoding='utf-8')
        except Exception as e:
            with state_lock:
                state[job] = {'status': 'error', 'error': str(e)}

    threading.Thread(target=work, daemon=True).start()
    return {'job': job}

@app.post('/make')
def make(b: dict):
    url, src0 = app.neon['new_source'](b)
    save = app.neon['save']
    raw_count = b.get('count')
    is_auto = raw_count in ('auto', 'all', None, '', 0, '0')
    if not is_auto:
        try: count = max(1, min(30, int(raw_count)))
        except (TypeError, ValueError): is_auto = True; count = None
    else: count = None
    try:
        max_len = max(10, min(180, float(b.get('max_len', b.get('length', 60)))))
        min_len = max(5, min(max_len, float(b.get('min_len', 30))))
    except (TypeError, ValueError):
        raise HTTPException(400, 'Choose a valid clip length range.')
    mode = 'deep' if b.get('mode') == 'deep' else 'fast'
    acc = b.get('accurate', 'accurate')
    acc = 'fast' if acc in (False, 'fast') else 'best' if acc == 'best' else 'accurate'
    layout = b.get('layout', 'fill')
    style = b.get('caption_style', 'gta')
    language = b.get('language') or None
    aspect = b.get('aspect', '9:16')
    resolution = b.get('resolution', '4k') # Default 4K Ultra High Definition
    from translit import to_hinglish
    top_h = b.get('top')
    cam = b.get('cam') or {'x': 0, 'y': 0, 'w': .25, 'h': .3}
    game = b.get('game') or {'x': .25, 'y': 0, 'w': .75, 'h': 1}
    job = uuid.uuid4().hex
    save(job, {'id': job, 'status': 'running', 'stage': 'Initializing...', 'progress': 0, 'clips': [], 'source': {'url': url}})

    def work():
        began = time.monotonic()
        try:
            from smart import find
            def calc_auto(dur):
                if dur <= 180: return max(2, min(4, int(dur / 45) or 2))
                elif dur <= 900: return max(4, min(8, int(dur / 90) or 4))
                elif dur <= 3600: return max(6, min(12, int(dur / 200) or 6))
                else: return max(8, min(20, int(dur / 350) or 8))
            target_count = [count]
            fast = False
            title = b.get('title') or ''
            yt_words = None
            video_lang = ''
            if not url and id:
                meta_f = DATA / (id + '.meta')
                if meta_f.exists():
                    try:
                        title = json.loads(meta_f.read_text(encoding='utf-8')).get('title', '')
                    except Exception:
                        pass
            if url:
                save(job, {'stage': 'Analyzing remote video info...', 'progress': .01})
                with _yt({'skip_download': True}) as y:
                    info = y.extract_info(url, download=False)
                video_lang = str(info.get('language') or '')
                is_currently_live = bool(info.get('is_live') or info.get('live_status') == 'is_live')
                dur_val = float(info.get('duration') or 0)
                fast = dur_val > FAST_OVER and not is_currently_live and mode == 'fast'
                title = info.get('title') or ''
                if not target_count[0] and dur_val:
                    target_count[0] = calc_auto(dur_val)
                try:
                    from captions_yt import from_info
                    yt_words = from_info(info)
                    save(job, {'transcript_source': 'Auto Captions' if yt_words else 'Local Speech Model'})
                except Exception:
                    yt_words = None
            save(job, {'fast_path': fast, 'title': title, 'target_count': target_count[0]})
            import learn, action
            from concurrent.futures import ThreadPoolExecutor as _TP
            W = learn.load_weights(DATA)

            def lowres_section(u, c):
                from yt_dlp.utils import download_range_func
                name = 'lr_' + uuid.uuid4().hex
                with _yt({'format': 'worst[ext=mp4]/bv*[height<=240][ext=mp4]/worst', 'outtmpl': str(DATA / (name + '.%(ext)s')), 'merge_output_format': 'mp4',
                          'download_ranges': download_range_func(None, [(max(0, c['start']), c['end'])])}) as y:
                    y.download([u])
                f = next(x for x in DATA.glob(name + '.*') if x.suffix not in ('.part', '.ytdl'))
                return f, 0.0

            def choose(pool_clips, locate):
                tc = target_count[0] or 6
                if not pool_clips:
                    return []
                if locate is None:
                    for c in pool_clips:
                        c['rank'] = learn.score(c['features'], W)
                        c['score'] = round(c['rank'] * 100)
                        cues = [n for n, k, t in (('shouting', 'shout', .6), ('loud jump', 'loud', .6), ('exclamations', 'words', .5)) if (c['features'].get(k) or 0) >= t]
                        c['reason'] = (('Cues: ' + ', '.join(cues)) if cues else 'Peak energy') + (f" · AI {c['llm']:g}/10" if c.get('llm') else '')
                    pool_clips.sort(key=lambda c: -c['rank'])
                    return pool_clips[:tc]
                K = min(len(pool_clips), tc * 2)
                top = pool_clips[:K]
                save(job, {'stage': 'Verifying action dynamics in candidate moments...', 'progress': .5})

                def one(c):
                    tmp = None
                    try:
                        f, off = locate(c)
                        tmp = f if str(f).startswith(str(DATA)) and Path(f).name.startswith('lr_') else None
                        c['_act'] = action.analyze(f, off, c['end'] - c['start'])
                    except Exception:
                        c['_act'] = None
                    finally:
                        if tmp:
                            Path(tmp).unlink(missing_ok=True)

                with _TP(max_workers=4) as ex:
                    list(ex.map(one, top))

                for key in ('motion', 'flash', 'strobe', 'cuts'):
                    vals = [c['_act'][key] for c in top if c.get('_act')]
                    if not vals:
                        continue
                    lo, hi = min(vals), max(vals)
                    for c in top:
                        if c.get('_act'):
                            v = c['_act'][key]
                            c['features'][key] = round(.5 * v + .5 * ((v - lo) / (hi - lo) if hi > lo else v), 3)

                for c in top:
                    c['rank'] = learn.score(c['features'], W)
                    c['score'] = round(c['rank'] * 100)
                    cues = [n for n, k, t in (('shouting', 'shout', .6), ('loud jump', 'loud', .6), ('exclamations', 'words', .5), ('fast motion', 'motion', .7), ('explosion/flash', 'flash', .5), ('light flicker', 'strobe', .5)) if (c['features'].get(k) or 0) >= t]
                    c['reason'] = (('Cues: ' + ', '.join(cues)) if cues else 'Peak moments') + (f" · AI {c['llm']:g}/10" if c.get('llm') else '')
                    c.pop('_act', None)
                top.sort(key=lambda c: -c['rank'])
                return top[:tc]

            sections = None
            if fast:
                def hook(d):
                    t = d.get('total_bytes') or d.get('total_bytes_estimate')
                    if t and d.get('status') == 'downloading':
                        save(job, {'stage': f"Fetching audio track {d['downloaded_bytes']/1048576:.0f}/{t/1048576:.0f} MB", 'progress': .02 + .1 * d['downloaded_bytes'] / t})
                with _yt({'format': 'ba[ext=m4a]/ba/worst', 'outtmpl': str(DATA / (job + '.audio.%(ext)s')), 'progress_hooks': [hook]}) as y:
                    y.download([url])
                p = next(f for f in DATA.glob(job + '.audio.*') if f.suffix not in ('.part', '.ytdl'))
                duration = _dur(p)
                sid = job
                if not target_count[0]:
                    target_count[0] = calc_auto(duration)
                tc = target_count[0]
                save(job, {'meta': {'duration': duration}, 'target_count': tc, 'stage': f'Extracting viral highlights ({tc} clips)...', 'progress': .12})
                found, engine = find(p, duration, tc, max_len, lambda f, m: save(job, {'stage': m, 'progress': .12 + .4 * f}), language, DATA, min_len, False, yt_words, False, W, False, video_title=title)
                found = choose(found, None if fast else lambda c: lowres_section(url, c))
                save(job, {'engine': engine + ' (audio-first)', 'stage': 'Downloading high-resolution video segments...', 'progress': .58, 'candidates': len(found)})
                from concurrent.futures import ThreadPoolExecutor
                from yt_dlp.utils import download_range_func

                def section(i, c):
                    last_err = None
                    for attempt in range(3):
                        try:
                            name = uuid.uuid4().hex
                            with _yt({'format': 'bv*[height<=2160][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=2160]+ba/b[height<=2160]/b', 'merge_output_format': 'mp4', 'outtmpl': str(DATA / (name + '.%(ext)s')),
                                      'download_ranges': download_range_func(None, [(max(0, c['start'] - PAD), c['end'] + 1)])}) as y:
                                y.download([url])
                            return name
                        except Exception as err:
                            last_err = err
                            time.sleep(2)
                    raise last_err

                pool = ThreadPoolExecutor(max_workers=2)
                sections = [pool.submit(section, i, c) for i, c in enumerate(found)]
            else:
                if url:
                    save(job, {'stage': 'Downloading 4K/HD source...', 'progress': .02})
                    sid, p = app.neon['download'](url, job)
                else:
                    sid, p = src0.stem, src0
                meta = probe(p)
                duration = meta['duration']
                if not target_count[0]:
                    target_count[0] = calc_auto(duration)
                tc = target_count[0]
                save(job, {'source_id': sid, 'meta': meta, 'target_count': tc, 'stage': f'Locating viral peaks ({tc} clips)...', 'progress': .1})
                found, engine = find(p, duration, tc, max_len, lambda f, m: save(job, {'stage': m, 'progress': .1 + .5 * f}), language, DATA, min_len, True, yt_words, mode == 'deep', W, False, video_title=title)
                found = choose(found, lambda c: (p, c['start']))
                save(job, {'engine': engine, 'stage': f'Rendering clips in {resolution.upper()} quality...', 'progress': .6, 'candidates': len(found)})

            done = []
            for i, c in enumerate(found):
                if sections:
                    sid2 = sections[i].result()
                    dur = _dur(DATA / (sid2 + '.mp4'))
                    real = section_start(p, DATA / (sid2 + '.mp4'), max(0, c['start'] - PAD))
                    start = max(0.0, min(c['start'] - real, max(0.0, dur - 1.0)))
                    end = max(start + 1.0, min(dur, start + c['end'] - c['start']))
                    words = c.get('words')
                else:
                    sid2 = sid
                    start = c['start']
                    end = c['end']
                    words = c.get('words')

                lang = language or ('hinglish' if (video_lang.startswith('hi') or c.get('language') == 'hi') else None)
                hook_txt = ' '.join(to_hinglish(w) for w in c.get('hook', '').split()) if lang == 'hinglish' else c.get('hook', '')
                
                # Full social metadata package
                social_meta = c.get('social_metadata') or generate_social_metadata(c.get('transcript', ''), c.get('title', ''), hook_txt, video_title=title, clip_index=i+1)
                if not social_meta or any(social_meta.get('title', '').lower().startswith(p) for p in ('clip', 'activity', 'moment', 'highlight')):
                    social_meta = generate_social_metadata(c.get('transcript', ''), '', hook_txt, video_title=title, clip_index=i+1)

                body = {
                    'id': sid2,
                    'start': start,
                    'end': end,
                    'layout': layout,
                    'aspect': aspect,
                    'resolution': resolution,
                    'top': (top_h if isinstance(top_h, (int, float)) and .2 <= top_h <= .5 else .32),
                    'captions': True,
                    'caption_style': style,
                    'language': language,
                    'hook': hook_txt,
                    'title': social_meta['title'],
                    'social_metadata': social_meta,
                    'cam': cam,
                    'game': game,
                    'features': c.get('features'),
                    'weights_used': W
                }

                if acc != 'fast' or lang in ('hinglish', 'hi') or not c.get('words'):
                    save(job, {'stage': f'AI speech recognition {i+1}/{len(found)}'})
                    body['words'] = transcribe(DATA / (sid2 + '.mp4'), start, end, DATA, lambda m: None, lang, (large_v3() if acc == 'best' else None))['words']
                else:
                    body['words'] = c['words']

                ej = export(body)['job']
                while state.get(ej, {}).get('status') == 'running':
                    time.sleep(.4)
                r = state.get(ej, {})
                done.append({
                    'file': ej,
                    'start': c['start'],
                    'end': c['end'],
                    'title': social_meta['title'],
                    'hook': social_meta['hook'],
                    'score': c['score'],
                    'reason': c.get('reason', ''),
                    'social_metadata': social_meta,
                    'error': r.get('error'),
                    'srt': r.get('srt')
                })
                save(job, {'clips': list(done), 'stage': f'Rendered {i+1}/{len(found)} ({resolution.upper()})', 'progress': .6 + .4 * (i + 1) / len(found)})

            # Clean up temporary downloads
            cleanup_temporary_files(DATA, max_age_seconds=1800)
            save(job, {'status': 'done', 'stage': 'All clips ready!', 'progress': 1, 'elapsed_seconds': round(time.monotonic() - began, 1)})
        except Exception as e:
            save(job, {'status': 'error', 'error': str(e)[-500:]})

    threading.Thread(target=work, daemon=True).start()
    return {'job': job}

@app.post('/feedback')
def feedback(b: dict):
    import learn
    file = str(b.get('file', ''))
    try: rating = int(b.get('rating', 0))
    except (TypeError, ValueError): rating = 0
    if not file.isalnum() or not 1 <= rating <= 5:
        raise HTTPException(400, 'Please choose a rating between 1 and 5.')
    f = DATA / (file + '.json')
    if not f.exists():
        raise HTTPException(404, 'Clip record not found.')
    m = json.loads(f.read_text(encoding='utf-8')).get('settings', {})
    if not m.get('features'):
        raise HTTPException(400, 'Clip scoring data unavailable.')
    return learn.record(DATA, file, rating, m['features'], {'layout': m.get('layout'), 'style': m.get('caption_style')})

@app.get('/feedback/summary')
def feedback_summary():
    import learn
    return learn.refit(DATA)

@app.get('/make/{job}')
def make_status(job: str):
    if job not in state:
        raise HTTPException(404, 'Job not found.')
    return state[job]

@app.get('/status/{id}')
def status(id: str):
    return state.get(id, {'status': 'error', 'error': 'Job not found. Please try again.'})

@app.post('/shutdown')
def shutdown_server():
    threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
    return {'status': 'shutting down'}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', os.environ.get('CLIPNEON_PORT', '8770')))
    uvicorn.run(app, host='127.0.0.1', port=port)
