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

const BIN_DIR = path.join(__dirname, 'src-tauri', 'ffmpeg-bin');

const DOWNLOAD_URLS = {
  'win32-x64': {
    url: 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip',
    type: 'zip',
    binPath: 'ffmpeg-master-latest-win64-gpl/bin'
  },
  'darwin-x64': {
    url: 'https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip',
    url2: 'https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip',
    type: 'zip'
  },
  'darwin-arm64': {
    url: 'https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip',
    url2: 'https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip',
    type: 'zip'
  },
  'linux-x64': {
    url: 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz',
    type: 'tar.xz',
    binPath: 'ffmpeg-master-latest-linux64-gpl/bin'
  }
};

function getPlatformKey() {
  const platform = os.platform();
  const arch = os.arch();
  
  if (platform === 'win32') return 'win32-x64';
  if (platform === 'darwin') {
    return arch === 'arm64' ? 'darwin-arm64' : 'darwin-x64';
  }
  if (platform === 'linux') return 'linux-x64';
  
  throw new Error(`Unsupported platform: ${platform}-${arch}`);
}

function downloadFile(url, dest, redirectCount = 0) {
  const MAX_REDIRECTS = 5;
  
  return new Promise((resolve, reject) => {
    // Prevent infinite redirect loops
    if (redirectCount > MAX_REDIRECTS) {
      return reject(new Error(`Too many redirects (${redirectCount})`));
    }
    
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
        
        // Handle relative redirects
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
      
      const totalBytes = parseInt(response.headers['content-length'], 10);
      let downloadedBytes = 0;
      
      response.pipe(file);
      
      response.on('data', (chunk) => {
        downloadedBytes += chunk.length;
        if (totalBytes) {
          const percent = ((downloadedBytes / totalBytes) * 100).toFixed(1);
          process.stdout.write(`\rProgress: ${percent}%`);
        }
      });
      
      file.on('finish', () => {
        console.log('\nDownload complete');
        file.close(resolve);
      });
    });
    
    request.on('error', (err) => {
      file.close();
      fs.unlinkSync(dest);
      reject(err);
    });
  });
}

function extractZip(zipPath, destDir) {
  console.log(`Extracting ${zipPath}...`);
  const platform = os.platform();
  
  if (platform === 'win32') {
    execSync(`powershell -command "Expand-Archive -Path '${zipPath}' -DestinationPath '${destDir}' -Force"`, {
      stdio: 'inherit'
    });
  } else {
    execSync(`unzip -o "${zipPath}" -d "${destDir}"`, { stdio: 'inherit' });
  }
}

function extractTarXz(tarPath, destDir) {
  console.log(`Extracting ${tarPath}...`);
  execSync(`tar -xf "${tarPath}" -C "${destDir}"`, { stdio: 'inherit' });
}

async function main() {
  try {
    const platformKey = getPlatformKey();
    const config = DOWNLOAD_URLS[platformKey];
    
    if (!config) {
      throw new Error(`No download configuration for platform: ${platformKey}`);
    }
    
    console.log(`Platform: ${platformKey}`);
    
    // Clean and create bin directory
    if (fs.existsSync(BIN_DIR)) {
      fs.rmSync(BIN_DIR, { recursive: true, force: true });
    }
    fs.mkdirSync(BIN_DIR, { recursive: true });
    
    const tempDir = path.join(__dirname, '.ffmpeg-temp');
    if (fs.existsSync(tempDir)) {
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
    fs.mkdirSync(tempDir, { recursive: true });
    
    // Download and extract
    if (config.url2) {
      // macOS: separate ffmpeg and ffprobe downloads
      const ffmpegZip = path.join(tempDir, 'ffmpeg.zip');
      const ffprobeZip = path.join(tempDir, 'ffprobe.zip');
      
      await downloadFile(config.url, ffmpegZip);
      await downloadFile(config.url2, ffprobeZip);
      
      extractZip(ffmpegZip, tempDir);
      extractZip(ffprobeZip, tempDir);
      
      // Move binaries
      fs.renameSync(path.join(tempDir, 'ffmpeg'), path.join(BIN_DIR, 'ffmpeg'));
      fs.renameSync(path.join(tempDir, 'ffprobe'), path.join(BIN_DIR, 'ffprobe'));
      
      // Make executable
      fs.chmodSync(path.join(BIN_DIR, 'ffmpeg'), 0o755);
      fs.chmodSync(path.join(BIN_DIR, 'ffprobe'), 0o755);
    } else {
      // Windows/Linux: single archive with both binaries
      const ext = config.type;
      const archivePath = path.join(tempDir, `ffmpeg.${ext}`);
      
      await downloadFile(config.url, archivePath);
      
      if (config.type === 'zip') {
        extractZip(archivePath, tempDir);
      } else if (config.type === 'tar.xz') {
        extractTarXz(archivePath, tempDir);
      }
      
      // Find and move binaries
      const binPath = path.join(tempDir, config.binPath);
      const ffmpegSrc = path.join(binPath, os.platform() === 'win32' ? 'ffmpeg.exe' : 'ffmpeg');
      const ffprobeSrc = path.join(binPath, os.platform() === 'win32' ? 'ffprobe.exe' : 'ffprobe');
      
      const ffmpegDest = path.join(BIN_DIR, os.platform() === 'win32' ? 'ffmpeg.exe' : 'ffmpeg');
      const ffprobeDest = path.join(BIN_DIR, os.platform() === 'win32' ? 'ffprobe.exe' : 'ffprobe');
      
      fs.copyFileSync(ffmpegSrc, ffmpegDest);
      fs.copyFileSync(ffprobeSrc, ffprobeDest);
      
      if (os.platform() !== 'win32') {
        fs.chmodSync(ffmpegDest, 0o755);
        fs.chmodSync(ffprobeDest, 0o755);
      }
    }
    
    // Cleanup temp directory
    fs.rmSync(tempDir, { recursive: true, force: true });
    
    console.log('\n✓ FFmpeg binaries downloaded and extracted successfully');
    console.log(`  Location: ${BIN_DIR}`);
    
    // Verify binaries
    const ffmpegPath = path.join(BIN_DIR, os.platform() === 'win32' ? 'ffmpeg.exe' : 'ffmpeg');
    const ffprobePath = path.join(BIN_DIR, os.platform() === 'win32' ? 'ffprobe.exe' : 'ffprobe');
    
    if (fs.existsSync(ffmpegPath) && fs.existsSync(ffprobePath)) {
      console.log('✓ Verified: ffmpeg and ffprobe are present');
    } else {
      throw new Error('Failed to verify binaries');
    }
    
  } catch (error) {
    console.error('Error:', error.message);
    process.exit(1);
  }
}

main();
