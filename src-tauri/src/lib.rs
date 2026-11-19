//! Forge DSP desktop app (Tauri): the web GUI in the system webview, plus what a browser page
//! cannot do on every platform:
//!  - serial ports (WebKit on macOS/Linux has no Web Serial): list, open, write, read as a stream
//!  - the native "save as" dialog and writing the chosen file (save_file, the page names no path)
//!  - HTTP GET to GitHub for the firmware releases (no CORS in the app), GitHub hosts only
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::ipc::Channel;
use tauri::State;

#[derive(Serialize)]
struct PortInfo {
    name: String,
    vid: Option<u16>,
    pid: Option<u16>,
    product: Option<String>,
}

/// The thread that reads the open port (it holds a clone of the port handle).
struct Reader {
    run: Arc<AtomicBool>,
    thread: std::thread::JoinHandle<()>,
}

#[derive(Default)]
struct Serial {
    port: Mutex<Option<Box<dyn serialport::SerialPort>>>,
    reader: Mutex<Option<Reader>>,
}

#[tauri::command]
fn serial_ports() -> Result<Vec<PortInfo>, String> {
    let ports = serialport::available_ports().map_err(|e| e.to_string())?;
    Ok(ports
        .into_iter()
        .map(|p| match p.port_type {
            serialport::SerialPortType::UsbPort(u) => PortInfo { name: p.port_name, vid: Some(u.vid), pid: Some(u.pid), product: u.product },
            _ => PortInfo { name: p.port_name, vid: None, pid: None, product: None },
        })
        .collect())
}

/// Closes the port. Waits for the reading thread (at most one read timeout, 50 ms): its clone of
/// the handle must be closed before the same port is opened again, the port is opened exclusively
/// (TIOCEXCL on Unix, Windows likewise) and a second open in that time failed with "busy".
fn stop(state: &Serial) {
    let reader = state.reader.lock().unwrap().take();
    if let Some(r) = reader {
        r.run.store(false, Ordering::SeqCst);
        let _ = r.thread.join();
    }
    state.port.lock().unwrap().take();
}

/// Opens a port (8N1) and streams everything it receives to `on_data`. When the port goes away on
/// its own (unplugged, Forge switched off) `on_close` gets the reason; not after serial_close.
#[tauri::command(async)]
fn serial_open(
    state: State<'_, Serial>,
    path: String,
    baud: u32,
    on_data: Channel<Vec<u8>>,
    on_close: Channel<String>,
) -> Result<(), String> {
    stop(&state);
    let port = serialport::new(&path, baud)
        .data_bits(serialport::DataBits::Eight)
        .parity(serialport::Parity::None)
        .stop_bits(serialport::StopBits::One)
        .timeout(Duration::from_millis(50))
        .open()
        .map_err(|e| format!("{path}: {e}"))?;
    let mut reader = port.try_clone().map_err(|e| e.to_string())?;
    let run = Arc::new(AtomicBool::new(true));
    let alive = run.clone();
    let thread = std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
        let why = loop {
            if !alive.load(Ordering::SeqCst) {
                break None; // serial_close / serial_open: no report
            }
            match reader.read(&mut buf) {
                Ok(n) if n > 0 => {
                    if on_data.send(buf[..n].to_vec()).is_err() {
                        break None; // the page is gone
                    }
                }
                Ok(_) => {}
                Err(e) if e.kind() == std::io::ErrorKind::TimedOut => {}
                Err(e) => break Some(e.to_string()), // unplugged
            }
        };
        if let Some(why) = why {
            if alive.load(Ordering::SeqCst) {
                let _ = on_close.send(why);
            }
        }
    });
    *state.port.lock().unwrap() = Some(port);
    *state.reader.lock().unwrap() = Some(Reader { run, thread });
    Ok(())
}

/// Another rate on the open port, without closing it (a reopen raced the reading thread's handle).
/// Bytes still waiting at the old rate are dropped, as with a reopen.
#[tauri::command(async)]
fn serial_set_baud(state: State<'_, Serial>, baud: u32) -> Result<(), String> {
    let mut guard = state.port.lock().unwrap();
    let port = guard.as_mut().ok_or("not connected")?;
    port.set_baud_rate(baud).map_err(|e| e.to_string())?;
    port.clear(serialport::ClearBuffer::Input).map_err(|e| e.to_string())
}

#[tauri::command(async)]
fn serial_write(state: State<'_, Serial>, data: Vec<u8>) -> Result<(), String> {
    let mut guard = state.port.lock().unwrap();
    let port = guard.as_mut().ok_or("not connected")?;
    port.write_all(&data).map_err(|e| e.to_string())?;
    port.flush().map_err(|e| e.to_string())
}

#[tauri::command(async)]
fn serial_close(state: State<'_, Serial>) -> Result<(), String> {
    stop(&state);
    Ok(())
}

/// Shows the native "save as" dialog and writes the data to the file the user picked; false if
/// cancelled. The page never names a path: a command that wrote to any path it was given let
/// any script in the webview write any file of the user (Review 19.11.2026).
#[tauri::command(async)]
fn save_file(app: tauri::AppHandle, name: String, title: String, data: Vec<u8>) -> Result<bool, String> {
    use tauri_plugin_dialog::DialogExt;
    let Some(picked) = app.dialog().file().set_file_name(&name).set_title(&title).blocking_save_file() else {
        return Ok(false);
    };
    let path = picked.into_path().map_err(|e| e.to_string())?;
    std::fs::write(&path, data).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(true)
}

const GITHUB: [&str; 2] = ["https://api.github.com/", "https://github.com/"];

/// GET from GitHub (release list and release files; redirects to GitHub's file hosts are followed).
#[tauri::command(async)]
fn http_get(url: String) -> Result<Vec<u8>, String> {
    if !GITHUB.iter().any(|p| url.starts_with(p)) {
        return Err(format!("not allowed: {url}"));
    }
    let mut r = ureq::get(&url)
        .header("User-Agent", "forge-dsp-desktop")
        .header("Accept", if url.contains("api.github.com") { "application/vnd.github+json" } else { "application/octet-stream" })
        .call()
        .map_err(|e| format!("{url}: {e}"))?;
    r.body_mut().with_config().limit(16 * 1024 * 1024).read_to_vec().map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Serial::default())
        .invoke_handler(tauri::generate_handler![serial_ports, serial_open, serial_set_baud, serial_write, serial_close, save_file, http_get])
        .run(tauri::generate_context!())
        .expect("error while running the app");
}
