#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

struct Backend(Mutex<Option<Child>>);

fn project_root(app: &tauri::App) -> Result<PathBuf, String> {
    #[cfg(debug_assertions)]
    {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        if root.join("run.py").is_file() {
            return Ok(root.canonicalize().unwrap_or(root));
        }
    }

    let resources = app.path().resource_dir().map_err(|e| e.to_string())?;
    for candidate in [resources.clone(), resources.join("airport-crm")] {
        if candidate.join("run.py").is_file() {
            return Ok(candidate);
        }
    }
    Err(format!(
        "Backend files are missing from {}",
        resources.display()
    ))
}

fn free_port(preferred: Option<u16>) -> Result<u16, String> {
    if let Some(port) = preferred {
        if TcpListener::bind(("127.0.0.1", port)).is_ok() {
            return Ok(port);
        }
    }
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .map_err(|e| format!("Could not reserve a local port: {e}"))
}

fn spawn_backend(root: &Path, db_path: &Path, port: u16) -> Result<Child, String> {
    let venv_python = if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };
    let python = if venv_python.is_file() {
        venv_python
    } else {
        PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
    };
    let airport_file = root.join("data").join("airport_sheremetyevo.json");
    let bundled_server = root.join("server").join(if cfg!(windows) {
        "airport-crm-server.exe"
    } else {
        "airport-crm-server"
    });
    let mut command = if bundled_server.is_file() {
        Command::new(bundled_server)
    } else {
        let mut fallback = Command::new(python);
        fallback.arg(root.join("run.py"));
        fallback
    };
    let child = command
        .args(["--host", "127.0.0.1", "--port", &port.to_string()])
        .current_dir(root)
        .env("AIRPORT_FILE", airport_file)
        .env("DB_FILE", db_path)
        .env("DEMO_MODE", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Could not start the CRM service: {e}"))?;
    Ok(child)
}

fn wait_for_backend(port: u16) -> Result<(), String> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err("The CRM service did not start in 15 seconds. Install the Python requirements and restart the app.".into())
}

fn main() {
    #[cfg(target_os = "linux")]
    if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        // Prefer XWayland for this WebKitGTK app on Wayland desktops. Some
        // WebKitGTK/Wayland combinations fail during page load with GDK
        // protocol errors. Keep an app-specific override for troubleshooting.
        let backend = std::env::var("AIRPORT_CRM_GDK_BACKEND").unwrap_or_else(|_| "x11".into());
        std::env::set_var("GDK_BACKEND", backend);
    }
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DMABUF_RENDERER_FORCE_SHM").is_none() {
        // Avoid broken GBM imports on some WebKitGTK/GPU combinations while
        // keeping WebKit's shared-memory renderer transport enabled.
        std::env::set_var("WEBKIT_DMABUF_RENDERER_FORCE_SHM", "1");
    }

    tauri::Builder::default()
        .setup(|app| {
            let root = project_root(app).map_err(std::io::Error::other)?;
            let data_dir = app.path().app_data_dir().map_err(std::io::Error::other)?;
            std::fs::create_dir_all(&data_dir)?;
            let db_path = data_dir.join("dispatch.db");
            let port_file = data_dir.join("service-port");
            let previous_port = std::fs::read_to_string(&port_file)
                .ok()
                .and_then(|value| value.trim().parse::<u16>().ok());
            let port = free_port(previous_port).map_err(std::io::Error::other)?;
            std::fs::write(port_file, port.to_string())?;
            let child = spawn_backend(&root, &db_path, port).map_err(std::io::Error::other)?;
            if let Err(error) = wait_for_backend(port) {
                let mut child = child;
                let _ = child.kill();
                return Err(std::io::Error::other(error).into());
            }

            app.manage(Backend(Mutex::new(Some(child))));
            let url = format!("http://127.0.0.1:{port}/");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse().unwrap()))
                .title("ОТО · Операционный центр")
                .inner_size(1480.0, 940.0)
                .min_inner_size(1040.0, 680.0)
                .center()
                .build()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(backend) = window.app_handle().try_state::<Backend>() {
                    if let Ok(mut child) = backend.0.lock() {
                        if let Some(mut child) = child.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run Airport CRM desktop application");
}
