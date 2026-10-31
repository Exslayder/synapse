#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod doh;
mod proxy;

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::net::{IpAddr, SocketAddr, ToSocketAddrs};
use std::time::Duration;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use tauri::{AppHandle, Manager, State, WindowEvent};

use doh::DohResolver;

// ---------------------------------------------------------------- config

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(default)]
struct DohConfig {
    url: String,
    bootstrap_ips: Vec<String>,
    fallback_tcp: bool,
    timeout: f64,
}

impl Default for DohConfig {
    fn default() -> Self {
        Self {
            url: String::new(),
            bootstrap_ips: Vec::new(),
            fallback_tcp: true,
            timeout: 8.0,
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(default)]
struct Site {
    id: String,
    name: String,
    url: String,
    pinned: bool,
    enabled: bool,
    direct: bool,
}

impl Default for Site {
    fn default() -> Self {
        Self {
            id: String::new(),
            name: String::new(),
            url: String::new(),
            pinned: false,
            enabled: true,
            direct: false,
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(default)]
struct AppConfig {
    version: u32,
    doh: DohConfig,
    proxy_port: u16,
    allowed_ports: Vec<u16>,
    browser: String,
    browser_path: String,
    browser_width: u32,
    browser_height: u32,
    close_browsers_on_exit: bool,
    window_geometry: String,
    window_topmost: bool,
    auto_check_updates: bool,
    last_update_check: String,
    sites: Vec<Site>,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            version: 3,
            doh: DohConfig::default(),
            proxy_port: 8877,
            allowed_ports: vec![80, 443],
            browser: "auto".into(),
            browser_path: String::new(),
            browser_width: 1280,
            browser_height: 860,
            close_browsers_on_exit: true,
            window_geometry: String::new(),
            window_topmost: false,
            auto_check_updates: true,
            last_update_check: String::new(),
            sites: default_sites(),
        }
    }
}

fn default_sites() -> Vec<Site> {
    [
        ("gemini", "Gemini", "https://gemini.google.com"),
        ("chatgpt", "ChatGPT", "https://chatgpt.com"),
        ("claude", "Claude", "https://claude.ai"),
        ("grok", "Grok", "https://grok.com"),
    ]
    .iter()
    .map(|(id, name, url)| Site {
        id: id.to_string(),
        name: name.to_string(),
        url: url.to_string(),
        pinned: false,
        enabled: true,
        direct: false,
    })
    .collect()
}

// ---------------------------------------------------------------- state

#[derive(Clone)]
struct Shared {
    base: PathBuf,
    config: Arc<Mutex<AppConfig>>,
    children: Arc<Mutex<HashMap<String, Child>>>,
    pids: Arc<Mutex<HashMap<String, Vec<u32>>>>,
    resolver: Arc<DohResolver>,
    proxy_port: Arc<Mutex<u16>>,
    direct_hosts: Arc<Mutex<Vec<String>>>,
    health: Arc<Mutex<HashMap<String, SiteHealth>>>,
    running_cache: Arc<Mutex<Vec<String>>>,
}

static STATE: OnceLock<Shared> = OnceLock::new();

fn direct_hosts_from_sites(sites: &[Site]) -> Vec<String> {
    sites
        .iter()
        .filter(|s| s.enabled && s.direct)
        .filter_map(|s| {
            let host = s
                .url
                .trim_start_matches("https://")
                .trim_start_matches("http://")
                .split('/')
                .next()
                .unwrap_or("")
                .trim_start_matches("www.")
                .to_ascii_lowercase();
            if host.is_empty() {
                None
            } else {
                Some(host)
            }
        })
        .collect()
}

fn resolve_base(app: &AppHandle) -> PathBuf {
    if let Ok(v) = std::env::var("SYNAPSE_HOME") {
        if !v.trim().is_empty() {
            return PathBuf::from(v);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let probe = dir.join(".synapse-write-probe");
            if fs::write(&probe, b"x").is_ok() {
                let _ = fs::remove_file(&probe);
                return dir.to_path_buf();
            }
        }
    }
    app.path()
        .app_local_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
}

fn config_path(base: &Path) -> PathBuf {
    base.join("config.json")
}

fn load_config(base: &Path) -> AppConfig {
    match fs::read_to_string(config_path(base)) {
        Ok(text) => {
            let cleaned = text.trim_start_matches('\u{feff}');
            serde_json::from_str(cleaned).unwrap_or_default()
        }
        Err(_) => AppConfig::default(),
    }
}

fn write_config(base: &Path, cfg: &AppConfig) -> Result<(), String> {
    fs::create_dir_all(base).map_err(|e| e.to_string())?;
    let text = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    fs::write(config_path(base), text).map_err(|e| e.to_string())
}

// ---------------------------------------------------------------- logging

fn log_line(base: &Path, level: &str, message: &str) {
    static LOG_LOCK: Mutex<()> = Mutex::new(());
    let _guard = LOG_LOCK.lock();
    let dir = base.join("logs");
    let _ = fs::create_dir_all(&dir);
    let path = dir.join("synapse.log");
    let stamp = chrono::Local::now().format("%H:%M:%S");
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{} {} {}", stamp, level.to_uppercase(), message);
    }
}

// ---------------------------------------------------------------- browser

#[derive(Serialize, Clone)]
struct BrowserInfo {
    kind: String,
    name: String,
    path: String,
    found: bool,
}

fn browser_roots() -> Vec<PathBuf> {
    ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"]
        .iter()
        .filter_map(|key| std::env::var(key).ok())
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .collect()
}

fn chromium_candidates(kind: &str) -> Vec<PathBuf> {
    let rel = match kind {
        "chrome" => r"Google\Chrome\Application\chrome.exe",
        "edge" => r"Microsoft\Edge\Application\msedge.exe",
        _ => return Vec::new(),
    };
    browser_roots()
        .into_iter()
        .map(|root| root.join(rel))
        .collect()
}

fn firefox_candidates() -> Vec<PathBuf> {
    const REL: [&str; 6] = [
        r"Mozilla Firefox\firefox.exe",
        r"Firefox Nightly\firefox.exe",
        r"Nightly\firefox.exe",
        r"Firefox\firefox.exe",
        r"Programs\Mozilla Firefox\firefox.exe",
        r"Programs\Firefox Nightly\firefox.exe",
    ];
    let mut out = Vec::new();
    for root in browser_roots() {
        for rel in REL {
            out.push(root.join(rel));
        }
    }
    out
}

fn firefox_display_name(path: &Path) -> String {
    let ini = path.with_file_name("application.ini");
    let mut codename = String::new();
    if let Ok(text) = fs::read_to_string(&ini) {
        for line in text.lines() {
            if let Some((key, value)) = line.split_once('=') {
                if key.trim().eq_ignore_ascii_case("codename") {
                    codename = value.trim().to_string();
                }
            }
        }
    }
    let lower = codename.to_ascii_lowercase();
    if lower.contains("nightly") {
        "Firefox Nightly".to_string()
    } else if lower.contains("developer") {
        "Firefox Developer".to_string()
    } else {
        "Firefox".to_string()
    }
}

fn system_default_browser() -> Option<String> {
    static CACHE: OnceLock<String> = OnceLock::new();
    let value = CACHE.get_or_init(|| {
        let output = Command::new("reg")
            .args([
                "query",
                r"HKCU\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
                "/v",
                "ProgId",
            ])
            .output()
            .ok();
        let text = output
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
            .unwrap_or_default()
            .to_ascii_lowercase();
        if text.contains("firefox") {
            "firefox".to_string()
        } else if text.contains("chrome") {
            "chrome".to_string()
        } else if text.contains("edge") {
            "edge".to_string()
        } else {
            String::new()
        }
    });
    if value.is_empty() {
        None
    } else {
        Some(value.clone())
    }
}

fn detect_browser(cfg: &AppConfig) -> Option<BrowserInfo> {
    if !cfg.browser_path.trim().is_empty() {
        let path = PathBuf::from(cfg.browser_path.trim());
        if path.is_file() {
            let file = path
                .file_name()
                .map(|n| n.to_string_lossy().to_ascii_lowercase())
                .unwrap_or_default();
            let kind = if file.contains("firefox") {
                "firefox"
            } else if file.contains("msedge") {
                "edge"
            } else {
                "chrome"
            };
            let name = if kind == "firefox" {
                firefox_display_name(&path)
            } else if kind == "edge" {
                "Microsoft Edge".to_string()
            } else {
                "Google Chrome".to_string()
            };
            return Some(BrowserInfo {
                kind: kind.into(),
                name,
                path: path.to_string_lossy().into(),
                found: true,
            });
        }
    }

    let prefer = |kind: &str| -> Option<BrowserInfo> {
        if kind == "firefox" {
            return firefox_candidates()
                .into_iter()
                .find(|path| path.is_file())
                .map(|path| BrowserInfo {
                    kind: "firefox".into(),
                    name: firefox_display_name(&path),
                    path: path.to_string_lossy().into(),
                    found: true,
                });
        }
        chromium_candidates(kind)
            .into_iter()
            .find(|path| path.is_file())
            .map(|path| BrowserInfo {
                kind: kind.into(),
                name: if kind == "edge" {
                    "Microsoft Edge".into()
                } else {
                    "Google Chrome".into()
                },
                path: path.to_string_lossy().into(),
                found: true,
            })
    };

    let order: Vec<String> = match cfg.browser.as_str() {
        "chrome" => vec!["chrome".into()],
        "firefox" => vec!["firefox".into()],
        "edge" => vec!["chrome".into(), "firefox".into()],
        _ => {
            let mut list: Vec<String> = Vec::new();
            // Prefer whatever Windows uses as the default browser, but only
            // Chrome or Firefox: Edge is never selected automatically.
            if let Some(kind) = system_default_browser() {
                if kind == "chrome" || kind == "firefox" {
                    list.push(kind);
                }
            }
            for fallback in ["chrome", "firefox"] {
                if !list.iter().any(|k| k == fallback) {
                    list.push(fallback.to_string());
                }
            }
            list
        }
    };
    for kind in order {
        if let Some(info) = prefer(&kind) {
            return Some(info);
        }
    }
    None
}

fn browser_info_or_missing(cfg: &AppConfig) -> BrowserInfo {
    detect_browser(cfg).unwrap_or(BrowserInfo {
        kind: String::new(),
        name: "Не найден".into(),
        path: String::new(),
        found: false,
    })
}

fn safe_id(id: &str) -> String {
    id.chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '-' })
        .collect()
}

