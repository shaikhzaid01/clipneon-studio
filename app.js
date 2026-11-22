// ClipNeon Studio - Production Client Application
const $ = id => document.getElementById(id);
const v = $('video'), c = $('source'), ctx = c.getContext('2d'), pc = $('preview').getContext('2d');

ctx.imageSmoothingEnabled = true;
ctx.imageSmoothingQuality = 'high';
pc.imageSmoothingEnabled = true;
pc.imageSmoothingQuality = 'high';

let id = null, still = null, sourceW = 0, sourceH = 0, sourceDuration = 0;
let mode = 'cam', layout = 'stack', style = 'gta', drag = null;
let highlights = [], words = null, wordRange = '', pendingImport = null, importToken = 0, selectedTime = 0;
let rectEdited = false, layoutClicked = false;
let currentActiveJob = null;

let rect = {
  cam: { x: 0, y: 0, w: .25, h: .32 },
  game: { x: .25, y: 0, w: .75, h: 1 }
};

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function showToast(message, type = 'info') {
  const container = $('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.append(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-10px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

const status = (t, detail = '') => {
  $('status').textContent = t;
  if (detail) $('detail').textContent = detail;
};

async function request(path, body) {
  let response;
  try {
    response = await fetch(path, body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  } catch (err) {
    throw new Error('Connection error. Server may be busy, retrying...');
  }

  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch (e) {
    if (response.status === 524 || response.status === 504) {
      throw new Error('Server task in progress. Background processing active.');
    }
    throw new Error(`Server response (${response.status}): please wait a moment.`);
  }

  if (!response.ok) throw Error(data.detail || data.error || 'Request failed');
  return data;
}

function signature() {
  return [id, Number($('start').value), Number($('end').value), $('language').value].join(':');
}

function clearWords() {
  words = null;
  wordRange = '';
  $('words').replaceChildren();
}

function setRange(start, end) {
  $('start').value = start;
  $('end').value = end;
  clearWords();
}

function setSource(j, seek = 0) {
  id = j.id;
  sourceW = j.w;
  sourceH = j.h;
  sourceDuration = j.duration;
  if (!j.fast_path) {
    still = null;
    v.onloadeddata = () => {
      if (seek > 0 && seek < v.duration) v.currentTime = seek;
      $('source-mode').textContent = 'SOURCE READY (4K/HD)';
      $('empty-source').hidden = true;
      if ($('playback-bar')) $('playback-bar').style.display = 'flex';
      updateTimeDisplay();
    };
    v.src = '/media/' + id;
  }
  c.width = j.w;
  c.height = j.h;
  if (j.preview_frames?.length && (!still || v.readyState < 2)) {
    addFrames(j.preview_frames, j.preview_meta || { duration: j.duration, w: j.w, h: j.h });
  }
  if (still || v.readyState >= 2) {
    $('empty-source').hidden = true;
  } else {
    $('empty-source').hidden = false;
    $('empty-source').innerHTML = '<span class="icon">🎬</span><p>Loading preview frames...</p><small>Your high quality video frame will appear here shortly</small>';
  }
  $('source-mode').textContent = j.fast_path ? 'FAST 4K CLIPPING READY' : 'SOURCE READY (4K/HD)';
}

async function requireSource() {
  if (pendingImport && !id) {
    status('Source ready. Preparing layout...');
    await pendingImport;
  }
  if (!id) throw Error('Please load a video link or upload an MP4 first.');
}

function showFrame(frame, meta, button) {
  const image = new Image();
  image.onload = () => {
    still = image;
    sourceW = image.naturalWidth;
    sourceH = image.naturalHeight;
    c.width = sourceW;
    c.height = sourceH;
    sourceDuration = meta?.duration || sourceDuration;
    selectedTime = frame.time;
    $('empty-source').hidden = true;
    $('source-mode').textContent = '1080p/4K HD FRAME';
    v.pause();
    document.querySelectorAll('.frame-btn').forEach(b => b.classList.remove('active'));
    button?.classList?.add('active');
    if (typeof draw === 'function') draw();
  };
  image.src = frame.url;
}

function addFrames(frames, meta) {
  if (!frames || !frames.length) return;
  const sig = frames.map(f => f.url + (f.kind || '')).join(';');
  if (window._lastFramesSig === sig) return;
  window._lastFramesSig = sig;
  const fmtTime = t => t >= 3600 ? Math.floor(t / 3600) + 'h ' + Math.floor((t % 3600) / 60) + 'm' : t >= 60 ? Math.floor(t / 60) + 'm ' + Math.round(t % 60) + 's' : Math.round(t) + 's';
  $('frames').replaceChildren();
  frames.forEach((frame, index) => {
    const button = document.createElement('button');
    button.className = 'frame-btn';
    const image = document.createElement('img');
    image.src = frame.url;
    image.alt = 'Frame at ' + frame.time + 's';
    const label = document.createElement('small');
    label.textContent = fmtTime(frame.time);
    button.append(image, label);
    button.onclick = () => showFrame(frame, meta, button);
    $('frames').append(button);
    if (index === 0) {
      showFrame(frame, meta, button);
      if (meta?.duration && Number($('end').value) > meta.duration) $('end').value = meta.duration;
      status('Preview frames ready. Fine-tune your framing in Step 03.');
    }
  });
}

// Import Video URL
$('link').onclick = async () => {
  const url = $('url').value.trim();
  if (!url) {
    showToast('Please paste a YouTube, Twitch or Kick video link.', 'error');
    return;
  }
  const token = ++importToken;
  id = null; still = null; clearWords();
  $('frames').replaceChildren();
  $('link').disabled = true;
  $('progress-wrap').hidden = false;
  $('progress').style.width = '5%';
  $('link-status').textContent = 'Fetching video info and preview frames...';

  const operation = (async () => {
    const { job } = await request('/link', { url });
    currentActiveJob = job;
    let completed = false;
    for (;;) {
      const s = await request('/status/' + job);
      if (token !== importToken) return;
      if (s.preview_frames) addFrames(s.preview_frames, s.preview_meta);
      $('link-status').textContent = s.status === 'done' ? 'Video loaded successfully.' : (s.message || s.stage || 'Importing...');
      $('progress').style.width = (s.status === 'done' ? 100 : Math.min(95, (s.progress || .05) * 300)) + '%';
      if (s.status === 'error') throw Error(s.error);
      if (s.status === 'done' && !completed) {
        completed = true;
        setSource(s, selectedTime);
        if (!Number($('end').value) || Number($('end').value) > s.duration) $('end').value = Math.min(30, s.duration);
        showToast('Video loaded! Set your framing or click ⚡ Make 4K Clips.', 'success');
      }
      if (completed && (s.preview_status !== 'loading' || (s.preview_frames?.length || 0) >= 1)) break;
      await sleep(1200);
    }
  })();
  pendingImport = operation;
  try {
    await operation;
  } catch (e) {
    showToast(e.message, 'error');
    $('link-status').textContent = e.message;
  } finally {
    if (token === importToken) {
      pendingImport = null;
      $('link').disabled = false;
    }
  }
};

// File Upload
$('file').onchange = async () => {
  try {
    const f = $('file').files[0];
    if (!f) return;
    ++importToken;
    pendingImport = null; id = null; still = null;
    $('frames').replaceChildren();
    showToast('Uploading video file...', 'info');
    const form = new FormData();
    form.append('file', f);
    const response = await fetch('/upload', { method: 'POST', body: form });
    const j = await response.json();
    if (!response.ok) throw Error(j.detail);
    $('url').value = '';
    setSource(j);
    setRange(0, Math.min(30, j.duration));
    showToast('File uploaded successfully! Ready to clip.', 'success');
  } catch (e) {
    showToast(e.message, 'error');
  }
};

// Canvas Painting & Interactive Drag
['cam', 'game'].forEach(k => $(k).onclick = () => {
  mode = k;
  ['cam', 'game'].forEach(x => $(x).classList.toggle('active', x === k));
});

function point(e) {
  const b = c.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (e.clientX - b.left) / b.width)),
    y: Math.max(0, Math.min(1, (e.clientY - b.top) / b.height))
  };
}

