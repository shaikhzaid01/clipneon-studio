"""Independent word-timed ASR, editable transcript and styled ASS/SRT."""
from pathlib import Path
from translit import to_hinglish
import json, os, sys, threading, subprocess

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

MODELS = {}
ROMAN_PROMPT = 'Aaj hum video ke baare mein baat karenge, yeh bahut mast hai. Kya haal hai bhai, chalo shuru karte hain. Matlab ekdum sahi hai, nahi yaar, bilkul.'
DEVA_PROMPT = 'यह हिंदी और अंग्रेज़ी में गेमिंग और व्लॉग वीडियो है। गेमप्ले, क्लिपिंग, वीडियो, मजेदार मोमेंट्स।'

# Cross-platform fonts that reliably render Hindi & English ligatures
HINDI_FONTS = 'Nirmala UI, Mangal, Noto Sans Devanagari, Segoe UI, Arial'

def large_v3():
    snaps = list((Path.home() / '.cache/huggingface/hub/models--Systran--faster-whisper-large-v3/snapshots').glob('*'))
    return str(snaps[0]) if snaps else 'large-v3'

def _best_hindi():
    try:
        if os.environ.get('CLIPNEON_CPU') == '1':
            return ''
        free = int(subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5).stdout.split()[0])
        if free >= 3400:
            return large_v3()
    except Exception:
        pass
    return ''

def _load(WhisperModel, spec, data):
    """Hardware accelerated GPU first, CPU as universal fallback."""
    # 1. Cross-platform CUDA / cuDNN library path linking
    if sys.platform == 'win32':
        site = Path(sys.prefix) / 'Lib' / 'site-packages' / 'nvidia'
        for sub in ('cublas', 'cudnn'):
            b = site / sub / 'bin'
            if b.exists():
                try:
                    os.add_dll_directory(str(b))
                except (OSError, AttributeError):
                    pass
                os.environ['PATH'] = str(b) + os.pathsep + os.environ['PATH']
    else:
        # Linux (Kaggle / Colab): Link nvidia shared libs to LD_LIBRARY_PATH
        import site as _site
        sp_paths = _site.getsitepackages() if hasattr(_site, 'getsitepackages') else [Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages']
        for sp in sp_paths:
            nvidia_dir = Path(sp) / 'nvidia'
            if nvidia_dir.exists():
                for sub in ('cublas', 'cudnn', 'cuda_runtime'):
                    lib_dir = nvidia_dir / sub / 'lib'
                    if lib_dir.exists():
                        cur_ld = os.environ.get('LD_LIBRARY_PATH', '')
                        if str(lib_dir) not in cur_ld:
                            os.environ['LD_LIBRARY_PATH'] = str(lib_dir) + (':' + cur_ld if cur_ld else '')

    # 2. Try loading on NVIDIA GPU (Tesla T4 / CUDA) with Turing-optimal float16
    if os.environ.get('CLIPNEON_CPU') != '1':
        for ctype in ('float16', 'int8_float16', 'int8'):
            try:
                m = WhisperModel(spec, device='cuda', compute_type=ctype, download_root=str(data / 'models'))
                list(m.transcribe(__import__('numpy').zeros(16000, dtype='float32'))[0])
                print(f"[AI Speech] Successfully loaded Whisper ({spec}) on NVIDIA GPU (compute: {ctype})")
                return m
            except Exception as err:
                continue

    print(f"[AI Speech] Warning: Running Whisper ({spec}) on CPU fallback (limited to 2 threads)")
    return WhisperModel(spec, device='cpu', compute_type='int8', cpu_threads=2, download_root=str(data / 'models'))

LOCK = threading.Lock()

def transcribe(source, start, end, data, report=lambda message: None, language=None, model=None):
    report('Loading speech model...')
    from faster_whisper import WhisperModel
    if end <= start:
        return {'words': [], 'language': language or 'en', 'duration': 0, 'model': 'none'}
    spec = model or os.environ.get('CLIPNEON_ASR_MODEL', '') or (_best_hindi() if language in ('hi', 'hinglish') else '')
    if not spec:
        snapshots = Path.home() / '.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots'
        available = [p for p in snapshots.glob('*') if (p / 'model.bin').exists()]
        spec = str(available[0]) if available else 'large-v3-turbo'
    model_tag = Path(spec).name[:14]
    key = f'{source.stem}-{start:.2f}-{end:.2f}-{language or "auto"}-{model_tag}-v4'
    cached = data / (key + '.words.json')
    if cached.exists():
        return json.loads(cached.read_text(encoding='utf-8'))
    with LOCK:
        MODEL = MODELS.get(spec)
        if MODEL is None:
            MODEL = MODELS[spec] = _load(WhisperModel, spec, data)
        audio = data / (key + '.wav')
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(start), '-t', str(end - start), '-i', str(source), '-vn', '-ac', '1', '-ar', '16000', str(audio)], capture_output=True, check=True, timeout=300)
        try:
            report('Transcribing speech with AI timestamps...')
            prompt = DEVA_PROMPT if language in ('hi', 'hinglish') else None
            segments, info = MODEL.transcribe(
                str(audio),
                beam_size=5,
                language=('hi' if language == 'hinglish' else language) or None,
                initial_prompt=prompt,
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False
            )
            words = []
            for segment in segments:
                for w in segment.words or []:
                    text = w.word.strip()
                    if language == 'hinglish':
                        text = to_hinglish(text)
                    if text and w.end > w.start:
                        words.append({'start': round(max(0, w.start), 3), 'end': round(min(end - start, w.end), 3), 'text': text})
            result = {'words': words, 'language': info.language, 'duration': end - start, 'model': model_tag}
            cached.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            return result
        finally:
            audio.unlink(missing_ok=True)