#[cfg(windows)]
fn process_table() -> Vec<(u32, u32)> {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    let mut out = Vec::new();
    unsafe {
        let Ok(snapshot) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
            return out;
        };
        let mut entry = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        if Process32FirstW(snapshot, &mut entry).is_ok() {
            loop {
                out.push((entry.th32ProcessID, entry.th32ParentProcessID));
                if Process32NextW(snapshot, &mut entry).is_err() {
                    break;
                }
            }
        }
        let _ = CloseHandle(snapshot);
    }
    out
}

#[cfg(windows)]
fn expand_tree(roots: &[u32]) -> Vec<u32> {
    let table = process_table();
    let mut set: Vec<u32> = roots.to_vec();
    loop {
        let mut added = false;
        for (pid, ppid) in &table {
            if set.contains(ppid) && !set.contains(pid) {
                set.push(*pid);
                added = true;
            }
        }
        if !added {
            break;
        }
    }
    set
}

#[cfg(windows)]
fn kill_pid(pid: u32) {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE};
    unsafe {
        if let Ok(handle) = OpenProcess(PROCESS_TERMINATE, false, pid) {
            let _ = TerminateProcess(handle, 1);
            let _ = CloseHandle(handle);
        }
    }
}

#[cfg(windows)]
fn pid_alive(pid: u32) -> bool {
    use windows::Win32::Foundation::{CloseHandle, STILL_ACTIVE};
    use windows::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
            Ok(handle) => {
                let mut code = 0u32;
                let ok = GetExitCodeProcess(handle, &mut code).is_ok();
                let _ = CloseHandle(handle);
                ok && code == STILL_ACTIVE.0 as u32
            }
            Err(_) => false,
        }
    }
}

#[cfg(windows)]
static PIN_TARGET: Mutex<u32> = Mutex::new(0);
#[cfg(windows)]
static PIN_FOUND: Mutex<Vec<isize>> = Mutex::new(Vec::new());

