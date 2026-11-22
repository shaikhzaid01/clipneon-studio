# ClipNeon Studio - 4K AI Viral Video Clipping Suite

> **Turn long videos and streams into high-converting, captioned 4K vertical clips in seconds. 100% local, hardware-accelerated, commercial-grade.**

ClipNeon Studio is a commercial-grade, self-hosted AI video clipping workstation. It automatically converts long-form YouTube, Twitch, Kick streams, or local video files into viral 9:16 vertical clips with animated karaoke captions, intelligent hooks, and ready-to-publish social metadata for YouTube Shorts, Instagram Reels, TikTok, and Facebook.

---

## ⚡ Key Highlights & Capabilities

- **💎 4K Ultra HD Export**:
  - Export ultra-sharp **2160 × 3840 (4K)**, **1440 × 2560 (2K)**, **1080 × 1920 (FHD)**, and **720 × 1280 (HD)** vertical videos.
  - Multi-tap Lanczos interpolation with custom aspect ratio scaling (`9:16`, `1:1`, `16:9`, `4:5`).

- **🚀 Cross-OS Hardware Acceleration**:
  - **Windows & Linux**: Auto-detects NVIDIA NVENC (`h264_nvenc`) and AMD AMF (`h264_amf`) with CPU fallback (`libx264`).
  - **macOS (Apple Silicon & Intel)**: Full hardware acceleration via `h264_videotoolbox`.
  - Automatic thread management and low-latency chunk rendering.

- **🔊 Studio-Grade Audio Normalization**:
  - Built-in **EBU R128 loudness normalization** (`loudnorm`) targeting `-16 LUFS` with `-1.5 dBFS` true peak limit. Dialogue is punchy and audible across phone speakers.

- **🇮🇳 Multi-Language & Hindi/Hinglish Subtitles**:
  - Native Unicode Devanagari font rendering (`Nirmala UI`, `Noto Sans Devanagari`, `Mangal`) without broken glyphs.
  - Built-in automated **Hinglish** conversion engine for romanized Hindi speech.
  - Karaoke-style word highlighting with 5 viral presets (*GTA Neon, Bold Pop, Clean White, Hormozi Impact, Beast Punch*).

- **📱 AI Social Media Copy Suite**:
  - Automatically writes viral titles, hooks, YouTube Shorts descriptions, Instagram Reels captions with emojis, TikTok copy, and hashtags.
  - 1-click clipboard copy buttons directly beneath every rendered clip card.

- **🧹 Automatic Garbage Collection**:
  - Auto-cleans intermediate audio chunks, low-res preview slices, and temporary WAVs to prevent disk bloat.
  - Includes a manual 1-click **"Clean Temp Storage"** button in the header.

- **📐 Interactive Framing Studio**:
  - Visual canvas overlay to drag and position Facecam & Gameplay regions with real-time iPhone 16 Pro simulator preview.
  - Multiple layout modes: **Fill & Crop** (full-bleed center crop), **Blurred Backdrop**, **Face + Gameplay Dual Screen**, and **Selected Region**.

---

## 🛠️ System Requirements

| Component | Minimum | Recommended |
|---|---|---|
| **Operating System** | Windows 10/11 (64-bit), macOS 12+, Ubuntu 20.04+ | Windows 11 / macOS (Apple Silicon M-Series) |
| **Python** | Python 3.10 | Python 3.11 or 3.12 |
| **FFmpeg** | FFmpeg 5.0+ installed and on system PATH | FFmpeg 6.0+ with NVENC or VideoToolbox support |
| **Memory (RAM)** | 8 GB RAM | 16 GB+ RAM |
| **GPU (Optional)** | Integrated Graphics | NVIDIA RTX 2060+ (6GB+ VRAM) / Apple M1/M2/M3/M4 |
| **LLM (Optional)** | None | Ollama running `llama3.2:3b` for semantic moment analysis |

---

## 🚀 Quick Setup & Installation

### 1. Prerequisites

Make sure **Python 3.10+** and **FFmpeg** are installed on your machine.

- **Windows**: Install via `winget`:
  ```powershell
  winget install Gyan.FFmpeg
  winget install Python.Python.3.11
  ```
- **macOS**: Install via `brew`:
  ```bash
  brew install ffmpeg python@3.11
  ```
- **Linux (Ubuntu/Debian)**:
  ```bash
  sudo apt update && sudo apt install -y ffmpeg python3-pip python3-venv
  ```

---

### 2. Clone & Setup Environment

```bash
# Clone the repository
git clone https://github.com/shaikhzaid01/clipneon-studio.git
cd clipneon-studio

# Create a virtual environment
python -m venv .venv

# Activate the virtual environment
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Windows (Command Prompt):
.\.venv\Scripts\activate.bat
# macOS / Linux:
source .venv/bin/activate

# Install required dependencies
pip install -r requirements.txt
```

---

### 3. Launching ClipNeon Studio

#### Option A: 1-Click Launch Scripts
- **Windows (PowerShell)**:
  ```powershell
  .\run.ps1
  ```
- **Windows (Batch File)**:
  Double click `run.bat` or run:
  ```cmd
  run.bat
  ```
- **macOS / Linux**:
  ```bash
  chmod +x run.sh
  ./run.sh
  ```

#### Option B: Direct Python Launch
```bash
python app.py
```

Open your browser at:
👉 **`http://127.0.0.1:8770`**

---

## 🧩 Architecture & Project Structure

```
clipneon-studio/
├── app.py              # FastAPI backend: 4K rendering engine, layout compositor & routes
├── app.js              # Vanilla JS frontend controller: interactive canvas & studio UI
├── index.html          # Studio interface with Obsidian Dark glassmorphism theme
├── style.css           # Premium responsive studio styles & animations
├── metadata_gen.py     # AI social media metadata generator (Titles, Hooks, Descriptions)
├── cleanup.py          # Auto-cleanup utility for temporary chunks & preview cache
├── captions.py         # Faster-Whisper transcription & ASS subtitle styling engine
├── captions_yt.py      # Rapid YouTube auto-caption extractor
├── smart.py            # Multi-modal highlight detector (Loudness, Speech, Motion, LLM)
├── highlights.py       # Audio jump & energy distribution analyzer
├── action.py           # OpenCV optical flow & camera movement analyzer
├── excite.py           # Speech excitement and exclamatory word scoring
├── standalone.py       # Hardware encoder detection & remote stream downloader
├── translit.py         # Devanagari to Romanized Hinglish conversion engine
├── previews.py         # Real-time frame extractor for interactive canvas editor
├── requirements.txt    # Production dependencies
├── run.ps1             # PowerShell 1-click launcher
├── run.bat             # Windows batch 1-click launcher
└── run.sh              # Unix shell 1-click launcher
```

---

## 📡 API Endpoints Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/make` | Starts automated multi-clip viral detection and 4K batch rendering |
| `GET` | `/make/{job}` | Returns live progress, status, and rendered clips for a batch job |
| `POST` | `/export` | Exports a single customized clip with manual crop coordinates |
| `POST` | `/upload` | Uploads local MP4/MKV video files with metadata extraction |
| `GET` | `/library` | Retrieves all rendered clips, social metadata, and timestamps |
| `POST` | `/cleanup` | Manually triggers disk cleanup to free temporary space |
| `POST` | `/cancel/{job}`| Cancels an active rendering or transcription task |

---

## 🔒 Whitelabel & Commercial Distribution

This codebase is 100% whitelabeled and clean. No third-party personal credits, author handles, or proprietary lock-ins exist. You are free to rebrand, customize, deploy, or package as a commercial SaaS product.

---

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for more information.