c.onpointerdown = e => {
  if (!still && v.readyState < 2) return;
  drag = point(e);
  c.setPointerCapture(e.pointerId);
};

c.onpointermove = e => {
  if (!drag) return;
  rectEdited = true;
  const p = point(e);
  rect[mode] = {
    x: Math.min(p.x, drag.x),
    y: Math.min(p.y, drag.y),
    w: Math.max(.01, Math.abs(p.x - drag.x)),
    h: Math.max(.01, Math.abs(p.y - drag.y))
  };
};

c.onpointerup = () => drag = null;
c.onpointercancel = () => drag = null;

$('save').onclick = () => {
  localStorage.setItem('clipneon-crop', JSON.stringify({ rect, top: $('top').value, layout }));
  showToast('Layout settings saved locally.', 'success');
};

$('load').onclick = () => {
  try {
    const j = JSON.parse(localStorage.getItem('clipneon-crop') || 'null');
    if (j) {
      rect = j.rect;
      rectEdited = true;
      $('top').value = j.top;
      setLayout(j.layout || 'stack');
      showToast('Saved layout loaded.', 'success');
    } else {
      showToast('No saved layout found.', 'info');
    }
  } catch (e) {
    showToast('Failed to load saved layout.', 'error');
  }
};

function setLayout(value) {
  layout = value;
  document.querySelectorAll('[data-layout]').forEach(b => b.classList.toggle('active', b.dataset.layout === layout));
  $('height-control').hidden = layout !== 'stack';
  if (layout !== 'stack') {
    preset = 'none';
    document.querySelectorAll('[data-preset]').forEach(b => b.classList.toggle('on', b.dataset.preset === 'none'));
  }
}

