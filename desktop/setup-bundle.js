#!/usr/bin/env node

import https from 'https';
import http from 'http';
import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';
import os from 'os';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const PYTHON_DIR = path.join(__dirname, 'src-tauri', 'python');
const BIN_DIR = path.join(__dirname, 'src-tauri', 'ffmpeg-bin');

// Python embeddable versions
const PYTHON_URLS = {
  'win32-x64': 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip',
  'darwin-x64': 'https://github.com/indygreg/python-build-standalone/releases/download/20240224/cpython-3.11.8+20240224-x86_64-apple-darwin-install_only.tar.gz',
  'darwin-arm64': 'https://github.com/indygreg/python-build-standalone/releases/download/20240224/cpython-3.11.8+20240224-aarch64-apple-darwin-install_only.tar.gz',
  'linux-x64': 'https://github.com/indygreg/python-build-standalone/releases/download/20240224/cpython-3.11.8+20240224-x86_64-unknown-linux-gnu-install_only.tar.gz'
};
const PROJECT_ROOT = path.join(__dirname, '..');

// FFmpeg download URLs
const FFMPEG_URLS = {
  'darwin-arm64': 'https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip',
  'darwin-x64': 'https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip',
  'win32-x64': 'https://github.com/GyanD/codexffmpeg/releases/download/7.0.2/ffmpeg-7.0.2-full_build.zip',
};

const FFPROBE_URLS = {
  'darwin-arm64': 'https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip',
  'darwin-x64': 'https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip',
};

function downloadFile(url, dest, redirectCount = 0) {
  if (redirectCount > 5) {
    return Promise.reject(new Error('Too many redirects'));
  }

  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const protocol = url.startsWith('https') ? https : http;
    
    console.log(`Downloading ${url}...`);
    
    const request = protocol.get(url, (response) => {
      if (response.statusCode === 302 || response.statusCode === 301) {
        file.close();
        fs.unlinkSync(dest);
        
        let location = response.headers.location;
        if (!location) {
          return reject(new Error('Redirect with no location header'));
        }
        
        if (!location.startsWith('http://') && !location.startsWith('https://')) {
          const urlObj = new URL(url);
          location = `${urlObj.protocol}//${urlObj.host}${location}`;
        }
        
        return downloadFile(location, dest, redirectCount + 1).then(resolve).catch(reject);
      }

      if (response.statusCode !== 200) {
        file.close();
        fs.unlinkSync(dest);
        return reject(new Error(`Failed to download: ${response.statusCode}`));
      }

      response.pipe(file);

      file.on('finish', () => {
        file.close(() => {
          console.log(`Downloaded to ${dest}`);
          resolve();
        });
      });
    });

    request.on('error', (err) => {
      file.close();
      fs.unlinkSync(dest);
      reject(err);
    });

    file.on('error', (err) => {
      fs.unlinkSync(dest);
      reject(err);
    });
  });
}

function setupSystemFFmpegWrappers() {
  console.log('\n=== Configuring FFmpeg wrappers (system ffmpeg) ===');

  if (fs.existsSync(BIN_DIR)) {
    fs.rmSync(BIN_DIR, { recursive: true, force: true });
  }
  fs.mkdirSync(BIN_DIR, { recursive: true });

  const ffmpegWrapper = '#!/bin/sh\nexec ffmpeg "$@"\n';
  const ffprobeWrapper = '#!/bin/sh\nexec ffprobe "$@"\n';

  const ffmpegPath = path.join(BIN_DIR, 'ffmpeg');
  const ffprobePath = path.join(BIN_DIR, 'ffprobe');

  fs.writeFileSync(ffmpegPath, ffmpegWrapper, { mode: 0o755 });
  fs.writeFileSync(ffprobePath, ffprobeWrapper, { mode: 0o755 });

  console.log('✓ Created FFmpeg wrappers that use system binaries');
}

