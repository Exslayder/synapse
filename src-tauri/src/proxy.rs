// Local HTTP/HTTPS proxy. The browser points at it, and every hostname is
// resolved through the DoH resolver before the tunnel is opened.

use std::io::{Read, Write};
use std::net::{IpAddr, Shutdown, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::doh::DohResolver;

pub type LogFn = Arc<dyn Fn(String) + Send + Sync>;

pub fn start(
    port: u16,
    resolver: Arc<DohResolver>,
    allowed_ports: Vec<u16>,
    direct_hosts: Arc<Mutex<Vec<String>>>,
    log: LogFn,
) -> std::io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", port))?;
    let actual = listener.local_addr()?.port();
    thread::spawn(move || {
        for incoming in listener.incoming() {
            match incoming {
                Ok(stream) => {
                    let resolver = resolver.clone();
                    let log = log.clone();
                    let allowed = allowed_ports.clone();
                    let direct = direct_hosts.clone();
                    thread::spawn(move || {
                        if let Err(err) = handle(stream, resolver, allowed, direct, log.clone()) {
                            log(format!("прокси: {err}"));
                        }
                    });
                }
                Err(err) => log(format!("прокси: {err}")),
            }
        }
    });
    Ok(actual)
}

fn is_direct(list: &Arc<Mutex<Vec<String>>>, host: &str) -> bool {
    let h = host.to_ascii_lowercase();
    list.lock()
        .map(|items| {
            items
                .iter()
                .any(|d| h == *d || h.ends_with(&format!(".{d}")))
        })
        .unwrap_or(false)
}

fn system_ips(host: &str) -> Result<Vec<IpAddr>, String> {
    (host, 0u16)
        .to_socket_addrs()
        .map(|iter| iter.map(|addr| addr.ip()).collect())
        .map_err(|e| format!("{host}: {e}"))
}

fn resolve_host(
    resolver: &Arc<DohResolver>,
    direct: &Arc<Mutex<Vec<String>>>,
    host: &str,
) -> Result<Vec<IpAddr>, String> {
    if is_direct(direct, host) {
        system_ips(host)
    } else {
        resolver.resolve(host)
    }
}