document.querySelectorAll('[data-layout]').forEach(b => b.onclick = () => {
  layoutClicked = true;
  setLayout(b.dataset.layout);
});

document.querySelectorAll('[data-style]').forEach(b => b.onclick = () => {
  style = b.dataset.style;
  document.querySelectorAll('[data-style]').forEach(x => x.classList.toggle('active', x === b));
});

function formatTime(sec) {
  if (isNaN(sec) || !isFinite(sec)) return '00:00';
  sec = Math.floor(sec);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m < 10 ? '0' : ''}${m}:${s < 10 ? '0' : ''}${s}`;
}

function updateTimeDisplay() {
  const cur = v.currentTime || 0;
  const dur = v.duration || sourceDuration || 0;
  if ($('time-display')) {
    $('time-display').textContent = `${formatTime(cur)} / ${formatTime(dur)}`;
  }
  if ($('seek-bar') && dur > 0) {
    $('seek-bar').value = (cur / dur) * 100;
  }
}

$('play-btn')?.addEventListener('click', () => {
  if (v.paused) {
    v.play();
    still = null;
    if ($('play-btn')) $('play-btn').textContent = '⏸';
  } else {
    v.pause();
    if ($('play-btn')) $('play-btn').textContent = '▶';
  }
});

v.addEventListener('play', () => {
  still = null;
  if ($('play-btn')) $('play-btn').textContent = '⏸';
});

v.addEventListener('pause', () => {
  if ($('play-btn')) $('play-btn').textContent = '▶';
});

v.addEventListener('timeupdate', () => {
  updateTimeDisplay();
  const end = Number($('end').value);
  if (end > Number($('start').value) && v.currentTime > end && end < v.duration) {
    v.pause();
  }
});

$('seek-bar')?.addEventListener('input', () => {
  const dur = v.duration || sourceDuration || 0;
  if (dur > 0) {
    const target = ($('seek-bar').value / 100) * dur;
    v.currentTime = target;
    still = null;
    updateTimeDisplay();
  }
});

$('set-start-btn')?.addEventListener('click', () => {
  const t = parseFloat((v.currentTime || 0).toFixed(1));
  $('start').value = t;
  clearWords();
  showToast(`Clip Start set to ${t}s`, 'info');
});

$('set-end-btn')?.addEventListener('click', () => {
  const t = parseFloat((v.currentTime || 0).toFixed(1));
  $('end').value = t;
  clearWords();
  showToast(`Clip End set to ${t}s`, 'info');
});

v.onplay = () => {
  still = null;
  if ($('play-btn')) $('play-btn').textContent = '⏸';
};
v.ontimeupdate = () => {
  updateTimeDisplay();
  const end = Number($('end').value);
  if (end > Number($('start').value) && v.currentTime > end && end < v.duration) v.pause();
};

['start', 'end', 'language'].forEach(key => $(key).addEventListener('change', () => {
  clearWords();
  if (key === 'start' && id) v.currentTime = Math.max(0, Math.min(v.duration || Infinity, Number($('start').value)));
}));

function cover(source, r, x, y, w, h) {
  const sw = r.w * sourceW, sh = r.h * sourceH;
  const ratio = Math.max(w / sw, h / sh);
  const cw = w / ratio, ch = h / ratio;
  pc.drawImage(source, r.x * sourceW + (sw - cw) / 2, r.y * sourceH + (sh - ch) / 2, cw, ch, x, y, w, h);
}

function captionPreview() {
  if (!$('captions').checked) return;
  let line = 'VIRAL 4K HIGHLIGHT';
  if (words && wordRange === signature() && words.length) {
    const time = Math.max(0, v.currentTime - Number($('start').value));
    let index = words.findIndex(w => time >= w.start && time <= w.end);
    if (index < 0) index = 0;
    line = words.slice(Math.floor(index / 3) * 3, Math.floor(index / 3) * 3 + 3).map(w => w.text).join(' ');
  }
  pc.save();
  pc.textAlign = 'center';
  pc.textBaseline = 'middle';
  pc.font = (style === 'clean' ? '600 26px' : '900 34px') + " 'Plus Jakarta Sans', Arial";
  const max = 460;
  while (pc.measureText(line).width > max) {
    const size = parseFloat(pc.font.match(/(\d+)px/)[1]);
    pc.font = '900 ' + Math.max(12, size - 1) + "px 'Plus Jakarta Sans', Arial";
    if (size <= 12) break;
  }
  const y = Number($('caption-y').value) / 2;
  pc.lineWidth = 6;
  pc.strokeStyle = '#08080C';
  pc.shadowBlur = style === 'gta' ? 12 : 0;
  pc.shadowColor = '#FF007F';
  pc.strokeText(line, 270, y);
  pc.fillStyle = style === 'gta' ? '#00F2FE' : style === 'bold' ? '#10B981' : style === 'mrbeast' ? '#FFFF00' : '#FFFFFF';
  pc.fillText(line, 270, y);
  pc.restore();
}

function drawBoundingBoxWithLabel(k, r) {
  const isCam = k === 'cam';
  const color = isCam ? '#FF007F' : '#00F2FE';
  const label = isCam ? 'Facecam' : 'Gameplay';
  
  ctx.strokeStyle = color;
  ctx.lineWidth = Math.max(3, c.width / 320);
  ctx.fillStyle = isCam ? 'rgba(255, 0, 127, 0.12)' : 'rgba(0, 242, 254, 0.08)';
  
  const bx = r.x * c.width, by = r.y * c.height, bw = r.w * c.width, bh = r.h * c.height;
  ctx.fillRect(bx, by, bw, bh);
  ctx.strokeRect(bx, by, bw, bh);
  
  // Badge Label
  ctx.fillStyle = color;
  ctx.fillRect(bx, by, 75, 22);
  ctx.fillStyle = '#08080C';
  ctx.font = '700 11px sans-serif';
  ctx.fillText(label, bx + 8, by + 15);
}

function paint() {
  const source = still || (v.readyState >= 2 ? v : null);
  if (source) {
    if (!still) {
      sourceW = v.videoWidth;
      sourceH = v.videoHeight;
    }
    if (sourceW && sourceH) {
      ctx.drawImage(source, 0, 0, c.width, c.height);
      ['game', 'cam'].forEach(k => {
        drawBoundingBoxWithLabel(k, rect[k]);
      });
      pc.clearRect(0, 0, 540, 960);
      if (layout === 'stack') {
        const h = Math.floor(Number($('top').value) / 100 * 960);
        cover(source, rect.cam, 0, 0, 540, h);
        cover(source, rect.game, 0, h, 540, 960 - h);
      } else if (layout === 'game') {
        cover(source, rect.game, 0, 0, 540, 960);
      } else if (layout === 'fill') {
        cover(source, { x: 0, y: 0, w: 1, h: 1 }, 0, 0, 540, 960);
      } else {
        pc.save();
        pc.filter = 'blur(16px)';
        cover(source, { x: 0, y: 0, w: 1, h: 1 }, -15, -15, 570, 990);
        pc.restore();
        const ratio = Math.min(540 / sourceW, 960 / sourceH);
        const w = sourceW * ratio, h = sourceH * ratio;
        pc.drawImage(source, 0, 0, sourceW, sourceH, (540 - w) / 2, (960 - h) / 2, w, h);
      }
      captionPreview();
    }
  }
  $('height-value').textContent = $('top').value + '%';
  requestAnimationFrame(paint);
}
paint();

function renderWords() {
  const editor = $('words');
  editor.replaceChildren();
  if (!words || !words.length) {
    editor.innerHTML = '<div class="empty-words-notice">No speech detected in this range.</div>';
    return;
  }
  for (const word of words) {
    const input = document.createElement('input');
    input.value = word.text;
    input.title = word.start.toFixed(2) + '–' + word.end.toFixed(2) + 's';
    input.oninput = () => word.text = input.value;
    editor.append(input);
  }
}

async function generateSubtitles() {
  await requireSource();
  showToast('Generating AI timestamps and subtitles...', 'info');
  const range = signature();
  const { job } = await request('/transcribe', { id, start: Number($('start').value), end: Number($('end').value), language: $('language').value });
  for (;;) {
    const s = await request('/status/' + job);
    if (s.status === 'error') throw Error(s.error);
    if (s.status === 'done') {
      words = s.words;
      wordRange = range;
      renderWords();
      showToast('Subtitles generated! You can edit any word above.', 'success');
      return;
    }
    await sleep(1000);
  }
}
$('transcribe').onclick = async () => {
  $('transcribe').disabled = true;
  try {
    await generateSubtitles();
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    $('transcribe').disabled = false;
  }
};

function getClipCount() {
  const mode = $('count-mode')?.value || 'auto';
  if (mode === 'auto') return 'auto';
  if (mode === 'custom') return Math.max(1, Math.min(30, parseInt($('count')?.value) || 5));
  return Number(mode) || 'auto';
}

function updateCustomCountDisplay() {
  const isCustom = $('count-mode')?.value === 'custom';
  const wrap = $('custom-count-wrap');
  if (wrap) {
    wrap.style.display = isCustom ? 'flex' : 'none';
    if (isCustom) {
      setTimeout(() => {
        $('count')?.focus();
        $('count')?.select();
      }, 50);
    }
  }
}

$('count-mode')?.addEventListener('change', updateCustomCountDisplay);

$('count-minus')?.addEventListener('click', (e) => {
  e.preventDefault();
  const input = $('count');
  if (input) {
    const cur = parseInt(input.value) || 5;
    input.value = Math.max(1, cur - 1);
  }
});

$('count-plus')?.addEventListener('click', (e) => {
  e.preventDefault();
  const input = $('count');
  if (input) {
    const cur = parseInt(input.value) || 5;
    input.value = Math.min(30, cur + 1);
  }
});

function initCustomSelects() {
  const selects = document.querySelectorAll('select');
  selects.forEach(select => {
    if (select.dataset.customized === 'true') return;
    select.dataset.customized = 'true';

    select.style.display = 'none';

    const wrapper = document.createElement('div');
    wrapper.className = 'custom-select-wrapper';

    const trigger = document.createElement('div');
    trigger.className = 'custom-select-trigger';

    const label = document.createElement('span');
    label.className = 'custom-select-label';
    const selectedOption = select.options[select.selectedIndex] || select.options[0];
    label.textContent = selectedOption ? selectedOption.textContent : 'Select...';

    const arrow = document.createElement('span');
    arrow.className = 'custom-select-arrow';
    arrow.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"></polyline></svg>`;

    trigger.append(label, arrow);

    const menu = document.createElement('div');
    menu.className = 'custom-select-menu';

    function buildOptions() {
      menu.replaceChildren();
      Array.from(select.options).forEach((opt, idx) => {
        const optionItem = document.createElement('div');
        optionItem.className = 'custom-select-option' + (opt.selected ? ' selected' : '');
        optionItem.dataset.value = opt.value;

        const optText = document.createElement('span');
        optText.className = 'option-text';
        optText.textContent = opt.textContent;

        const optCheck = document.createElement('span');
        optCheck.className = 'option-check';
        optCheck.textContent = opt.selected ? '✓' : '';

        optionItem.append(optText, optCheck);

        optionItem.onclick = (e) => {
          e.stopPropagation();
          select.value = opt.value;
          select.selectedIndex = idx;
          label.textContent = opt.textContent;

          menu.querySelectorAll('.custom-select-option').forEach(el => {
            el.classList.remove('selected');
            const check = el.querySelector('.option-check');
            if (check) check.textContent = '';
          });
          optionItem.classList.add('selected');
          optCheck.textContent = '✓';

          wrapper.classList.remove('open');
          select.dispatchEvent(new Event('change', { bubbles: true }));
        };

        menu.append(optionItem);
      });
    }

    buildOptions();

    trigger.onclick = (e) => {
      e.stopPropagation();
      const isOpen = wrapper.classList.contains('open');
      document.querySelectorAll('.custom-select-wrapper.open').forEach(w => {
        if (w !== wrapper) w.classList.remove('open');
      });
      if (!isOpen) {
        buildOptions();
        wrapper.classList.add('open');
      } else {
        wrapper.classList.remove('open');
      }
    };

    wrapper.append(trigger, menu);
    select.parentNode.insertBefore(wrapper, select.nextSibling);

    select.addEventListener('change', () => {
      const curOpt = select.options[select.selectedIndex];
      if (curOpt) label.textContent = curOpt.textContent;
    });
  });
}