async function downloadFFmpeg(platform, arch, key) {
  console.log('\n=== Downloading FFmpeg ===');
  
  // Create ffmpeg-bin directory
  if (fs.existsSync(BIN_DIR)) {
    fs.rmSync(BIN_DIR, { recursive: true, force: true });
  }
  fs.mkdirSync(BIN_DIR, { recursive: true });

  const tempDir = path.join(__dirname, '.ffmpeg-temp');
  if (fs.existsSync(tempDir)) {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
  fs.mkdirSync(tempDir, { recursive: true });

  if (platform === 'darwin') {
    // macOS: Download ffmpeg and ffprobe separately
    const ffmpegZip = path.join(tempDir, 'ffmpeg.zip');
    const ffprobeZip = path.join(tempDir, 'ffprobe.zip');
    
    await downloadFile(FFMPEG_URLS[key], ffmpegZip);
    await downloadFile(FFPROBE_URLS[key], ffprobeZip);
    
    execSync(`unzip -o "${ffmpegZip}" -d "${tempDir}"`, { stdio: 'inherit' });
    execSync(`unzip -o "${ffprobeZip}" -d "${tempDir}"`, { stdio: 'inherit' });
    
    fs.renameSync(path.join(tempDir, 'ffmpeg'), path.join(BIN_DIR, 'ffmpeg'));
    fs.renameSync(path.join(tempDir, 'ffprobe'), path.join(BIN_DIR, 'ffprobe'));
    
    fs.chmodSync(path.join(BIN_DIR, 'ffmpeg'), 0o755);
    fs.chmodSync(path.join(BIN_DIR, 'ffprobe'), 0o755);
  } else if (platform === 'win32') {
    // Windows: Download full build
    const ffmpegZip = path.join(tempDir, 'ffmpeg.zip');
    await downloadFile(FFMPEG_URLS[key], ffmpegZip);
    
    execSync(`powershell -command "Expand-Archive -Path '${ffmpegZip}' -DestinationPath '${tempDir}' -Force"`, {
      stdio: 'inherit'
    });
    
    // Find bin directory in extracted folder
    const extracted = fs.readdirSync(tempDir).find(f => f.startsWith('ffmpeg-'));
    const binPath = path.join(tempDir, extracted, 'bin');
    
    fs.copyFileSync(path.join(binPath, 'ffmpeg.exe'), path.join(BIN_DIR, 'ffmpeg.exe'));
    fs.copyFileSync(path.join(binPath, 'ffprobe.exe'), path.join(BIN_DIR, 'ffprobe.exe'));
  }

  fs.rmSync(tempDir, { recursive: true, force: true });
  console.log('✓ FFmpeg downloaded');
}

async function downloadPython(platform, arch, key) {
  console.log('\n=== Downloading Python ===');
  
  if (!PYTHON_URLS[key]) {
    throw new Error(`No Python download configured for ${key}`);
  }

  // Create python directory
  if (fs.existsSync(PYTHON_DIR)) {
    fs.rmSync(PYTHON_DIR, { recursive: true, force: true });
  }
  fs.mkdirSync(PYTHON_DIR, { recursive: true });

  const url = PYTHON_URLS[key];
  const filename = path.basename(new URL(url).pathname);
  const downloadPath = path.join(PYTHON_DIR, filename);

  await downloadFile(url, downloadPath);

  console.log('Extracting Python...');
  if (filename.endsWith('.zip')) {
    if (platform === 'win32') {
      execSync(`powershell -command "Expand-Archive -Path '${downloadPath}' -DestinationPath '${PYTHON_DIR}' -Force"`, {
        stdio: 'inherit'
      });
    } else {
      execSync(`unzip -o "${downloadPath}" -d "${PYTHON_DIR}"`, { stdio: 'inherit' });
    }
  } else if (filename.endsWith('.tar.gz')) {
    execSync(`tar -xzf "${downloadPath}" -C "${PYTHON_DIR}"`, { stdio: 'inherit' });
  }

  fs.unlinkSync(downloadPath);

  // Windows: Enable pip
  if (platform === 'win32') {
    const pthFile = path.join(PYTHON_DIR, 'python311._pth');
    if (fs.existsSync(pthFile)) {
      let content = fs.readFileSync(pthFile, 'utf8');
      content = content.replace('#import site', 'import site');
      fs.writeFileSync(pthFile, content);
    }

    const getPipPath = path.join(PYTHON_DIR, 'get-pip.py');
    await downloadFile('https://bootstrap.pypa.io/get-pip.py', getPipPath);
    
    console.log('Installing pip...');
    execSync(`"${path.join(PYTHON_DIR, 'python.exe')}" "${getPipPath}"`, { stdio: 'inherit' });
    fs.unlinkSync(getPipPath);
  }

  // Find python executable
  let pythonExe;
  if (platform === 'win32') {
    pythonExe = path.join(PYTHON_DIR, 'python.exe');
  } else {
    pythonExe = path.join(PYTHON_DIR, 'python', 'bin', 'python3');
  }

  if (!fs.existsSync(pythonExe)) {
    console.error('Python executable not found!');
    process.exit(1);
  }

  // Install ghoststream
  console.log('Installing GhostStream...');
  execSync(`"${pythonExe}" -m pip install "${PROJECT_ROOT}"`, { stdio: 'inherit' });

  console.log('✓ Python setup complete');
  return pythonExe;
}

async function main() {
  const platform = os.platform();
  const arch = os.arch();
  const key = `${platform}-${arch}`;

  console.log(`Platform: ${key}`);

  // Download FFmpeg (Windows/macOS only)
  if (FFMPEG_URLS[key]) {
    await downloadFFmpeg(platform, arch, key);
  } else {
    console.log('FFmpeg: Using system package');
    setupSystemFFmpegWrappers();
  }

  // Download Python
  await downloadPython(platform, arch, key);

  console.log('\n✓ All downloads complete!');
}

main().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