#[cfg(windows)]
fn window_handles(pid: u32) -> Vec<isize> {
    use windows::core::BOOL;
    use windows::Win32::Foundation::{HWND, LPARAM};
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindowTextLengthW, GetWindowThreadProcessId, IsWindowVisible,
    };

    unsafe extern "system" fn collect(hwnd: HWND, _: LPARAM) -> BOOL {
        unsafe {
            let mut owner = 0u32;
            GetWindowThreadProcessId(hwnd, Some(&mut owner));
            let target = *PIN_TARGET.lock().unwrap();
            if owner == target && IsWindowVisible(hwnd).as_bool() && GetWindowTextLengthW(hwnd) > 0 {
                PIN_FOUND.lock().unwrap().push(hwnd.0 as isize);
            }
            BOOL(1)
        }
    }

    *PIN_TARGET.lock().unwrap() = pid;
    PIN_FOUND.lock().unwrap().clear();
    unsafe {
        let _ = EnumWindows(Some(collect), LPARAM(0));
    }
    PIN_FOUND.lock().unwrap().clone()
}

#[cfg(windows)]
static TITLE_NEEDLE: Mutex<String> = Mutex::new(String::new());
#[cfg(windows)]
static TITLE_EXE: Mutex<String> = Mutex::new(String::new());
#[cfg(windows)]
static TITLE_FOUND: Mutex<Option<isize>> = Mutex::new(None);

#[cfg(windows)]
fn process_exe_name(pid: u32) -> Option<String> {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
        PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buffer = [0u16; 1024];
        let mut size = buffer.len() as u32;
        let ok = QueryFullProcessImageNameW(
            handle,
            PROCESS_NAME_WIN32,
            windows::core::PWSTR(buffer.as_mut_ptr()),
            &mut size,
        )
        .is_ok();
        let _ = CloseHandle(handle);
        if !ok {
            return None;
        }
        let path = String::from_utf16_lossy(&buffer[..size as usize]);
        Some(
            path.rsplit(['\\', '/'])
                .next()
                .unwrap_or("")
                .to_ascii_lowercase(),
        )
    }
}

#[cfg(windows)]
fn find_site_window(name: &str, exe: &str) -> Option<isize> {
    use windows::core::BOOL;
    use windows::Win32::Foundation::{HWND, LPARAM};
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId,
        IsWindowVisible,
    };

    unsafe extern "system" fn collect(hwnd: HWND, _: LPARAM) -> BOOL {
        unsafe {
            if TITLE_FOUND.lock().unwrap().is_some() {
                return BOOL(0);
            }
            if !IsWindowVisible(hwnd).as_bool() {
                return BOOL(1);
            }
            let len = GetWindowTextLengthW(hwnd);
            if len <= 0 {
                return BOOL(1);
            }
            let mut buf = vec![0u16; len as usize + 1];
            let read = GetWindowTextW(hwnd, &mut buf);
            let title = String::from_utf16_lossy(&buf[..read as usize]).to_ascii_lowercase();
            let needle = TITLE_NEEDLE.lock().unwrap().to_ascii_lowercase();
            if needle.is_empty() || !title.contains(&needle) {
                return BOOL(1);
            }
            let mut pid = 0u32;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            let exe_name = TITLE_EXE.lock().unwrap().to_ascii_lowercase();
            if process_exe_name(pid).as_deref() != Some(exe_name.as_str()) {
                return BOOL(1);
            }
            *TITLE_FOUND.lock().unwrap() = Some(hwnd.0 as isize);
            BOOL(0)
        }
    }

    *TITLE_NEEDLE.lock().unwrap() = name.to_string();
    *TITLE_EXE.lock().unwrap() = exe.to_string();
    *TITLE_FOUND.lock().unwrap() = None;
    unsafe {
        let _ = EnumWindows(Some(collect), LPARAM(0));
    }
    *TITLE_FOUND.lock().unwrap()
}

#[cfg(windows)]
fn focus_handle(raw: isize) {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{
        SetForegroundWindow, ShowWindow, SW_RESTORE, SW_SHOW,
    };
    unsafe {
        let hwnd = HWND(raw as *mut _);
        let _ = ShowWindow(hwnd, SW_RESTORE);
        let _ = ShowWindow(hwnd, SW_SHOW);
        let _ = SetForegroundWindow(hwnd);
    }
}

#[cfg(windows)]
fn focus_window(pid: u32) {
    if let Some(raw) = window_handles(pid).first() {
        focus_handle(*raw);
    }
}

#[cfg(windows)]
fn is_topmost(raw: isize) -> bool {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{GetWindowLongW, GWL_EXSTYLE, WS_EX_TOPMOST};
    unsafe {
        let style = GetWindowLongW(HWND(raw as *mut _), GWL_EXSTYLE);
        (style & WS_EX_TOPMOST.0 as i32) != 0
    }
}

#[cfg(windows)]
fn set_topmost(raw: isize, topmost: bool, force: bool) {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, HWND_NOTOPMOST, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
    };
    if !force && is_topmost(raw) == topmost {
        return;
    }
    unsafe {
        let _ = SetWindowPos(
            HWND(raw as *mut _),
            if topmost { Some(HWND_TOPMOST) } else { Some(HWND_NOTOPMOST) },
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        );
    }
}

#[cfg(windows)]
fn raise_topmost(raw: isize) {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, HWND_TOP, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
    };
    unsafe {
        let _ = SetWindowPos(
            HWND(raw as *mut _),
            Some(HWND_TOP),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        );
    }
}

#[cfg(windows)]
fn apply_pin(pid: u32, topmost: bool) {
    let handles = window_handles(pid);
    for raw in handles {
        set_topmost(raw, topmost, false);
    }
}

#[cfg(windows)]
fn apply_pin_force(pid: u32, topmost: bool) {
    let handles = window_handles(pid);
    for raw in handles {
        set_topmost(raw, topmost, true);
    }
}

