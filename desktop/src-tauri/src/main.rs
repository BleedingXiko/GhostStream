#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use std::net::UdpSocket;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{
    CustomMenuItem, Manager, SystemTray, SystemTrayEvent, SystemTrayMenu, SystemTrayMenuItem,
};

struct GhostStreamState {
    process: Mutex<Option<Child>>,
}

#[tauri::command]
fn start_ghoststream(state: tauri::State<GhostStreamState>) -> Result<(), String> {
    let mut process_guard = state.process.lock().map_err(|e| e.to_string())?;

    if process_guard.is_some() {
        return Err("GhostStream is already running".to_string());
    }

    // Check if server is already running on port 8765
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_millis(500))
        .build()
        .map_err(|e| e.to_string())?;
    
    if client.get("http://localhost:8765/api/health").send().is_ok() {
        return Err("GhostStream is already running on port 8765".to_string());
    }

    // Determine the command based on OS
    #[cfg(target_os = "windows")]
    let child = Command::new("python")
        .args(["-m", "ghoststream"])
        .spawn()
        .map_err(|e| format!("Failed to start GhostStream: {}", e))?;

    #[cfg(not(target_os = "windows"))]
    let child = Command::new("python3")
        .args(["-m", "ghoststream"])
        .spawn()
        .or_else(|_| {
            Command::new("python")
                .args(["-m", "ghoststream"])
                .spawn()
        })
        .map_err(|e| format!("Failed to start GhostStream: {}", e))?;

    println!("GhostStream process started with PID: {}", child.id());

    *process_guard = Some(child);
    Ok(())
}

#[tauri::command]
fn stop_ghoststream(state: tauri::State<GhostStreamState>) -> Result<(), String> {
    let mut process_guard = state.process.lock().map_err(|e| e.to_string())?;

    if let Some(mut child) = process_guard.take() {
        // Try graceful shutdown first on Unix
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            unsafe {
                libc::kill(child.id() as i32, libc::SIGTERM);
            }
            // Give it a moment to shut down gracefully
            std::thread::sleep(std::time::Duration::from_millis(500));
        }

        // Force kill if still running
        let _ = child.kill();
        let _ = child.wait();
    }

    Ok(())
}

#[tauri::command]
fn is_ghoststream_running(state: tauri::State<GhostStreamState>) -> bool {
    let process_guard = state.process.lock().unwrap();
    process_guard.is_some()
}

#[tauri::command]
fn get_local_ip() -> Result<String, String> {
    // Connect to a remote address to determine local IP
    // This doesn't actually send any data, just determines the route
    let socket = UdpSocket::bind("0.0.0.0:0").map_err(|e| e.to_string())?;
    socket.connect("8.8.8.8:80").map_err(|e| e.to_string())?;
    let local_addr = socket.local_addr().map_err(|e| e.to_string())?;
    Ok(local_addr.ip().to_string())
}

#[tauri::command]
fn is_ghosthub_network() -> bool {
    // Check if we're on the GhostHub AP network (192.168.4.x)
    if let Ok(ip) = get_local_ip() {
        return ip.starts_with("192.168.4.");
    }
    false
}

#[tauri::command]
fn check_server_health() -> Result<String, String> {
    // Check if GhostStream server is responding
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .map_err(|e| e.to_string())?;
    
    let res = client
        .get("http://localhost:8765/api/health")
        .send()
        .map_err(|e| format!("Server not responding: {}", e))?;
    
    if res.status().is_success() {
        let body = res.text().map_err(|e| e.to_string())?;
        Ok(body)
    } else {
        Err(format!("Server returned status: {}", res.status()))
    }
}

#[tauri::command]
fn get_capabilities() -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .map_err(|e| e.to_string())?;
    
    let res = client
        .get("http://localhost:8765/api/capabilities")
        .send()
        .map_err(|e| format!("Failed to get capabilities: {}", e))?;
    
    if res.status().is_success() {
        let body = res.text().map_err(|e| e.to_string())?;
        Ok(body)
    } else {
        Err(format!("Server returned status: {}", res.status()))
    }
}

#[tauri::command]
fn wait_for_server_ready() -> Result<String, String> {
    // Check every 200ms for faster detection, up to 20 seconds
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_millis(200))
        .build()
        .map_err(|e| e.to_string())?;
    
    // Check immediately first
    if let Ok(res) = client.get("http://localhost:8765/api/health").send() {
        if res.status().is_success() {
            let body = res.text().unwrap_or_default();
            println!("Server ready immediately");
            return Ok(body);
        }
    }
    
    // Then poll every 200ms
    for i in 0..100 {
        std::thread::sleep(std::time::Duration::from_millis(200));
        
        match client.get("http://localhost:8765/api/health").send() {
            Ok(res) if res.status().is_success() => {
                let body = res.text().unwrap_or_default();
                let secs = (i + 1) as f32 * 0.2;
                println!("Server ready after {:.1} seconds", secs);
                return Ok(body);
            }
            _ => {
                // Only log every second
                if (i + 1) % 5 == 0 {
                    println!("Waiting for server... {:.1}s", (i + 1) as f32 * 0.2);
                }
            }
        }
    }
    
    Err("Server failed to start within 20 seconds".to_string())
}

fn create_tray_menu() -> SystemTrayMenu {
    let show = CustomMenuItem::new("show".to_string(), "Show Window");
    let start = CustomMenuItem::new("start".to_string(), "Start Server");
    let stop = CustomMenuItem::new("stop".to_string(), "Stop Server");
    let quit = CustomMenuItem::new("quit".to_string(), "Quit");

    SystemTrayMenu::new()
        .add_item(show)
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(start)
        .add_item(stop)
        .add_native_item(SystemTrayMenuItem::Separator)
        .add_item(quit)
}

fn main() {
    let tray = SystemTray::new().with_menu(create_tray_menu());

    tauri::Builder::default()
        .manage(GhostStreamState {
            process: Mutex::new(None),
        })
        .system_tray(tray)
        .on_system_tray_event(|app, event| match event {
            SystemTrayEvent::LeftClick { .. } => {
                if let Some(window) = app.get_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            SystemTrayEvent::MenuItemClick { id, .. } => {
                let state = app.state::<GhostStreamState>();
                match id.as_str() {
                    "show" => {
                        if let Some(window) = app.get_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "start" => {
                        let _ = start_ghoststream(state);
                    }
                    "stop" => {
                        let _ = stop_ghoststream(state);
                    }
                    "quit" => {
                        // Stop GhostStream before quitting
                        let _ = stop_ghoststream(state);
                        std::process::exit(0);
                    }
                    _ => {}
                }
            }
            _ => {}
        })
        .on_window_event(|event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event.event() {
                // Hide window instead of closing (minimize to tray)
                event.window().hide().unwrap();
                api.prevent_close();
            }
        })
        .invoke_handler(tauri::generate_handler![
            start_ghoststream,
            stop_ghoststream,
            is_ghoststream_running,
            get_local_ip,
            is_ghosthub_network,
            check_server_health,
            get_capabilities,
            wait_for_server_ready
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
