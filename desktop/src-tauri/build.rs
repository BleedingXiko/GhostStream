use std::path::PathBuf;

fn main() {
    let ffmpeg_bin_dir = PathBuf::from("ffmpeg-bin");
    
    if !ffmpeg_bin_dir.exists() {
        eprintln!("Warning: ffmpeg-bin directory not found!");
        eprintln!("Run 'node ../download-ffmpeg.js' to download FFmpeg binaries");
    }
    
    tauri_build::build()
}