document.addEventListener('click', () => {
  document.querySelectorAll('.custom-select-wrapper.open').forEach(w => w.classList.remove('open'));
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.custom-select-wrapper.open').forEach(w => w.classList.remove('open'));
  }
});

function exportSettings(source, start, end) {
  return {
    id: source,
    url: $('url').value.trim(),
    ...rect,
    layout,
    aspect: $('export-aspect').value,
    resolution: $('export-res').value,
    top: Number($('top').value) / 100,
    start,
    end,
    captions: $('captions').checked,
    caption_style: style,
    caption_y: Number($('caption-y').value),
    language: $('language').value,
    hook: $('hook').value,
    title: $('clip-title').value,
    post_caption: $('post-caption').value,
    words: words && wordRange === signature() ? words : null
  };
}

async function runExport(body, prefix = '') {
  const { job } = await request('/export', body);
  currentActiveJob = job;
  for (;;) {
    const s = await request('/status/' + job);
    status(prefix + (s.message || 'Rendering in 4K Ultra HD...'));
    if (s.status === 'error') throw Error(s.error);
    if (s.status === 'done') return s;
    await sleep(900);
  }
}

function showDownload(s) {
  $('download').href = '/media/' + s.file;
  $('download').hidden = false;
  $('srt').hidden = !s.srt;
  if (s.srt) $('srt').href = s.srt;
}