fn write_firefox_prefs(profile: &Path, port: u16, width: u32, height: u32) {
    let prefs = [
        ("network.proxy.type", "1".to_string()),
        ("network.proxy.http", "\"127.0.0.1\"".into()),
        ("network.proxy.http_port", port.to_string()),
        ("network.proxy.ssl", "\"127.0.0.1\"".into()),
        ("network.proxy.ssl_port", port.to_string()),
        ("network.proxy.share_proxy_settings", "true".into()),
        ("network.proxy.no_proxies_on", "\"\"".into()),
        ("network.proxy.failover_direct", "false".into()),
        ("network.proxy.allow_hijacking_localhost", "false".into()),
        ("network.trr.mode", "5".into()),
        ("network.dns.disablePrefetch", "true".into()),
        ("network.dns.disablePrefetchFromHTTPS", "true".into()),
        ("network.prefetch-next", "false".into()),
        ("network.http.speculative-parallel-limit", "0".into()),
        ("network.captive-portal-service.enabled", "false".into()),
        ("network.connectivity-service.enabled", "false".into()),
        ("browser.aboutwelcome.enabled", "false".into()),
        ("termsofuse.acceptedVersion", "4".into()),
        ("termsofuse.acceptedDate", "\"0\"".into()),
        ("termsofuse.minimumVersion", "4".into()),
        ("termsofuse.bypassNotification", "true".into()),
        ("browser.shell.checkDefaultBrowser", "false".into()),
        ("browser.startup.homepage_override.mstone", "\"ignore\"".into()),
        ("browser.startup.page", "0".into()),
        ("app.update.auto", "false".into()),
        ("app.update.background.enabled", "false".into()),
        ("app.update.service.enabled", "false".into()),
        ("app.update.staging.enabled", "false".into()),
        ("extensions.update.enabled", "false".into()),
        ("browser.search.update", "false".into()),
        ("browser.sessionstore.resume_from_crash", "false".into()),
        ("browser.newtabpage.enabled", "false".into()),
        ("browser.tabs.warnOnClose", "false".into()),
        ("browser.warnOnQuit", "false".into()),
        ("datareporting.policy.dataSubmissionEnabled", "false".into()),
        ("datareporting.healthreport.uploadEnabled", "false".into()),
        ("toolkit.telemetry.enabled", "false".into()),
        ("toolkit.telemetry.unified", "false".into()),
    ];

    let mut body = String::from("// Generated by Synapse. The file is rewritten on every launch.\n");
    for (name, value) in prefs {
        body.push_str(&format!("user_pref(\"{name}\", {value});\n"));
    }
    let _ = fs::write(profile.join("user.js"), body);

    let xulstore = format!(
        "{{\"chrome://browser/content/browser.xhtml\":{{\"main-window\":{{\"width\":\"{width}\",\"height\":\"{height}\",\"screenX\":\"80\",\"screenY\":\"60\"}}}}}}"
    );
    let xul_path = profile.join("xulstore.json");
    if !xul_path.exists() {
        let _ = fs::write(xul_path, xulstore);
    }
}

fn pid_file(profile: &Path) -> PathBuf {
    profile.join("synapse.pid")
}

fn read_pid_file(profile: &Path) -> Option<u32> {
    fs::read_to_string(pid_file(profile))
        .ok()
        .and_then(|text| text.trim().parse::<u32>().ok())
}

fn launch_site(
    cfg: &AppConfig,
    base: &Path,
    site: &Site,
    proxy_port: u16,
) -> Result<Child, String> {
    if !site.enabled {
        return Err(format!("{} отключён в настройках", site.name));
    }
    let browser = detect_browser(cfg).ok_or_else(|| "Браузер не найден".to_string())?;
    let profile = base.join("profiles").join(safe_id(&site.id));
    fs::create_dir_all(&profile).map_err(|e| format!("Профиль: {e}"))?;

    let width = cfg.browser_width.max(640);
    let height = cfg.browser_height.max(480);

    let mut cmd = Command::new(&browser.path);
    if browser.kind == "firefox" {
        if proxy_port != 0 {
            write_firefox_prefs(&profile, proxy_port, width, height);
        }
        cmd.arg("-new-instance")
            .arg("-no-remote")
            .arg("-profile")
            .arg(&profile)
            .arg("-new-window")
            .arg(&site.url)
            .env("MOZ_NO_REMOTE", "1")
            .env("MOZ_CRASHREPORTER_DISABLE", "1");
    } else {
        cmd.arg(format!("--app={}", site.url))
            .arg(format!("--user-data-dir={}", profile.to_string_lossy()))
            .arg(format!("--window-size={width},{height}"))
            .arg("--no-first-run")
            .arg("--no-default-browser-check")
            .arg("--disable-quic")
            .arg("--disable-sync")
            .arg("--disable-background-mode")
            .arg("--no-service-autorun")
            .arg("--disable-background-networking")
            .arg("--disable-component-update")
            .arg("--disable-default-apps");
        if proxy_port != 0 {
            cmd.arg(format!("--proxy-server=http://127.0.0.1:{proxy_port}"));
        }
    }

    let child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Запуск браузера: {e}"))?;

    log_line(
        base,
        "info",
        &format!(
            "{}: открыт {} (профиль {}, pid {})",
            site.name,
            browser.name,
            safe_id(&site.id),
            child.id()
        ),
    );
    let _ = fs::write(pid_file(&profile), child.id().to_string());
    Ok(child)
}

// ---------------------------------------------------------------- geometry

fn parse_geometry(text: &str) -> Option<(u32, u32, i32, i32)> {
    let (size, rest) = text.split_once('+')?;
    let (w, h) = size.split_once('x')?;
    let (x, y) = rest.split_once('+')?;
    let w: u32 = w.trim().parse().ok()?;
    let h: u32 = h.trim().parse().ok()?;
    let x: i32 = x.trim().parse().ok()?;
    let y: i32 = y.trim().parse().ok()?;
    if w < 400 || h < 300 {
        return None;
    }
    Some((w, h, x, y))
}

#[cfg(windows)]
fn read_window_geometry(hwnd: windows::Win32::Foundation::HWND) -> Option<String> {
    use windows::Win32::Foundation::RECT;
    use windows::Win32::UI::WindowsAndMessaging::GetWindowRect;
    let mut rect = RECT::default();
    unsafe { GetWindowRect(hwnd, &mut rect).ok()? };
    Some(format!(
        "{}x{}+{}+{}",
        rect.right - rect.left,
        rect.bottom - rect.top,
        rect.left,
        rect.top
    ))
}

#[cfg(windows)]
fn apply_window_geometry(hwnd: windows::Win32::Foundation::HWND, w: u32, h: u32, x: i32, y: i32) {
    use windows::Win32::UI::WindowsAndMessaging::{SetWindowPos, SWP_NOACTIVATE, SWP_NOZORDER};
    unsafe {
        let _ = SetWindowPos(
            hwnd,
            None,
            x,
            y,
            w as i32,
            h as i32,
            SWP_NOZORDER | SWP_NOACTIVATE,
        );
    }
}

// ---------------------------------------------------------------- commands

#[derive(Serialize, Clone)]
struct SiteHealth {
    ok: bool,
    detail: String,
    checked: String,
}

#[derive(Serialize)]
struct ProxyState {
    port: u16,
    running: bool,
}

#[derive(Serialize)]
struct StateResponse {
    config: AppConfig,
    base: String,
    browser: BrowserInfo,
    running: Vec<String>,
    log_path: String,
    proxy: ProxyState,
    health: HashMap<String, SiteHealth>,
}