def stamp(t, ass=False):
    units = int(round(max(0, float(t)) * (100 if ass else 1000)))
    h, units = divmod(units, (360000 if ass else 3600000))
    m, units = divmod(units, (6000 if ass else 60000))
    s, u = divmod(units, (100 if ass else 1000))
    return f'{h}:{m:02}:{s:02}.{u:02}' if ass else f'{h:02}:{m:02}:{s:02},{u:03}'

# style -> (rest colour, active colour, outline/shadow tags, base font size, pop scale %). ASS colours are BGR.
STYLES = {
    'gta': ('&H00EFFF43&', '&H00A845FF&', r'\bord4\3c&H00560C31&\shad5\4c&H00FFCC00&\blur0.7', 72, 0),
    'bold': ('&H00FFFFFF&', '&H003DF3CB&', r'\bord6\shad4', 72, 0),
    'hormozi': ('&H00FFFFFF&', '&H00FFFF00&', r'\bord7\3c&H00000000&\shad4\4c&H80000000&', 84, 114),
    'mrbeast': ('&H0000FFFF&', '&H000066FF&', r'\bord9\3c&H00000000&\shad5\4c&H00000000&', 90, 115),
}
STYLE_NAMES = tuple(STYLES) + ('clean',)

def safe(text):
    return str(text).replace('\\', '/').replace('{', '(').replace('}', ')').replace('\n', ' ')

def group_words(words, max_words=4, max_chars=24, gap=0.45):
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text = w['text'].rstrip()
        chars = sum(len(x['text']) + 1 for x in cur)
        stop = nxt is None or len(cur) >= max_words or (len(cur) >= 2 and (text[-1:] in '.?!,;:।…' or nxt['start'] - w['end'] > gap)) or (len(cur) >= 2 and chars + len(nxt['text']) > max_chars)
        if stop:
            groups.append(cur)
            cur = []
    if len(groups) > 1 and len(groups[-1]) == 1 and len(groups[-2]) < max_words and groups[-1][0]['start'] - groups[-2][-1]['end'] < gap:
        groups[-2] += groups.pop()
    if len(groups) > 1 and len(groups[-1]) == 1 and len(groups[-2]) >= max_words:
        groups[-1].insert(0, groups[-2].pop())
    return groups