$('export').onclick = async () => {
  const btn = $('export');
  btn.disabled = true;
  $('download').hidden = true;
  $('srt').hidden = true;
  try {
    await requireSource();
    const s = await runExport(exportSettings(id, Number($('start').value), Number($('end').value)));
    showDownload(s);
    showToast(`4K Clip Exported (${s.resolution})!`, 'success');
    status('Clip Ready · ' + s.resolution + ' · ' + s.encoder);
    await refreshLibrary();
  } catch (e) {
    showToast(e.message, 'error');
    status(e.message);
  } finally {
    btn.disabled = false;
  }
};

// Clipboard Copy Helper
function copyToClipboard(text, label = 'Copied') {
  navigator.clipboard.writeText(text).then(() => {
    showToast(`${label} copied to clipboard!`, 'success');
  }).catch(() => {
    showToast('Failed to copy to clipboard', 'error');
  });
}

function renderClipCard(c) {
  const card = document.createElement('div');
  card.className = 'clip-card-studio';

  const vid = document.createElement('video');
  vid.src = '/media/' + c.file;
  vid.controls = true;
  vid.preload = 'metadata';

  const title = document.createElement('h3');
  title.textContent = c.title || 'Viral Clip';

  const metaTag = document.createElement('div');
  metaTag.className = 'clip-meta-tag';
  metaTag.textContent = `${Math.round(c.start)}–${Math.round(c.end)}s • Viral Score ${c.score}/100`;

  const hook = document.createElement('span');
  hook.className = 'hook-quote';
  hook.textContent = c.hook ? `"${c.hook}"` : '';

  // Social media copy buttons
  const actions = document.createElement('div');
  actions.className = 'clip-actions';

  const copyGroup = document.createElement('div');
  copyGroup.className = 'btn-copy-group';

  const sm = c.social_metadata || {};

  const btnYT = document.createElement('button');
  btnYT.className = 'btn-mini';
  btnYT.textContent = '📋 YouTube Copy';
  btnYT.onclick = () => copyToClipboard(`${sm.title || c.title}\n\n${sm.youtube_description || ''}`, 'YouTube Title & Description');

  const btnInsta = document.createElement('button');
  btnInsta.className = 'btn-mini';
  btnInsta.textContent = '📋 Instagram Copy';
  btnInsta.onclick = () => copyToClipboard(sm.instagram_caption || `${c.title}\n\n#Viral #Reels #Shorts`, 'Instagram Caption');

  const btnTikTok = document.createElement('button');
  btnTikTok.className = 'btn-mini';
  btnTikTok.textContent = '📋 TikTok Copy';
  btnTikTok.onclick = () => copyToClipboard(sm.tiktok_caption || `${c.title} #fyp #viral`, 'TikTok Caption');

  const btnFB = document.createElement('button');
  btnFB.className = 'btn-mini';
  btnFB.textContent = '📋 Facebook Copy';
  btnFB.onclick = () => copyToClipboard(sm.facebook_caption || `${c.title}`, 'Facebook Post');

  copyGroup.append(btnYT, btnInsta, btnTikTok, btnFB);

  const dlLink = document.createElement('a');
  dlLink.href = '/media/' + c.file;
  dlLink.download = 'clip-4k-' + c.file + '.mp4';
  dlLink.className = 'btn btn-primary';
  dlLink.textContent = '↓ Download 4K Clip';

  actions.append(copyGroup, dlLink);

  card.append(vid, title, metaTag, hook, actions);
  return card;
}