#[tauri::command]
fn get_state(state: State<Shared>) -> StateResponse {
    let cfg = state.config.lock().unwrap().clone();
    let mut running = Vec::new();
    for site in &cfg.sites {
        let profile = state.base.join("profiles").join(safe_id(&site.id));
        let mut roots = state
            .pids
            .lock()
            .unwrap()
            .get(&site.id)
            .cloned()
            .unwrap_or_default();
        if roots.is_empty() {
            if let Some(pid) = read_pid_file(&profile) {
                let tree = expand_tree(&[pid]);
                if tree.iter().any(|p| pid_alive(*p)) {
                    roots.extend(tree);
                } else {
                    let _ = fs::remove_file(pid_file(&profile));
                }
            }
        }
        if roots.is_empty() {
            continue;
        }
        let tree = expand_tree(&roots);
        if tree
            .iter()
            .copied()
            .any(|pid| !window_handles(pid).is_empty())
        {
            running.push(site.id.clone());
            state.pids.lock().unwrap().insert(site.id.clone(), tree);
        }
    }

    {
        let mut running_sorted = running.clone();
        running_sorted.sort();
        let mut previous = state.running_cache.lock().unwrap().clone();
        previous.sort();
        if previous != running_sorted {
            let label = |ids: &[String]| {
                ids.iter()
                    .filter_map(|id| cfg.sites.iter().find(|s| &s.id == id).map(|s| s.name.clone()))
                    .collect::<Vec<_>>()
                    .join(", ")
            };
            let opened: Vec<String> = running_sorted
                .iter()
                .filter(|id| !previous.contains(id))
                .cloned()
                .collect();
            let closed: Vec<String> = previous
                .iter()
                .filter(|id| !running_sorted.contains(id))
                .cloned()
                .collect();
            if !opened.is_empty() {
                log_line(&state.base, "info", &format!("окно открыто: {}", label(&opened)));
            }
            if !closed.is_empty() {
                log_line(&state.base, "info", &format!("окно закрыто: {}", label(&closed)));
            }
            *state.running_cache.lock().unwrap() = running_sorted;
        }
    }
    let proxy_port = *state.proxy_port.lock().unwrap();
    let health = state.health.lock().unwrap().clone();
    StateResponse {
        browser: browser_info_or_missing(&cfg),
        base: state.base.to_string_lossy().into(),
        log_path: state
            .base
            .join("logs")
            .join("synapse.log")
            .to_string_lossy()
            .into(),
        running,
        proxy: ProxyState {
            port: proxy_port,
            running: proxy_port != 0,
        },
        health,
        config: cfg,
    }
}

#[tauri::command]
fn save_config(
    state: State<Shared>,
    config: AppConfig,
    app: AppHandle,
) -> Result<StateResponse, String> {
    let mut cfg = config;
    cfg.proxy_port = cfg.proxy_port.clamp(1024, 65535);
    cfg.browser_width = cfg.browser_width.clamp(640, 7680);
    cfg.browser_height = cfg.browser_height.clamp(480, 4320);
    if cfg.doh.timeout <= 0.0 {
        cfg.doh.timeout = 8.0;
    }
    if cfg.sites.is_empty() {
        cfg.sites = default_sites();
    }

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_always_on_top(cfg.window_topmost);
    }

    write_config(&state.base, &cfg)?;
    *state.direct_hosts.lock().unwrap() = direct_hosts_from_sites(&cfg.sites);
    let bootstrap: Vec<IpAddr> = cfg
        .doh
        .bootstrap_ips
        .iter()
        .filter_map(|s| s.parse::<IpAddr>().ok())
        .collect();
    state
        .resolver
        .configure(&cfg.doh.url, bootstrap, cfg.doh.timeout, cfg.doh.fallback_tcp);
    let running_port = *state.proxy_port.lock().unwrap();
    if cfg.proxy_port != running_port {
        log_line(
            &state.base,
            "warn",
            &format!(
                "порт прокси {} применится после перезапуска (сейчас {running_port})",
                cfg.proxy_port
            ),
        );
    }
    log_line(&state.base, "info", "настройки сохранены");
    *state.config.lock().unwrap() = cfg;
    Ok(get_state(state))
}

#[tauri::command]
fn open_site(id: String, state: State<Shared>) -> Result<String, String> {
    let shared = (*state).clone();
    {
        let cfg = shared.config.lock().unwrap();
        let site = cfg
            .sites
            .iter()
            .find(|s| s.id == id)
            .ok_or_else(|| "Сайт не найден".to_string())?;
        if !site.enabled {
            return Err(format!("{} отключён в настройках", site.name));
        }
    }
    let worker = shared.clone();
    std::thread::spawn(move || match open_site_impl(&worker, &id) {
        Ok(msg) => log_line(&worker.base, "info", &format!("{id}: {msg}")),
        Err(err) => log_line(&worker.base, "warn", &format!("{id}: {err}")),
    });
    Ok("запускается".into())
}

fn open_site_impl(state: &Shared, id: &str) -> Result<String, String> {
    let cfg = state.config.lock().unwrap().clone();
    let site = cfg
        .sites
        .iter()
        .find(|s| s.id == id)
        .cloned()
        .ok_or_else(|| "Сайт не найден".to_string())?;

    let profile = state.base.join("profiles").join(safe_id(&site.id));
    let mut existing = state.pids.lock().unwrap().get(id).cloned().unwrap_or_default();
    if existing.is_empty() {
        if let Some(pid) = read_pid_file(&profile) {
            // Firefox re-execs: the recorded root may be gone while its
            // children (the real browser) are still running.
            let tree = expand_tree(&[pid]);
            if tree.iter().any(|p| pid_alive(*p)) {
                existing.extend(tree);
            } else {
                let _ = fs::remove_file(pid_file(&profile));
            }
        }
    }
    if !existing.is_empty() {
        let tree = expand_tree(&existing);
        if let Some(handle) = tree
            .iter()
            .copied()
            .filter(|p| pid_alive(*p))
            .find_map(|p| window_handles(p).first().copied())
        {
            state
                .pids
                .lock()
                .unwrap()
                .insert(id.to_string(), tree);
            focus_handle(handle);
            return Ok("окно уже открыто, показано на переднем плане".into());
        }
        // Processes are alive but no window is visible: the previous browser is
        // shutting down. Finish it so a new instance can lock the profile.
        for pid in expand_tree(&existing) {
            kill_pid(pid);
        }
        let _ = fs::remove_file(pid_file(&profile));
        state.pids.lock().unwrap().remove(id);
        std::thread::sleep(std::time::Duration::from_millis(400));
    }

    let child = launch_site(&cfg, &state.base, &site, *state.proxy_port.lock().unwrap())?;
    let pid = child.id();
    let pinned = site.pinned;
    state
        .children
        .lock()
        .unwrap()
        .insert(id.to_string(), child);
    state
        .pids
        .lock()
        .unwrap()
        .insert(id.to_string(), vec![pid]);

    {
        let shared = (*state).clone();
        let site_id = id.to_string();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(1500));
            let tree = expand_tree(&[pid]);
            shared
                .pids
                .lock()
                .unwrap()
                .insert(site_id, tree.clone());
            if pinned {
                apply_pin(pid, true);
            }
            if let Some(handle) = tree.iter().find_map(|p| window_handles(*p).first().copied()) {
                focus_handle(handle);
            }
        });
    }
    {
        let shared = (*state).clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(2500));
            reapply_pins(&shared);
        });
    }
    Ok("открывается".into())
}