def hook_lines(hook, width=17, max_lines=3):
    import textwrap
    words = safe(hook).split()
    text = ' '.join(words)
    if len(text) > 60:
        text = text[:60].rsplit(' ', 1)[0]
    lines = textwrap.wrap(text, width=width) or ['']
    return lines[:max_lines]

def write(words, style, folder, name, hook='', position=1450, hook_y=300, target_w=1080, target_h=1920):
    scale_factor = target_w / 1080.0
    scaled_pos = int(position * (target_h / 1920.0))
    scaled_hook_y = int(hook_y * (target_h / 1920.0))
    center_x = target_w // 2

    # ASS header with Unicode multi-script fonts for English, Hinglish & Hindi
    ass = [
        '[Script Info]',
        'ScriptType: v4.00+',
        f'PlayResX: {target_w}',
        f'PlayResY: {target_h}',
        'WrapStyle: 2',
        '[V4+ Styles]',
        'Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding',
        f'Style: Main,{HINDI_FONTS},{int(72 * scale_factor)},&H00FFFFFF,&H0000FFFF,&H00100B18,&H99000000,-1,0,0,0,100,100,0,0,1,5,2,5,50,50,0,1',
        '[Events]',
        'Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text'
    ]
    
    srt = []
    max_char_limit = int(960 / (STYLES.get(style, ('', '', '', 58, 0))[3] * 0.62))
    groups = group_words(words, max_chars=max_char_limit)

    for i, group in enumerate(groups):
        if not group:
            continue
        begin = group[0]['start']
        end = group[-1]['end']
        line = ' '.join(safe(w['text']) for w in group)
        srt.append(f'{i + 1}\n{stamp(begin)} --> {stamp(end)}\n{line}\n')
        
        if style == 'clean':
            fs = int(58 * scale_factor)
            bord = max(2, int(3 * scale_factor))
            ass.append(f'Dialogue: 1,{stamp(begin, True)},{stamp(end, True)},Main,,0,0,0,,{{\\pos({center_x},{scaled_pos})\\fs{fs}\\bord{bord}}}{line}')
        else:
            for n, word in enumerate(group):
                a = max(begin, word['start'])
                b = group[n + 1]['start'] if n + 1 < len(group) else end
                if b <= a:
                    continue
                rest, active, extra, size, pop = STYLES[style]
                scaled_size = int(size * scale_factor)
                chunks = []
                for k, w in enumerate(group):
                    t = safe(w['text']).upper()
                    if k == n and pop:
                        chunks.append('{\\c' + active + r'\fscx100\fscy100\t(0,110,\fscx' + str(pop) + r'\fscy' + str(pop) + r')\t(110,230,\fscx100\fscy100)}' + t + r'{\fscx100\fscy100}')
                    else:
                        chunks.append('{\\c' + (active if k == n else rest) + '}' + t)
                text = r'{\pos(' + str(center_x) + ',' + str(scaled_pos) + r')\fs' + str(scaled_size) + extra + '}' + ' '.join(chunks)
                ass.append(f'Dialogue: 1,{stamp(a, True)},{stamp(b, True)},Main,,0,0,0,,{text}')

    if hook.strip():
        lines = hook_lines(hook)
        base_size = 82 if len(lines) == 1 else 74 if len(lines) == 2 else 66
        size = int(base_size * scale_factor)
        text = r'\N'.join(lines).upper()
        bord = max(4, int(10 * scale_factor))
        tags = r'{\an8\pos(%d,%d)\fs%d\b1\c&H00FFFFFF&\3c&H00A845FF&\bord%d\shad0\blur1\fad(150,200)}' % (center_x, scaled_hook_y, size, bord)
        ass.append('Dialogue: 2,0:00:00.00,0:00:03.20,Main,,0,0,0,,' + tags + text)

    (folder / (name + '.ass')).write_text('\n'.join(ass), encoding='utf-8')
    (folder / (name + '.srt')).write_text('\n'.join(srt), encoding='utf-8')
    return name + '.ass', name + '.srt'
