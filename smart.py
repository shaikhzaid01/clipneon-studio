"""Smart highlight refinement: shortlist by motion/audio, read each candidate's
transcript locally, score and generate social media metadata (YouTube, Instagram, TikTok, Facebook)."""
import json, urllib.request, re
from highlights import rank
import excite, learn
import numpy as np
from captions import transcribe
from metadata_gen import generate_social_metadata

SCOUT_MODEL = 'base'
OLLAMA = 'http://127.0.0.1:11434'

PROMPT = ('You pick viral short-form clips (Reels/Shorts) from gaming streams, podcasts and vlogs. First decide the content type '
          '(entertainment, gaming reaction, knowledge, opinion, story). Then score the candidate clip.\n'
          'HIGH score: strong emotion (laughter, shock, rage, hype), a surprise or punchline, a quotable line, a clear beginning-to-payoff, '
          'real reactions, a hot take, a funny fail.\n'
          'LOW score: intros/outros, "like and subscribe", sponsor reads, reading chat messages, small talk, silence, half-finished thoughts.\n'
          'Use ONLY words from the transcript; never invent facts.\n'
          'Transcript of a candidate clip:\n"""{t}"""\n'
          'Reply ONLY with JSON: {{"score":1-10,"title":"max 7 words, punchy, English, no quotes","hook":"max 6 words taken or paraphrased from what is SAID, shown on screen in the first 3 seconds"}}')

def _model():
    try:
        tags = json.load(urllib.request.urlopen(OLLAMA + '/api/tags', timeout=2))['models']
        names = [m['name'] for m in tags]
        for pref in ('llama3.2', 'qwen', 'gemma', 'llama'):
            for n in names:
                if pref in n:
                    return n
        return names[0] if names else None
    except Exception:
        return None

def _ask(model, text):
    try:
        body = json.dumps({'model': model, 'prompt': PROMPT.format(t=text[:1500]), 'stream': False, 'format': 'json', 'options': {'temperature': .3}}).encode()
        req = urllib.request.Request(OLLAMA + '/api/generate', body, {'Content-Type': 'application/json'})
        raw_res = json.load(urllib.request.urlopen(req, timeout=45))['response']
        match = re.search(r'\{.*\}', raw_res, re.DOTALL)
        if match:
            out = json.loads(match.group(0))
            score = max(1, min(10, float(out.get('score', 5))))
            title = str(out.get('title', '')).strip()[:70]
            hook = str(out.get('hook', '')).strip()[:50]
            return score, title, hook
    except Exception:
        pass
    return 5.0, "", ""

def snap(c, words, min_len, max_len):
    """Start where speech starts, end on a sentence end inside [min_len, max_len]. Word times are shifted with the start."""
    if not words:
        return
    lead = words[0]['start']
    if lead > 2.5:
        c['start'] = round(c['start'] + lead - .4, 2)
        for w in words:
            w['start'] = round(w['start'] - lead + .4, 3)
            w['end'] = round(w['end'] - lead + .4, 3)
    limit = c['end'] - c['start']
    ends = [w['end'] for w in words if w['end'] <= min(limit, max_len)]
    good = [w['end'] for w in words if w['end'] <= min(limit, max_len) and w['text'].rstrip()[-1:] in '.?!…।' and w['end'] >= min_len]
    pick = good[-1] if good else (ends[-1] if ends and ends[-1] >= min_len else None)
    c['end'] = round(c['start'] + (pick + .3 if pick else min(limit, max_len)), 2)

def find(src, duration, count, length, report, language=None, data=None, min_len=None, motion=True, words=None, deep=False, weights=None, select=True, video_title=''):
    """Return up to `count` clips, best first."""
    pool = (count * 4 + 2) if deep else (count * 2 + 1 if count < 6 else count + 4)
    if not words:
        pool = count + 2 if not deep else min(pool, count * 2 + 2)
    clips = []
    extra = excite.seconds_signal(words, duration) if words else None
    weights = weights or dict(learn.DEFAULT)
    while length >= 10:
        clips = rank(src, duration, pool, length, lambda s: report(.0 + .3 * s / max(duration, 1), f'Scanning motion & audio: {int(s)}/{int(duration)}s'), motion, extra)
        if len(clips) >= min(pool, count) or duration < length * count * .6 and length <= 15:
            break
        length = max(10, length * .7)
    model = _model()
    engine = 'motion-audio'
    for i, c in enumerate(clips):
        report(.3 + .65 * i / max(1, len(clips)), f'Analyzing highlight candidate {i+1}/{len(clips)}')
        try:
            if words:
                from captions_yt import window
                r = {'words': window(words, c['start'], c['end']), 'language': 'yt'}
            else:
                r = transcribe(src, c['start'], c['end'], data, lambda m: None, language, 'small' if deep else SCOUT_MODEL)
            text = ' '.join(w['text'] for w in r['words'])
            c['transcript'] = text
            c['language'] = r.get('language')
            c['words'] = r['words']
            snap(c, c['words'], min_len or length * .5, length)
            c.setdefault('features', {})['words'] = excite.window_score(c['words'], c['end'] - c['start'])
            
            raw_title = ""
            raw_hook = ""
            ai_score = 5.0
            if len(text.split()) < 4:
                c['reason'] = 'Peak gameplay action'
                c['llm'] = 2
                c['features']['llm'] = .2
            elif model:
                ctx = text
                if deep and words:
                    from captions_yt import window
                    ctx = '[before] ' + ' '.join(w['text'] for w in window(words, max(0, c['start'] - 15), c['start'])) + ' [CLIP] ' + text + ' [after] ' + ' '.join(w['text'] for w in window(words, c['end'], c['end'] + 15))
                ai_score, raw_title, raw_hook = _ask(model, ctx)
                c.update(llm=ai_score, reason=f'AI Viral Score: {ai_score:g}/10')
                c['features']['llm'] = ai_score / 10
                engine = 'motion-audio+' + model

            # Auto-generate full social media metadata package (YouTube, Instagram, TikTok, Facebook)
            meta_pkg = generate_social_metadata(text, raw_title or c.get('title', ''), raw_hook or c.get('hook', ''), video_title=video_title, clip_index=i+1)
            c['title'] = meta_pkg['title']
            c['hook'] = meta_pkg['hook']
            c['social_metadata'] = meta_pkg

        except Exception as e:
            c['reason'] = c.get('reason', '') + f' (note: {str(e)[:50]})'
            meta_pkg = generate_social_metadata(c.get('transcript', ''), c.get('title', ''), c.get('hook', ''), video_title=video_title, clip_index=i+1)
            c['title'] = meta_pkg['title']
            c['hook'] = meta_pkg['hook']
            c['social_metadata'] = meta_pkg

    for c in clips:
        c.setdefault('features', {}).setdefault('words', None)
        c['rank'] = learn.score(c['features'], weights)
        c['score'] = round(c['rank'] * 100)
    clips = sorted(clips, key=lambda c: -c['rank'])
    if select:
        clips = clips[:count]
    return clips, engine