#[tauri::command]
fn toggle_direct(id: String, state: State<Shared>) -> Result<bool, String> {
    let (direct, cfg) = {
        let mut config = state.config.lock().unwrap();
        let site = config
            .sites
            .iter_mut()
            .find(|s| s.id == id)
            .ok_or_else(|| "Сайт не найден".to_string())?;
        site.direct = !site.direct;
        (site.direct, config.clone())
    };
    write_config(&state.base, &cfg)?;
    *state.direct_hosts.lock().unwrap() = direct_hosts_from_sites(&cfg.sites);
    *state.config.lock().unwrap() = cfg;
    log_line(
        &state.base,
        "info",
        &format!(
            "{id}: обход DoH {}",
            if direct { "включён" } else { "выключен" }
        ),
    );
    Ok(direct)
}

fn site_host(url: &str) -> String {
    url.trim_start_matches("https://")
        .trim_start_matches("http://")
        .split('/')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
}

fn check_one(site: &Site, proxy_port: u16) -> SiteHealth {
    let checked = chrono::Local::now().format("%H:%M:%S").to_string();
    const UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36";

    if proxy_port == 0 {
        return SiteHealth {
            ok: false,
            detail: "прокси не запущен".into(),
            checked,
        };
    }

    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(10))
        .user_agent(UA)
        .use_rustls_tls()
        .http1_only()
        .proxy(
            reqwest::Proxy::http(format!("http://127.0.0.1:{proxy_port}"))
                .unwrap_or_else(|_| reqwest::Proxy::all("http://127.0.0.1").unwrap()),
        )
        .build()
    {
        Ok(client) => client,
        Err(err) => {
            return SiteHealth {
                ok: false,
                detail: format!("клиент: {err}"),
                checked,
            }
        }
    };

    match client.get(&site.url).send() {
        Ok(response) => {
            let code = response.status().as_u16();
            // 4xx means the host answered but blocked the bot-like request
            // (Cloudflare and similar), so the site itself is reachable.
            if code < 500 {
                SiteHealth {
                    ok: true,
                    detail: if code < 400 {
                        format!("HTTP {code}")
                    } else {
                        format!("HTTP {code} (защита сайта)")
                    },
                    checked,
                }
            } else {
                SiteHealth {
                    ok: false,
                    detail: format!("HTTP {code}"),
                    checked,
                }
            }
        }
        Err(err) => SiteHealth {
            ok: false,
            detail: err.to_string(),
            checked,
        },
    }
}

#[tauri::command]
fn check_sites(state: State<Shared>) -> HashMap<String, SiteHealth> {
    let sites: Vec<Site> = state
        .config
        .lock()
        .unwrap()
        .sites
        .iter()
        .filter(|s| s.enabled)
        .cloned()
        .collect();
    let proxy_port = *state.proxy_port.lock().unwrap();
    let mut results: HashMap<String, SiteHealth> = HashMap::new();

    std::thread::scope(|scope| {
        let handles: Vec<_> = sites
            .iter()
            .map(|site| scope.spawn(move || (site.clone(), check_one(site, proxy_port))))
            .collect();

        for handle in handles {
            if let Ok((site, health)) = handle.join() {
                if health.ok {
                    log_line(
                        &state.base,
                        "info",
                        &format!("проверка {}: доступен ({})", site.name, health.detail),
                    );
                } else {
                    log_line(
                        &state.base,
                        "warn",
                        &format!("проверка {}: недоступен — {}", site.name, health.detail),
                    );
                }
                results.insert(site.id.clone(), health);
            }
        }
    });

    *state.health.lock().unwrap() = results.clone();
    results
}

fn reapply_pins(state: &Shared) {
    let sites: Vec<Site> = state.config.lock().unwrap().sites.clone();
    // Apply from the bottom of the list to the top, so the first site ends up
    // above the others in the topmost group.
    for site in sites.iter().filter(|s| s.pinned && s.enabled).rev() {
        let roots = state
            .pids
            .lock()
            .unwrap()
            .get(&site.id)
            .cloned()
            .unwrap_or_default();
        for pid in expand_tree(&roots).iter().copied().filter(|p| pid_alive(*p)) {
            for raw in window_handles(pid) {
                set_topmost(raw, true, true);
                raise_topmost(raw);
            }
        }
    }
}

fn enforce_pins(state: &Shared) {
    let sites: Vec<Site> = state.config.lock().unwrap().sites.clone();
    for site in sites.iter().filter(|s| s.pinned && s.enabled) {
        let roots = state
            .pids
            .lock()
            .unwrap()
            .get(&site.id)
            .cloned()
            .unwrap_or_default();
        for pid in expand_tree(&roots).iter().copied().filter(|p| pid_alive(*p)) {
            apply_pin(pid, true);
        }
    }
}

#[tauri::command]
fn apply_pin_order(state: State<Shared>) -> Result<(), String> {
    reapply_pins(&state);
    log_line(&state.base, "info", "приоритет закрепления обновлён");
    Ok(())
}

#[tauri::command]
fn reorder_sites(ids: Vec<String>, state: State<Shared>) -> Result<(), String> {
    let snapshot = {
        let mut cfg = state.config.lock().unwrap();
        let mut ordered: Vec<Site> = Vec::new();
        for id in &ids {
            if let Some(site) = cfg.sites.iter().find(|s| &s.id == id) {
                ordered.push(site.clone());
            }
        }
        for site in cfg.sites.iter() {
            if !ordered.iter().any(|o| &o.id == &site.id) {
                ordered.push(site.clone());
            }
        }
        cfg.sites = ordered;
        cfg.clone()
    };
    write_config(&state.base, &snapshot)?;
    *state.config.lock().unwrap() = snapshot;
    reapply_pins(&state);
    log_line(&state.base, "info", "порядок сайтов обновлён");
    Ok(())
}