// ⚡ Make 4K Clips Engine
$('make').onclick = async () => {
  const url = $('url').value.trim();
  if (!url && !id) {
    showToast('Please paste a video link or upload an MP4 first.', 'error');
    return;
  }
  const btn = $('make');
  btn.disabled = true;
  $('make-results').hidden = false;
  $('cancel-job-btn').hidden = false;
  $('make-grid').replaceChildren();
  $('make-progress').style.width = '2%';
  $('make-results').scrollIntoView({ behavior: 'smooth', block: 'start' });

  try {
    const { job } = await request('/make', {
      ...(url ? { url } : { id }),
      count: getClipCount(),
      mode: $('mode').value,
      resolution: $('export-res').value,
      aspect: $('export-aspect').value,
      min_len: Number($('minlen').value),
      max_len: Number($('length').value),
      layout: makeLayout(),
      cam: rect.cam,
      game: rect.game,
      top: Number($('top').value) / 100,
      caption_style: style,
      language: $('language').value
    });

    currentActiveJob = job;
    const shown = new Set();

    for (;;) {
      const s = await request('/make/' + job);
      $('make-status').textContent = (s.stage || s.status) + ' • ' + Math.round((s.progress || 0) * 100) + '%';
      $('make-progress').style.width = Math.max(3, (s.progress || 0) * 100) + '%';

      (s.clips || []).forEach(c => {
        if (shown.has(c.file)) return;
        shown.add(c.file);
        const card = renderClipCard(c);
        $('make-grid').append(card);
      });

      if (s.status === 'error') throw Error(s.error);
      if (s.status === 'done') {
        $('make-status').textContent = 'Done • ' + (s.clips || []).length + ' 4K clips ready!';
        $('cancel-job-btn').hidden = true;
        showToast('All 4K Clips Rendered! Ready to download and copy social captions.', 'success');
        await refreshLibrary();
        break;
      }
      await sleep(1000);
    }
  } catch (e) {
    showToast(e.message, 'error');
    $('make-status').textContent = e.message;
  } finally {
    btn.disabled = false;
  }
};

