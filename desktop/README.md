# GhostStream Desktop

Cross-platform native GUI for GhostStream transcoding server.

## Features

- **Start/Stop** GhostStream server with one click
- **Real-time job monitoring** via WebSocket
- **System tray** integration (minimize to tray)
- **Cross-platform**: Windows, macOS, Linux
- **Lightweight**: ~10MB bundle (Tauri + Svelte)

## Prerequisites

1. **Node.js** 18+ - https://nodejs.org/
2. **Rust** - https://rustup.rs/
3. **GhostStream** installed and accessible via `python -m ghoststream`

### Platform-specific

**Windows:**
- Microsoft Visual Studio C++ Build Tools

**macOS:**
- Xcode Command Line Tools: `xcode-select --install`

**Linux:**
- `sudo apt install libwebkit2gtk-4.0-dev build-essential curl wget libssl-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev`

## Setup

```bash
cd desktop

# Install dependencies
npm install

# Run in development mode
npm run tauri:dev

# Build for production
npm run tauri:build
```

## Build Output

After `npm run tauri:build`, find installers in:

- **Windows**: `src-tauri/target/release/bundle/msi/` and `nsis/`
- **macOS**: `src-tauri/target/release/bundle/dmg/`
- **Linux**: `src-tauri/target/release/bundle/deb/` and `appimage/`

## How It Works

1. Click **Start Server** to spawn `python -m ghoststream`
2. GUI shows **"Searching for clients..."** while waiting
3. When a client (e.g., GhostHub) connects and starts a job, GUI shows **"Client Connected"**
4. Active transcoding jobs display with real-time progress
5. Click **Stop** or close window to minimize to system tray
6. Right-click tray icon for quick actions

## Architecture

```
┌─────────────────────────────────────┐
│      Tauri (Rust)                   │
│  - Spawns/kills GhostStream process │
│  - System tray management           │
├─────────────────────────────────────┤
│      Svelte Frontend                │
│  - Polls /api/health                │
│  - WebSocket for job updates        │
│  - Displays jobs and status         │
└─────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│    GhostStream (subprocess)         │
│    python -m ghoststream            │
│    Port 8765                        │
└─────────────────────────────────────┘
```

## Notes

- The GUI **does not modify GhostStream** - it only starts/stops the process and reads from the public API
- Jobs are tracked by listening to the WebSocket at `ws://localhost:8765/ws/progress`
- Window close minimizes to tray; use tray menu **Quit** to fully exit