#[tauri::command]
fn toggle_pin(id: String, state: State<Shared>) -> Result<bool, String> {
    let (pinned, cfg) = {
        let mut config = state.config.lock().unwrap();
        let site = config
            .sites
            .iter_mut()
            .find(|s| s.id == id)
            .ok_or_else(|| "Сайт не найден".to_string())?;
        site.pinned = !site.pinned;
        (site.pinned, config.clone())
    };
    write_config(&state.base, &cfg)?;

    if pinned {
        *state.config.lock().unwrap() = cfg;
        reapply_pins(&state);
    } else {
        let pids = state.pids.lock().unwrap().get(&id).cloned().unwrap_or_default();
        for pid in expand_tree(&pids).iter().copied().filter(|p| pid_alive(*p)) {
            apply_pin(pid, false);
        }
        *state.config.lock().unwrap() = cfg;
        reapply_pins(&state);
    }
    log_line(
        &state.base,
        "info",
        &format!(
            "{id}: закрепление {}",
            if pinned { "включено" } else { "выключено" }
        ),
    );
    Ok(pinned)
}

#[tauri::command]
fn open_all(state: State<Shared>) -> Result<Vec<String>, String> {
    let sites: Vec<Site> = state
        .config
        .lock()
        .unwrap()
        .sites
        .iter()
        .filter(|s| s.enabled)
        .cloned()
        .collect();
    let mut result = Vec::new();
    for site in sites {
        let outcome = open_site_impl(&state, &site.id);
        match outcome {
            Ok(msg) => result.push(format!("{}: {msg}", site.name)),
            Err(err) => result.push(format!("{}: {err}", site.name)),
        }
    }
    Ok(result)
}

#[tauri::command]
fn read_log(state: State<Shared>, limit: Option<usize>) -> Result<Vec<String>, String> {
    let path = state.base.join("logs").join("synapse.log");
    let text = fs::read_to_string(path).unwrap_or_default();
    let lines: Vec<String> = text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| l.to_string())
        .collect();
    let take = limit.unwrap_or(500).min(lines.len());
    Ok(lines[lines.len() - take..].to_vec())
}

#[tauri::command]
fn clear_log(state: State<Shared>) -> Result<(), String> {
    let path = state.base.join("logs").join("synapse.log");
    fs::write(path, b"").map_err(|e| e.to_string())
}

fn cleanup_stale_browsers(state: &Shared) {
    if !state.config.lock().unwrap().close_browsers_on_exit {
        return;
    }
    let sites: Vec<Site> = state.config.lock().unwrap().sites.clone();
    for site in sites {
        let profile = state.base.join("profiles").join(safe_id(&site.id));
        let Some(pid) = read_pid_file(&profile) else {
            continue;
        };
        let tree = expand_tree(&[pid]);
        let alive: Vec<u32> = tree.iter().copied().filter(|p| pid_alive(*p)).collect();
        if alive.is_empty() {
            let _ = fs::remove_file(pid_file(&profile));
            continue;
        }
        let had_window = alive
            .iter()
            .any(|p| !window_handles(*p).is_empty());
        if had_window {
            for p in &alive {
                kill_pid(*p);
            }
            log_line(
                &state.base,
                "info",
                &format!("{}: закрыто после аварийного завершения", site.name),
            );
        }
        let _ = fs::remove_file(pid_file(&profile));
    }
}

fn shutdown_children(state: &Shared, close: bool) {
    let mut children = match state.children.lock() {
        Ok(c) => c,
        Err(_) => return,
    };
    if close {
        let sites = state.config.lock().unwrap().sites.clone();
        for site in sites {
            let profile = state.base.join("profiles").join(safe_id(&site.id));
            let mut roots = state
                .pids
                .lock()
                .unwrap()
                .get(&site.id)
                .cloned()
                .unwrap_or_default();
            if roots.is_empty() {
                if let Some(pid) = read_pid_file(&profile) {
                    roots.push(pid);
                }
            }
            if !roots.is_empty() {
                for pid in expand_tree(&roots) {
                    kill_pid(pid);
                }
            }
            let _ = fs::remove_file(pid_file(&profile));
        }
        let trees = state.pids.lock().unwrap().clone();
        for roots in trees.values() {
            for pid in expand_tree(roots) {
                kill_pid(pid);
            }
        }
        for (_, child) in children.iter_mut() {
            let _ = child.kill();
        }
    }
    children.clear();
    state.pids.lock().unwrap().clear();
    if close {
        for site in state.config.lock().unwrap().sites.iter() {
            let profile = state.base.join("profiles").join(safe_id(&site.id));
            let _ = fs::remove_file(pid_file(&profile));
        }
    }
}

fn run_doh_check(host: &str) -> i32 {
    let base = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|p| p.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));
    let cfg = load_config(&base);
    let bootstrap: Vec<IpAddr> = cfg
        .doh
        .bootstrap_ips
        .iter()
        .filter_map(|s| s.parse::<IpAddr>().ok())
        .collect();
    let resolver = DohResolver::new(&cfg.doh.url, bootstrap, cfg.doh.timeout, false);
    let endpoint = resolver.endpoint_url().unwrap_or_else(|| "системный DNS".into());
    match resolver.resolve(host) {
        Ok(ips) => {
            let list = ips
                .iter()
                .map(|ip| ip.to_string())
                .collect::<Vec<_>>()
                .join(", ");
            log_line(&base, "info", &format!("doh-check {host} через {endpoint}: {list}"));
            0
        }
        Err(err) => {
            log_line(&base, "warn", &format!("doh-check {host}: {err}"));
            1
        }
    }
}

