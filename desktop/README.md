# Desktop build

The Tauri 2 shell runs the existing airport service on a loopback-only port,
opens it in a native window, and stores the SQLite database in the operating
system application data directory. The dispatcher and engineer workflows,
authentication, live updates, airport maps, and simulation remain shared with
the web build.

## Development

Install Rust, Node.js, Python 3, and the system WebView dependencies required by
Tauri 2. From the project root:

```sh
python -m pip install -r requirements.txt
npm install
npm run desktop:dev
```

## Production packaging

Create the embedded service executable before building the installer:

```sh
python -m pip install -r requirements.txt pyinstaller
npm run desktop:build
```

The build command packages the service first. PyInstaller embeds Python and the
service dependencies in the app's local sidecar, so the target computer does
not need a Python installation. Build the installer on each target operating
system.

Demo accounts remain enabled for this prototype. The SQLite database lives in
the OS application data directory and survives app restarts.
