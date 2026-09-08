//! ai-quotas-tray: minimal Windows tray icon for the ai-quotas WSL dashboard.
//!
//! Polls http://127.0.0.1:47623/api/quotas every 5 minutes and renders one
//! tray icon whose color encodes the worst provider usage (green < 70%,
//! amber < 90%, red >= 90%, gray when the API is unreachable). The right-click
//! menu mirrors the CodexBar popover structure: one submenu per provider with
//! rows like "Session   98% left   resets in 4h 44m" plus balances; left
//! click opens the dashboard.
//!
//! Pure win32 via windows-sys (no framework, no runtime): target RAM is a
//! couple of megabytes.

#![windows_subsystem = "windows"]
#![cfg(windows)]

use std::collections::HashMap;
use std::net::TcpStream;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{LazyLock, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::Value;
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::{CreateBitmap, DeleteObject, HBITMAP};
use windows_sys::Win32::UI::Shell::{
    SetCurrentProcessExplicitAppUserModelID, Shell_NotifyIconW, ShellExecuteW, NIF_ICON, NIF_INFO,
    NIF_MESSAGE, NIF_TIP, NIIF_ERROR, NIIF_WARNING, NIM_ADD, NIM_DELETE, NIM_MODIFY,
    NOTIFYICONDATAW,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreateIconIndirect, ICONINFO, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyIcon,
    DestroyMenu, DestroyWindow, DispatchMessageW, GetCursorPos, GetMessageW, PostMessageW,
    RegisterClassW, SetForegroundWindow, ShowWindow, TrackPopupMenu, TranslateMessage,
    CW_USEDEFAULT, HICON, HMENU, MF_GRAYED, MF_POPUP, MF_SEPARATOR, MF_STRING, SW_HIDE,
    SW_SHOWNORMAL, TPM_BOTTOMALIGN, TPM_LEFTALIGN, TPM_RETURNCMD, WM_APP, WNDCLASSW,
    WS_OVERLAPPEDWINDOW,
};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/// AppUserModelID: without it Windows treats the toasts as coming from an
/// unidentified app (silently dropped, absent from notification settings).
const AUMID: &str = "Bruno.aiquotas.tray";
const API_HOST_PORT: &str = "127.0.0.1:47623";
const DASHBOARD_URL: &str = "http://localhost:47623";
/// Background poll cadence. The fetch runs on its OWN thread so a slow or
/// dead backend never freezes the UI message loop.
const POLL_SECS: u64 = 120;
/// Every N polls the tray asks the back to force-refresh upstream, keeping
/// the shared data fresh for the dashboard too (same rate as its own TTL).
const FORCE_EVERY_POLLS: usize = 5;
const WM_TRAYICON: u32 = WM_APP + 1;
const WM_DATA_READY: u32 = WM_APP + 2;
const WM_NULL: u32 = 0x0000;
const WM_RBUTTONUP: u32 = 0x0204;
const WM_LBUTTONUP: u32 = 0x0202;
const ID_DASHBOARD: usize = 100;
const ID_EXIT: usize = 101;
/// Desktop toast fires when a window's usage crosses one of these levels
/// upward (only fresh `ok` records participate): a heads-up at 80% and then
/// a step every 5% until the window is gone. A poll that jumps several
/// levels fires a single toast for the highest one reached.
const NOTIFY_LEVELS: [f64; 5] = [80.0, 85.0, 90.0, 95.0, 100.0];

/// Icon states, drawn as solid 16x16 circles.
#[derive(Clone, Copy, PartialEq)]
enum State {
    Green,
    Amber,
    Red,
    Gray,
}

impl State {
    fn rgb(self) -> (u8, u8, u8) {
        match self {
            State::Green => (63, 185, 80),
            State::Amber => (210, 153, 34),
            State::Red => (248, 81, 73),
            State::Gray => (110, 119, 129),
        }
    }
}

// ---------------------------------------------------------------------------
// Minimal HTTP/1.1 client over TcpStream (localhost only, no TLS, no crates)
// ---------------------------------------------------------------------------

/// GET an arbitrary path (used for /api/quotas?refresh=1 force rounds).
fn http_get_json_path(host_port: &str, path: &str) -> Result<Value, String> {
    use std::io::{Read, Write};
    let mut stream = TcpStream::connect(host_port).map_err(|e| format!("connect: {e}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(15)))
        .map_err(|e| format!("timeout: {e}"))?;
    stream
        .set_write_timeout(Some(Duration::from_secs(15)))
        .map_err(|e| format!("timeout: {e}"))?;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {host_port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("write: {e}"))?;
    let mut raw = Vec::with_capacity(16 * 1024);
    stream.read_to_end(&mut raw).map_err(|e| format!("read: {e}"))?;
    let text = String::from_utf8_lossy(&raw);
    let body = match text.split_once("\r\n\r\n") {
        Some((_, body)) => body,
        None => return Err("no HTTP body".into()),
    };
    serde_json::from_str(body).map_err(|e| format!("json: {e}"))
}

// ---------------------------------------------------------------------------
// CodexBar-style menu model: one section per provider, rows label + right
// ---------------------------------------------------------------------------

/// One quota row inside a provider submenu: left label, right-aligned value.
#[derive(Clone)]
struct MenuRow {
    label: String,
    right: String,
    /// Record age in seconds (from /api/quotas age_seconds), when known.
    age_secs: Option<u64>,
}

/// One provider section (a submenu).
#[derive(Clone)]
struct ProviderSection {
    name: String,
    rows: Vec<MenuRow>,
}

/// "5h 1m" / "4d 23h" style duration from milliseconds.
fn fmt_duration(ms: i64) -> String {
    if ms <= 0 {
        return "ahora".into();
    }
    let mins = ms / 60_000;
    let days = mins / (60 * 24);
    let hours = (mins % (60 * 24)) / 60;
    let m = mins % 60;
    if days > 0 {
        format!("{days}d {hours}h")
    } else if hours > 0 {
        format!("{hours}h {m}m")
    } else {
        format!("{m}m")
    }
}

fn resets_suffix(resets_at: Option<&str>) -> String {
    match resets_at
        .and_then(|t| chrono_parse_ms(t))
        .map(|target| target - now_ms())
    {
        Some(ms) => format!(" · resets in {}", fmt_duration(ms)),
        None => String::new(),
    }
}

/// RFC3339 -> unix ms (dependency-free subset: seconds + optional fraction).
fn chrono_parse_ms(raw: &str) -> Option<i64> {
    let trimmed = raw.trim();
    // Format: 2026-09-07T18:00:00(.fff)?(Z|+00:00)
    let (date, rest) = trimmed.split_once('T')?;
    let mut d = date.split('-');
    let year: i64 = d.next()?.parse().ok()?;
    let month: i64 = d.next()?.parse().ok()?;
    let day: i64 = d.next()?.parse().ok()?;
    let (time, _tz) = rest.split_at(rest.find(['+', 'Z', '-']).unwrap_or(rest.len()));
    let mut t = time.split(':');
    let hour: i64 = t.next()?.parse().ok()?;
    let min: i64 = t.next()?.parse().ok()?;
    let sec_part = t.next().unwrap_or("0");
    let sec: i64 = sec_part.split('.').next()?.parse().ok()?;
    // days from civil algorithm (Howard Hinnant) for UTC; local offsets in
    // resets_at are close enough for countdown display purposes.
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some((days * 86_400 + hour * 3600 + min * 60 + sec) * 1000)
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

fn currency_symbol(cur: Option<&str>) -> String {
    match cur {
        Some("EUR") => "€".into(),
        Some("USD") => "$".into(),
        Some("CNY") => "¥".into(),
        Some(other) => format!("{other} "),
        None => String::new(),
    }
}

/// Human wording per statuspage indicator (mirrors the dashboard badge).
fn severity_label(indicator: &str) -> String {
    match indicator {
        "minor" => "degraded performance".into(),
        "major" => "partial outage".into(),
        "critical" => "major outage".into(),
        "maintenance" => "maintenance".into(),
        other => other.to_string(),
    }
}

/// Build the CodexBar-style sections from /api/quotas records (+ /api/status
/// incident map for the optional status row).
fn summarize(payload: &Value, status: &Value) -> Vec<ProviderSection> {
    let mut order: Vec<String> = Vec::new();
    let mut sections: HashMap<String, Vec<MenuRow>> = HashMap::new();
    let mut names: HashMap<String, String> = HashMap::new();
    let records = payload
        .get("records")
        .and_then(Value::as_array)
        .map(|a| a.as_slice())
        .unwrap_or(&[]);
    for record in records {
        let Some(provider) = record.get("provider").and_then(Value::as_str) else {
            continue;
        };
        if !sections.contains_key(provider) {
            order.push(provider.to_string());
            sections.insert(provider.to_string(), Vec::new());
        }
        names
            .entry(provider.to_string())
            .or_insert_with(|| {
                record
                    .get("display_name")
                    .and_then(Value::as_str)
                    .unwrap_or(provider)
                    .to_string()
            });

        let state = record.get("state").and_then(Value::as_str).unwrap_or("");
        let label = record
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or("cuota")
            .to_string();
        let resets = resets_suffix(record.get("resets_at").and_then(Value::as_str));

        let age_secs: Option<u64> = record.get("age_seconds").and_then(Value::as_u64);
        // Same staleness threshold as the dashboard's stalemark: 15 min.
        let stale_suffix = match age_secs {
            Some(secs) if secs > 900 => format!(" · hace {}", fmt_duration(secs as i64 * 1000)),
            _ => String::new(),
        };
        let right = if state == "missing" {
            "sin datos".to_string()
        } else if state == "error" {
            "error".to_string()
        } else if record.get("kind").and_then(Value::as_str) == Some("balance") {
            let cur = currency_symbol(record.get("currency").and_then(Value::as_str));
            match (
                record.get("used").and_then(Value::as_f64),
                record.get("limit").and_then(Value::as_f64),
            ) {
                (Some(used), Some(limit)) => format!("{cur}{used:.2} / {limit:.2}{resets}"),
                (None, Some(limit)) => format!("{cur}{limit:.2}{resets}"),
                _ => "—".into(),
            }
        } else if record.get("unit").and_then(Value::as_str) == Some("requests") {
            let used = record.get("used").and_then(Value::as_f64).unwrap_or(0.0);
            let limit = record.get("limit").and_then(Value::as_f64).unwrap_or(0.0);
            format!("{}/{} · quedan {}{}", used as u64, limit as u64,
                (limit - used).max(0.0) as u64, resets)
        } else {
            match record.get("used_percent").and_then(Value::as_f64) {
                Some(pct) if state == "ok" => {
                    format!("{}%{}{}", pct.round() as i64, resets, stale_suffix)
                }
                Some(pct) => format!("{}% ({state}){}", pct.round() as i64, stale_suffix),
                None => "—".into(),
            }
        };

        sections.get_mut(provider).unwrap().push(MenuRow {
            label,
            right,
            age_secs,
        });
        // Earned limit-reset credits (CodexBar's "Limit Reset Credits" row):
        // one extra row right under the window they belong to.
        if let Some(rc) = record.get("reset_credits") {
            let available = rc.get("available").and_then(Value::as_u64).unwrap_or(0);
            if available > 0 {
                let next_exp = rc
                    .get("credits")
                    .and_then(Value::as_array)
                    .map(|credits| {
                        credits
                            .iter()
                            .filter_map(|c| c.get("expires_at").and_then(Value::as_str))
                            .filter_map(chrono_parse_ms)
                            .min()
                    })
                    .flatten();
                let exp = next_exp
                    .map(|ms| {
                        let d = chrono::date_from_unix_ms(ms);
                        format!(" \u{00b7} exp {}", d)
                    })
                    .unwrap_or_default();
                sections.get_mut(provider).unwrap().push(MenuRow {
                    label: format!("\u{21b3} limit resets"),
                    right: format!("{available} available{exp}"),
                    age_secs,
                });
            }
        }
    }
    order
        .into_iter()
        .map(|provider| {
            let name = names.get(&provider).cloned().unwrap_or_else(|| provider.clone());
            let mut rows = sections.remove(&provider).unwrap_or_default();
            // Incident row on top when the provider's status page is degraded.
            if let Some(indicator) = status
                .get("providers")
                .and_then(|p| p.get(&provider))
                .and_then(|p| p.get("indicator"))
                .and_then(Value::as_str)
                .filter(|i| *i != "none" && !i.is_empty())
            {
                let summary = status
                    .get("providers")
                    .and_then(|p| p.get(&provider))
                    .and_then(|p| p.get("summary"))
                    .and_then(Value::as_str)
                    .unwrap_or("");
                rows.insert(
                    0,
                    MenuRow {
                        label: format!("\u{26a0} status: {}", severity_label(indicator)),
                        right: summary.chars().take(40).collect(),
                        age_secs: None,
                    },
                );
            }
            ProviderSection {
                name,
                rows,
            }
        })
        .collect()
}

/// Minimal date text ("21 Sep") from unix ms — no chrono dependency.
mod chrono {
    pub fn date_from_unix_ms(ms: i64) -> String {
        const MONTHS: [&str; 12] = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ];
        let secs = ms.div_euclid(1000);
        let days = secs.div_euclid(86_400);
        // civil-from-days (Howard Hinnant)
        let z = days + 719_468;
        let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
        let doe = z - era * 146_097;
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let day = doy - (153 * mp + 2) / 5 + 1;
        let month = if mp < 10 { mp + 3 } else { mp - 9 };
        let _ = y;
        format!("{day} {}", MONTHS[(month - 1) as usize])
    }
}

/// Worst usage across fresh window rows, driving the icon color.
fn worst_state(payload: &Value) -> State {
    let mut worst: Option<f64> = None;
    for record in payload
        .get("records")
        .and_then(Value::as_array)
        .map(|a| a.as_slice())
        .unwrap_or(&[])
    {
        if record.get("state").and_then(Value::as_str) != Some("ok") {
            continue;
        }
        if let Some(pct) = record.get("used_percent").and_then(Value::as_f64) {
            worst = Some(worst.map_or(pct, |prev: f64| prev.max(pct)));
        }
    }
    match worst {
        Some(pct) if pct >= 90.0 => State::Red,
        Some(pct) if pct >= 70.0 => State::Amber,
        Some(_) => State::Green,
        None => State::Green,
    }
}

/// Derive the full snapshot from /api/quotas + /api/status payloads.
fn build_snapshot(payload: &Value, status: &Value) -> Snapshot {
    let state = worst_state(payload);
    let sections = summarize(payload, status);
    let alerts = check_thresholds(payload);
    let mut parts: Vec<String> = Vec::new();
    let mut oldest: Option<u64> = None;
    for section in &sections {
        if let Some(age) = section.rows.iter().filter_map(|r| r.age_secs).max() {
            oldest = Some(oldest.map_or(age, |prev: u64| prev.max(age)));
        }
        let worst_pct: Option<f64> = payload
            .get("records")
            .and_then(Value::as_array)
            .map(|records| {
                records
                    .iter()
                    .filter(|r| {
                        r.get("provider").and_then(Value::as_str)
                            == Some(section.name.as_str())
                            || section.name.starts_with(
                                r.get("provider").and_then(Value::as_str).unwrap_or(""),
                            )
                    })
                    .filter_map(|r| r.get("used_percent").and_then(Value::as_f64))
                    .fold(None::<f64>, |acc, pct| {
                        Some(acc.map_or(pct, |prev: f64| prev.max(pct)))
                    })
            })
            .unwrap_or(None);
        match worst_pct {
            Some(pct) => parts.push(format!("{} {:.0}%", section.name.split_whitespace().next().unwrap_or(&section.name), pct)),
            None => parts.push(section.name.clone()),
        }
    }
    let mut tooltip: String = if parts.is_empty() {
        "sin datos".into()
    } else {
        parts.join(" · ")
    };
    // Old data must never look fresh: flag the age once it exceeds 15 min.
    if let Some(age) = oldest {
        if age > 900 {
            tooltip.push_str(&format!(" \u{25b3} hace {}", fmt_duration(age as i64 * 1000)));
        }
    }
    tooltip.insert(0, '\u{25CF}');
    Snapshot {
        state,
        tooltip,
        sections,
        alerts,
    }
}

// ---------------------------------------------------------------------------
// Icon rendering: solid 16x16 circle built in memory (no .ico files)
// ---------------------------------------------------------------------------

fn circle_pixels(rgb: (u8, u8, u8)) -> Vec<u8> {
    const SIZE: usize = 16;
    let mut px = vec![0u8; SIZE * SIZE * 4];
    let center = (SIZE - 1) as f32 / 2.0;
    for y in 0..SIZE {
        for x in 0..SIZE {
            let dx = x as f32 - center;
            let dy = y as f32 - center;
            if dx * dx + dy * dy <= 6.5 * 6.5 {
                let i = (y * SIZE + x) * 4;
                px[i] = rgb.2; // B
                px[i + 1] = rgb.1; // G
                px[i + 2] = rgb.0; // R
                px[i + 3] = 0xFF; // A
            }
        }
    }
    px
}

unsafe fn make_icon(state: State) -> HICON {
    let pixels = circle_pixels(state.rgb());
    let color: HBITMAP = CreateBitmap(16, 16, 1, 32, pixels.as_ptr() as *const _);
    let mask_bits = [0u8; 32];
    let mask: HBITMAP = CreateBitmap(16, 16, 1, 1, mask_bits.as_ptr() as *const _);
    let mut icon: HICON = std::mem::zeroed();
    if color != std::mem::zeroed() && mask != std::mem::zeroed() {
        let mut info: ICONINFO = std::mem::zeroed();
        info.fIcon = 1;
        info.hbmMask = mask;
        info.hbmColor = color;
        icon = CreateIconIndirect(&mut info);
    }
    if color != std::mem::zeroed() {
        DeleteObject(color);
    }
    if mask != std::mem::zeroed() {
        DeleteObject(mask);
    }
    icon
}

// ---------------------------------------------------------------------------
// Tray plumbing + app state (single message-loop thread: static is safe)
// ---------------------------------------------------------------------------

struct App {
    icon: HICON,
    sections: Vec<ProviderSection>,
}

/// Latest background poll result, handed from the worker thread to the UI
/// thread via WM_DATA_READY.
struct Snapshot {
    state: State,
    tooltip: String,
    sections: Vec<ProviderSection>,
    /// Desktop toasts to fire: (text, is_error).
    alerts: Vec<(String, bool)>,
}

static SHARED: Mutex<Option<Snapshot>> = Mutex::new(None);
static POLL_COUNT: AtomicUsize = AtomicUsize::new(0);

/// Last seen usage per "provider/label" — drives upward-crossing detection.
static LAST_PCTS: LazyLock<Mutex<HashMap<String, f64>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

/// Desktop toast alerts when a quota window crosses a level upward. First
/// poll never alerts (no previous value -> nothing to compare).
fn check_thresholds(payload: &Value) -> Vec<(String, bool)> {
    let mut prev = LAST_PCTS.lock().unwrap_or_else(|p| p.into_inner());
    let mut alerts: Vec<(String, bool)> = Vec::new();
    for record in payload
        .get("records")
        .and_then(Value::as_array)
        .map(|a| a.as_slice())
        .unwrap_or(&[])
    {
        if record.get("state").and_then(Value::as_str) != Some("ok") {
            continue;
        }
        let Some(pct) = record.get("used_percent").and_then(Value::as_f64) else {
            continue;
        };
        let provider = record.get("provider").and_then(Value::as_str).unwrap_or("?");
        let name = record
            .get("display_name")
            .and_then(Value::as_str)
            .unwrap_or(provider);
        let label = record
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or("cuota");
        let key = format!("{provider}/{label}");
        let old = prev.insert(key.clone(), pct);
        let Some(old) = old else { continue };
        let crossed: Vec<f64> = NOTIFY_LEVELS
            .iter()
            .copied()
            .filter(|&level| old < level && pct >= level)
            .collect();
        if let Some(&highest) = crossed.last() {
            alerts.push((
                format!(
                    "{} {} al {}%{}",
                    name,
                    label,
                    pct.round() as i64,
                    if highest >= 100.0 { " \u{2014} agotada" } else { "" }
                ),
                highest >= 100.0,
            ));
        }
    }
    alerts
}

static mut APP: Option<App> = None;

unsafe fn app() -> &'static mut App {
    APP.as_mut().expect("APP initialized before message loop")
}

unsafe fn tray_add(hwnd: HWND, icon: HICON, tooltip: &str) {
    let mut wide: Vec<u16> = tooltip.encode_utf16().collect();
    wide.truncate(127);
    wide.push(0);
    let mut data: NOTIFYICONDATAW = std::mem::zeroed();
    data.hWnd = hwnd;
    data.uID = 1;
    data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    data.uCallbackMessage = WM_TRAYICON;
    data.hIcon = icon;
    for (i, unit) in wide.into_iter().enumerate() {
        data.szTip[i] = unit;
    }
    Shell_NotifyIconW(NIM_ADD, &data);
}

unsafe fn tray_update(hwnd: HWND, icon: HICON, tooltip: &str) {
    let mut wide: Vec<u16> = tooltip.encode_utf16().collect();
    wide.truncate(127);
    wide.push(0);
    let mut data: NOTIFYICONDATAW = std::mem::zeroed();
    data.hWnd = hwnd;
    data.uID = 1;
    data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    data.uCallbackMessage = WM_TRAYICON;
    data.hIcon = icon;
    for (i, unit) in wide.into_iter().enumerate() {
        data.szTip[i] = unit;
    }
    Shell_NotifyIconW(NIM_MODIFY, &data);
}

/// Fire a REAL Windows toast through the notification platform (WinRT).
/// Legacy NIF_INFO balloons do not register the sender, so they never
/// appear in Settings > Notifications; this path does.
fn tray_notify(_hwnd: HWND, title: &str, text: &str, error: bool) {
    use windows::core::HSTRING;
    use windows::Data::Xml::Dom::XmlDocument;
    use windows::UI::Notifications::{
        ToastNotification, ToastNotificationManager, ToastTemplateType,
    };
    let body = if error {
        format!("{text} \u{2014} agotada")
    } else {
        text.to_string()
    };
    let result = (|| -> Result<(), windows::core::Error> {
        let xml = ToastNotificationManager::GetTemplateContent(ToastTemplateType::ToastText02)?;
        let texts = xml.GetElementsByTagName(&HSTRING::from("text"))?;
        let node = texts.Item(0)?;
        node.SetInnerText(&HSTRING::from(format!("{title}\u{00a0}\u{2014}\u{00a0}{body}")))?;
        let toast = ToastNotification::CreateToastNotification(&xml)?;
        ToastNotificationManager::CreateToastNotifierWithId(&HSTRING::from(AUMID))?
            .Show(&toast)?;
        Ok(())
    })();
    if let Err(e) = result {
        eprintln!("ai-quotas-tray: toast failed: {e}");
    }
}

unsafe fn tray_remove(hwnd: HWND) {
    let mut data: NOTIFYICONDATAW = std::mem::zeroed();
    data.hWnd = hwnd;
    data.uID = 1;
    Shell_NotifyIconW(NIM_DELETE, &data);
}

/// Repaint icon + tooltip from the latest shared snapshot, then fire any
/// threshold toasts (at most 3 per round to avoid notification storms).
unsafe fn apply_snapshot(hwnd: HWND) {
    let Some(snapshot) = SHARED.lock().unwrap_or_else(|p| p.into_inner()).take() else {
        return;
    };
    let Snapshot {
        state,
        tooltip,
        sections,
        alerts,
    } = snapshot;
    let new_icon = make_icon(state);
    let old = app().icon;
    app().icon = new_icon;
    app().sections = sections;
    if old != std::mem::zeroed() {
        DestroyIcon(old);
    }
    tray_update(hwnd, new_icon, &tooltip);
    for (text, error) in alerts.iter().take(3) {
        tray_notify(hwnd, "ai-quotas", text, *error);
    }
}

/// CodexBar-style menu: one submenu per provider with label/value rows, then
/// dashboard + exit. `\t` inside an item text right-aligns the rest.
unsafe fn show_menu(hwnd: HWND) -> usize {
    let menu = CreatePopupMenu();
    for section in &app().sections {
        let submenu = CreatePopupMenu();
        for row in &section.rows {
            let text: Vec<u16> = format!("{}\t{}\0", row.label, row.right).encode_utf16().collect();
            // id 0: clicking a row is a no-op, but text stays readable.
            AppendMenuW(submenu, MF_STRING, 0, text.as_ptr());
        }
        if section.rows.is_empty() {
            let text: Vec<u16> = "sin datos\0".encode_utf16().collect();
            AppendMenuW(submenu, MF_GRAYED | MF_STRING, 0, text.as_ptr());
        }
        let title: Vec<u16> = format!("{}\0", section.name).encode_utf16().collect();
        AppendMenuW(menu, MF_POPUP | MF_STRING, submenu as usize, title.as_ptr());
    }
    if !app().sections.is_empty() {
        AppendMenuW(menu, MF_SEPARATOR, 0, std::ptr::null());
    }
    let open: Vec<u16> = "Abrir dashboard\0".encode_utf16().collect();
    AppendMenuW(menu, MF_STRING, ID_DASHBOARD, open.as_ptr());
    let quit: Vec<u16> = "Salir\0".encode_utf16().collect();
    AppendMenuW(menu, MF_STRING, ID_EXIT, quit.as_ptr());

    let mut point = POINT { x: 0, y: 0 };
    GetCursorPos(&mut point);
    SetForegroundWindow(hwnd);
    let chosen = TrackPopupMenu(
        menu,
        TPM_RETURNCMD | TPM_BOTTOMALIGN | TPM_LEFTALIGN,
        point.x,
        point.y,
        0,
        hwnd,
        std::ptr::null(),
    );
    DestroyMenu(menu);
    PostMessageW(hwnd, WM_NULL, 0, 0);
    chosen as usize
}

unsafe fn open_dashboard() {
    let url: Vec<u16> = format!("{DASHBOARD_URL}\0").encode_utf16().collect();
    ShellExecuteW(
        std::ptr::null_mut(),
        windows_sys::w!("open"),
        url.as_ptr(),
        std::ptr::null(),
        std::ptr::null(),
        SW_SHOWNORMAL as i32,
    );
}

unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if msg == WM_TRAYICON {
        match lparam as u32 {
            WM_RBUTTONUP => match show_menu(hwnd) {
                ID_DASHBOARD => open_dashboard(),
                ID_EXIT => {
                    tray_remove(hwnd);
                    DestroyWindow(hwnd);
                }
                _ => {}
            },
            WM_LBUTTONUP => open_dashboard(),
            _ => {}
        }
        return 0;
    }
    if msg == WM_DATA_READY {
        apply_snapshot(hwnd);
        return 0;
    }
    DefWindowProcW(hwnd, msg, wparam, lparam)
}

fn main() {
    unsafe {
        // Must run before ANY notification: registers the process identity.
        let aumid: Vec<u16> = format!("{AUMID}\0").encode_utf16().collect();
        SetCurrentProcessExplicitAppUserModelID(aumid.as_ptr());

        // Hidden helper: `--test-toast` fires one notification and exits.
        if std::env::args().any(|a| a == "--test-toast") {
            let instance = GetModuleHandleW(std::ptr::null());
            let class_name: Vec<u16> = "ai-quotas-tray\0".encode_utf16().collect();
            let mut class: WNDCLASSW = std::mem::zeroed();
            class.lpfnWndProc = Some(wndproc);
            class.hInstance = instance;
            class.lpszClassName = class_name.as_ptr();
            RegisterClassW(&class);
            let hwnd = CreateWindowExW(
                0,
                class_name.as_ptr(),
                windows_sys::w!("ai-quotas-tray"),
                WS_OVERLAPPEDWINDOW,
                CW_USEDEFAULT,
                CW_USEDEFAULT,
                CW_USEDEFAULT,
                CW_USEDEFAULT,
                std::mem::zeroed::<HWND>(),
                std::mem::zeroed::<HMENU>(),
                instance,
                std::ptr::null(),
            );
            tray_notify(hwnd, "ai-quotas", "toast de prueba \u{2705}", false);
            thread::sleep(Duration::from_secs(2));
            return;
        }

        let instance = GetModuleHandleW(std::ptr::null());
        let class_name: Vec<u16> = "ai-quotas-tray\0".encode_utf16().collect();
        let mut class: WNDCLASSW = std::mem::zeroed();
        class.lpfnWndProc = Some(wndproc);
        class.hInstance = instance;
        class.lpszClassName = class_name.as_ptr();
        RegisterClassW(&class);

        let hwnd = CreateWindowExW(
            0,
            class_name.as_ptr(),
            windows_sys::w!("ai-quotas-tray"),
            WS_OVERLAPPEDWINDOW,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            std::mem::zeroed::<HWND>(),
            std::mem::zeroed::<HMENU>(),
            instance,
            std::ptr::null(),
        );
        // Hidden window: the tray icon IS the whole UI.
        ShowWindow(hwnd, SW_HIDE);

        APP = Some(App {
            icon: std::mem::zeroed(),
            sections: Vec::new(),
        });
        // Placeholder until the first poll lands (sub-second on a healthy
        // backend); the worker thread keeps it current from here on.
        let icon = make_icon(State::Gray);
        app().icon = icon;
        tray_add(hwnd, icon, "consultando ai-quotas\u{2026}");

        // Worker thread: poll -> stash snapshot -> poke the UI thread. The
        // UI never touches the network, so a slow or dead backend can delay
        // updates but never freeze the menu.
        {
            let hwnd_addr = hwnd as usize;
            thread::spawn(move || loop {
                let n = POLL_COUNT.fetch_add(1, Ordering::SeqCst);
                // Periodically ask the BACK to force-refresh upstream so the
                // shared data stays fresh for the dashboard too.
                let path = if n % FORCE_EVERY_POLLS as usize == 0 && n > 0 {
                    "/api/quotas?refresh=1"
                } else {
                    "/api/quotas"
                };
                let snapshot = match http_get_json_path(API_HOST_PORT, path) {
                    Ok(payload) => {
                        // /api/status is TTL-cached server-side (10 min), so
                        // this second GET costs nothing extra upstream.
                        let status = http_get_json_path(API_HOST_PORT, "/api/status")
                            .unwrap_or(Value::Null);
                        build_snapshot(&payload, &status)
                    }
                    Err(_) => Snapshot {
                        state: State::Gray,
                        tooltip: "ai-quotas no accesible".to_string(),
                        sections: Vec::new(),
                        alerts: Vec::new(),
                    },
                };
                *SHARED.lock().unwrap_or_else(|p| p.into_inner()) = Some(snapshot);
                PostMessageW(hwnd_addr as HWND, WM_DATA_READY, 0, 0);
                thread::sleep(Duration::from_secs(POLL_SECS));
            });
        }

        let mut msg = std::mem::zeroed::<windows_sys::Win32::UI::WindowsAndMessaging::MSG>();
        while GetMessageW(&mut msg, std::mem::zeroed::<HWND>(), 0, 0) != 0 {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}