fn main() {
    if let Some(host) = std::env::args()
        .skip(1)
        .find_map(|a| a.strip_prefix("--doh-check=").map(|s| s.to_string()))
    {
        std::process::exit(run_doh_check(&host));
    }

    tauri::Builder::default()
        .setup(|app| {
            let base = resolve_base(&app.handle().clone());
            let _ = fs::create_dir_all(&base);

            let lock_path = base.join("app.lock");
            if let Ok(text) = fs::read_to_string(&lock_path) {
                if let Ok(pid) = text.trim().parse::<u32>() {
                    if pid != std::process::id() && pid_alive(pid) {
                        log_line(&base, "info", &format!("уже запущен экземпляр pid {pid}"));
                        std::process::exit(0);
                    }
                }
            }
            let _ = fs::write(&lock_path, std::process::id().to_string());
            let cfg = load_config(&base);
            if !config_path(&base).exists() {
                let _ = write_config(&base, &cfg);
            }
            log_line(
                &base,
                "info",
                &format!(
                    "Synapse запущен, папка данных: {}",
                    base.to_string_lossy()
                ),
            );
            if cfg.doh.url.trim().is_empty() {
                log_line(&base, "info", "DoH не настроен — используется системный DNS");
            } else {
                log_line(&base, "info", &format!("DoH: {}", cfg.doh.url));
            }
            if let Some(b) = detect_browser(&cfg) {
                log_line(&base, "info", &format!("найден браузер: {} ({})", b.name, b.path));
            } else {
                log_line(&base, "warn", "браузер не найден — откройте настройки");
            }

            let bootstrap: Vec<IpAddr> = cfg
                .doh
                .bootstrap_ips
                .iter()
                .filter_map(|s| s.parse::<IpAddr>().ok())
                .collect();
            let resolver = Arc::new(DohResolver::new(
                &cfg.doh.url,
                bootstrap,
                cfg.doh.timeout,
                cfg.doh.fallback_tcp,
            ));

            let direct_hosts = Arc::new(Mutex::new(direct_hosts_from_sites(&cfg.sites)));
            let proxy_log_base = base.clone();
            let logger: proxy::LogFn =
                Arc::new(move |msg: String| log_line(&proxy_log_base, "info", &msg));
            let proxy_port = match proxy::start(
                cfg.proxy_port,
                resolver.clone(),
                cfg.allowed_ports.clone(),
                direct_hosts.clone(),
                logger,
            ) {
                Ok(port) => {
                    log_line(
                        &base,
                        "info",
                        &format!("локальный прокси слушает 127.0.0.1:{port}"),
                    );
                    port
                }
                Err(err) => {
                    log_line(&base, "warn", &format!("прокси не запустился: {err}"));
                    0
                }
            };

            if let Some(window) = app.get_webview_window("main") {
                if let (Some((w, h, x, y)), Ok(hwnd)) = (parse_geometry(&cfg.window_geometry), window.hwnd()) {
                    apply_window_geometry(hwnd, w, h, x, y);
                }
                if cfg.window_topmost {
                    let _ = window.set_always_on_top(true);
                }
            }

            let shared = Shared {
                base: base.clone(),
                config: Arc::new(Mutex::new(cfg)),
                children: Arc::new(Mutex::new(HashMap::new())),
                pids: Arc::new(Mutex::new(HashMap::new())),
                resolver: resolver.clone(),
                proxy_port: Arc::new(Mutex::new(proxy_port)),
                direct_hosts: direct_hosts.clone(),
                health: Arc::new(Mutex::new(HashMap::new())),
                running_cache: Arc::new(Mutex::new(Vec::new())),
            };
            let _ = STATE.set(shared.clone());
            app.manage(shared.clone());
            cleanup_stale_browsers(&shared);

            {
                let warmer = shared.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(400));
                    let info = {
                        let cfg = warmer.config.lock().unwrap();
                        let browser = detect_browser(&cfg);
                        let port = *warmer.proxy_port.lock().unwrap();
                        let sites: Vec<Site> = cfg
                            .sites
                            .iter()
                            .filter(|s| s.enabled)
                            .cloned()
                            .collect();
                        (browser, port, sites, cfg.browser_width, cfg.browser_height)
                    };
                    if let (Some(browser), port, sites, width, height) = info {
                        for site in &sites {
                            let host = site_host(&site.url);
                            if !host.is_empty() {
                                let _ = warmer.resolver.resolve(&host);
                            }
                        }
                        if browser.kind == "firefox" && port != 0 {
                            for site in sites {
                                let profile =
                                    warmer.base.join("profiles").join(safe_id(&site.id));
                                if fs::create_dir_all(&profile).is_ok() {
                                    write_firefox_prefs(&profile, port, width, height);
                                }
                            }
                            log_line(&warmer.base, "info", "профили Firefox подготовлены");
                        }
                    }
                });
            }

            {
                let keeper = shared.clone();
                std::thread::spawn(move || loop {
                    std::thread::sleep(std::time::Duration::from_millis(2000));
                    enforce_pins(&keeper);
                });
            }

            {
                let starter = shared.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(1500));
                    reapply_pins(&starter);
                });
            }

            if let Some(list) = std::env::args()
                .skip(1)
                .find_map(|a| a.strip_prefix("--open=").map(|s| s.to_string()))
            {
                let ids: Vec<String> = list
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();
                let shared = shared.clone();
                std::thread::spawn(move || {
                    for (index, id) in ids.iter().enumerate() {
                        if index > 0 {
                            std::thread::sleep(std::time::Duration::from_millis(2500));
                        }
                        match open_site_impl(&shared, id) {
                            Ok(msg) => log_line(&shared.base, "info", &format!("--open {id}: {msg}")),
                            Err(err) => log_line(&shared.base, "warn", &format!("--open {id}: {err}")),
                        }
                    }
                });
            }

            log_line(&base, "info", "setup: готово");
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                if window.label() != "main" {
                    return;
                }
                if let Some(state) = STATE.get() {
                    log_line(&state.base, "info", "закрытие: начало");
                    if let Ok(hwnd) = window.hwnd() {
                        if let Some(geometry) = read_window_geometry(hwnd) {
                            let mut cfg = state.config.lock().unwrap();
                            cfg.window_geometry = geometry;
                            let _ = write_config(&state.base, &cfg);
                            let close = cfg.close_browsers_on_exit;
                            drop(cfg);
                            log_line(&state.base, "info", "закрытие: конфиг сохранён");
                            shutdown_children(&state, close);
                            log_line(&state.base, "info", "закрытие: браузеры остановлены");
                            let _ = fs::remove_file(state.base.join("app.lock"));
                            log_line(&state.base, "info", "приложение закрыто");
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_state,
            save_config,
            open_site,
            open_all,
            reorder_sites,
            apply_pin_order,
            toggle_pin,
            toggle_direct,
            check_sites,
            read_log,
            clear_log
        ])
        .run(tauri::generate_context!())
        .expect("error while running Synapse");
}