// Cancel Job
$('cancel-job-btn').onclick = async () => {
  if (!currentActiveJob) return;
  try {
    await fetch('/cancel/' + currentActiveJob, { method: 'POST' });
    showToast('Job cancellation requested.', 'info');
    $('cancel-job-btn').hidden = true;
  } catch (e) {
    showToast('Failed to cancel job', 'error');
  }
};

// Disk Cleanup
$('clean-disk-btn').onclick = async () => {
  try {
    showToast('Cleaning temporary chunk files...', 'info');
    const res = await request('/cleanup');
    showToast(`Cleaned ${res.deleted_files} temp files, freed ${res.freed_mb} MB!`, 'success');
  } catch (e) {
    showToast('Cleanup failed: ' + e.message, 'error');
  }
};

// Library
async function refreshLibrary() {
  const clips = await request('/library');
  $('library-count').textContent = clips.length;
  const host = $('library');
  host.replaceChildren();
  if (!clips.length) {
    host.innerHTML = '<div class="empty-words-notice" style="grid-column:1/-1; text-align:center; padding:50px;">Your rendered clips will appear here. Create your first clip to start.</div>';
    return;
  }
  for (const clip of clips) {
    const card = renderClipCard({
      file: clip.id,
      title: clip.title,
      start: clip.settings?.start || 0,
      end: clip.settings?.end || (clip.meta?.duration || 30),
      score: 95,
      hook: clip.settings?.hook || '',
      social_metadata: clip.social_metadata
    });
    host.append(card);
  }
}