fn handle(
    mut client: TcpStream,
    resolver: Arc<DohResolver>,
    allowed: Vec<u16>,
    direct_hosts: Arc<Mutex<Vec<String>>>,
    log: LogFn,
) -> Result<(), String> {
    client.set_read_timeout(Some(Duration::from_secs(120))).ok();
    client.set_write_timeout(Some(Duration::from_secs(30))).ok();

    let mut buf = Vec::with_capacity(4096);
    let mut chunk = [0u8; 4096];
    let head_end = loop {
        if let Some(idx) = find_head_end(&buf) {
            break idx;
        }
        if buf.len() > 65536 {
            return Err("слишком большой заголовок".into());
        }
        let read = client
            .read(&mut chunk)
            .map_err(|e| format!("чтение запроса: {e}"))?;
        if read == 0 {
            return Ok(());
        }
        buf.extend_from_slice(&chunk[..read]);
    };

    let head = String::from_utf8_lossy(&buf[..head_end]).to_string();
    let leftover = buf[head_end..].to_vec();
    let mut lines = head.split("\r\n");
    let request_line = lines.next().unwrap_or_default();
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or_default().to_string();
    let target = parts.next().unwrap_or_default().to_string();

    if method.eq_ignore_ascii_case("CONNECT") {
        let (host, port) = split_authority(&target, 443)?;
        if !allowed.is_empty() && !allowed.contains(&port) {
            respond_error(&mut client, 403, "Forbidden");
            return Err(format!("порт {port} запрещён"));
        }
        let ips = resolve_host(&resolver, &direct_hosts, &host)?;
        let upstream = connect_any(&ips, port, &host)?;
        log(format!(
            "connect  {host}:{port}  DNS: {}",
            ips.first().map(|ip| ip.to_string()).unwrap_or_default()
        ));
        client
            .write_all(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: Synapse\r\n\r\n")
            .map_err(|e| e.to_string())?;
        if !leftover.is_empty() {
            let mut up = upstream
                .try_clone()
                .map_err(|e| format!("клонирование: {e}"))?;
            up.write_all(&leftover).ok();
        }
        relay(client, upstream);
        return Ok(());
    }

    // Plain HTTP through the proxy: absolute URI -> origin form.
    let (host, port, path) = parse_absolute_url(&target)?;
    let ips = resolve_host(&resolver, &direct_hosts, &host)?;
    log(format!(
        "http     {host}:{port}  DNS: {}",
        ips.first().map(|ip| ip.to_string()).unwrap_or_default()
    ));
    let mut upstream = connect_any(&ips, port, &host)?;

    let mut rewritten = format!("{method} {path} HTTP/1.1\r\n");
    for line in lines {
        if line.is_empty() {
            continue;
        }
        let lower = line.to_ascii_lowercase();
        if lower.starts_with("proxy-connection:") || lower.starts_with("proxy-authorization:") {
            continue;
        }
        rewritten.push_str(line);
        rewritten.push_str("\r\n");
    }
    rewritten.push_str("\r\n");
    upstream
        .write_all(rewritten.as_bytes())
        .and_then(|_| upstream.write_all(&leftover))
        .map_err(|e| format!("запись запроса: {e}"))?;

    relay(client, upstream);
    Ok(())
}

fn relay(mut client: TcpStream, mut upstream: TcpStream) {
    let (client_read, upstream_write) = match (client.try_clone(), upstream.try_clone()) {
        (Ok(c), Ok(u)) => (c, u),
        _ => return,
    };
    let forward = thread::spawn(move || {
        let mut src = client_read;
        let mut dst = upstream_write;
        let _ = std::io::copy(&mut src, &mut dst);
        let _ = dst.shutdown(Shutdown::Write);
    });
    let _ = std::io::copy(&mut upstream, &mut client);
    let _ = client.shutdown(Shutdown::Write);
    let _ = forward.join();
}

fn connect_any(ips: &[IpAddr], port: u16, host: &str) -> Result<TcpStream, String> {
    let mut last = String::from("нет адресов");
    for ip in ips {
        let addr = SocketAddr::new(*ip, port);
        match TcpStream::connect_timeout(&addr, Duration::from_secs(10)) {
            Ok(stream) => {
                stream.set_read_timeout(Some(Duration::from_secs(120))).ok();
                stream.set_write_timeout(Some(Duration::from_secs(30))).ok();
                return Ok(stream);
            }
            Err(err) => last = format!("{host}: {err}"),
        }
    }

    // The DoH answer may be unreachable or filtered (some resolvers return a
    // single wildcard address). Fall back to the system resolver before failing.
    if let Ok(iter) = (host, port).to_socket_addrs() {
        for addr in iter {
            match TcpStream::connect_timeout(&addr, Duration::from_secs(10)) {
                Ok(stream) => {
                    stream.set_read_timeout(Some(Duration::from_secs(120))).ok();
                    stream.set_write_timeout(Some(Duration::from_secs(30))).ok();
                    return Ok(stream);
                }
                Err(err) => last = format!("{host}: {err}"),
            }
        }
    }
    Err(last)
}

fn find_head_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map(|i| i + 4)
        .or_else(|| buf.windows(2).position(|w| w == b"\n\n").map(|i| i + 2))
}

fn split_authority(value: &str, default_port: u16) -> Result<(String, u16), String> {
    if value.starts_with('[') {
        let end = value.find(']').ok_or("неверный хост")?;
        let host = value[1..end].to_string();
        let port = value[end + 1..]
            .strip_prefix(':')
            .and_then(|p| p.parse::<u16>().ok())
            .unwrap_or(default_port);
        return Ok((host, port));
    }
    match value.rsplit_once(':') {
        Some((host, port)) if port.chars().all(|c| c.is_ascii_digit()) => Ok((
            host.to_string(),
            port.parse::<u16>().map_err(|_| "неверный порт".to_string())?,
        )),
        _ => Ok((value.to_string(), default_port)),
    }
}

fn parse_absolute_url(target: &str) -> Result<(String, u16, String), String> {
    let rest = target
        .strip_prefix("http://")
        .ok_or_else(|| "ожидался http:// адрес".to_string())?;
    let (authority, path) = match rest.find('/') {
        Some(idx) => (&rest[..idx], &rest[idx..]),
        None => (rest, "/"),
    };
    let (host, port) = split_authority(authority, 80)?;
    Ok((host, port, path.to_string()))
}

fn respond_error(client: &mut TcpStream, code: u16, reason: &str) {
    let body = format!("Synapse: {reason}");
    let response = format!(
        "HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = client.write_all(response.as_bytes());
}