document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => {
  document.querySelectorAll('[data-view]').forEach(x => x.classList.toggle('active', x === b));
  $('editor-view').hidden = b.dataset.view !== 'editor';
  $('library-view').hidden = b.dataset.view !== 'library';
  if (b.dataset.view === 'library') refreshLibrary();
});
$('refresh-library').onclick = () => refreshLibrary();

// Presets
const PRESETS = {
  tl: { cam: { x: 0, y: 0, w: .24, h: .32 }, game: { x: .24, y: 0, w: .76, h: 1 } },
  tr: { cam: { x: .76, y: 0, w: .24, h: .32 }, game: { x: 0, y: 0, w: .76, h: 1 } },
  bl: { cam: { x: 0, y: .68, w: .24, h: .32 }, game: { x: .24, y: 0, w: .76, h: 1 } },
  br: { cam: { x: .76, y: .68, w: .24, h: .32 }, game: { x: 0, y: 0, w: .76, h: 1 } },
  bc: { cam: { x: .37, y: .68, w: .26, h: .32 }, game: { x: 0, y: 0, w: 1, h: .68 } }
};
let preset = null;

function applyPreset(k, save = true) {
  preset = k;
  document.querySelectorAll('[data-preset]').forEach(b => b.classList.toggle('on', b.dataset.preset === k));
  if (k === 'none') {
    if (layout === 'stack') {
      setLayout('fill');
    }
  } else {
    rectEdited = true;
    rect = { cam: { ...PRESETS[k].cam }, game: { ...PRESETS[k].game } };
    setLayout('stack');
    try { paint(); } catch (e) { }
  }
  if (save) try { localStorage.setItem('clipneon-preset', k); } catch (e) { }
}
document.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => applyPreset(b.dataset.preset));
try {
  const k = localStorage.getItem('clipneon-preset');
  if (k && k !== 'none') applyPreset(k, false);
} catch (e) { }

function makeLayout() {
  return layout || 'fill';
}

refreshLibrary();
initCustomSelects();
