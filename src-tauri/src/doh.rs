// DNS-over-HTTPS resolver (RFC 8484) over HTTP/2 with a small DNS
// wire-format implementation: builds A queries, parses answers, caches them.

use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr, ToSocketAddrs};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[derive(Clone, Debug)]
pub struct Endpoint {
    pub host: String,
    pub port: u16,
    pub path: String,
}

impl Endpoint {
    pub fn parse(url: &str) -> Result<Endpoint, String> {
        let trimmed = url.trim();
        let rest = trimmed
            .strip_prefix("https://")
            .ok_or_else(|| "DoH-адрес должен начинаться с https://".to_string())?;
        let (authority, path) = match rest.find('/') {
            Some(idx) => (&rest[..idx], &rest[idx..]),
            None => (rest, "/"),
        };
        let (host, port) = match authority.rsplit_once(':') {
            Some((h, p)) if p.chars().all(|c| c.is_ascii_digit()) => (
                h.to_string(),
                p.parse::<u16>()
                    .map_err(|_| "неверный порт DoH".to_string())?,
            ),
            _ => (authority.to_string(), 443),
        };
        if host.is_empty() {
            return Err("пустой хост DoH".into());
        }
        Ok(Endpoint {
            host,
            port,
            path: path.to_string(),
        })
    }

    fn url(&self) -> String {
        if self.port == 443 {
            format!("https://{}{}", self.host, self.path)
        } else {
            format!("https://{}:{}{}", self.host, self.port, self.path)
        }
    }
}

#[derive(Clone)]
struct Settings {
    endpoint: Option<Endpoint>,
    bootstrap: Vec<IpAddr>,
    timeout: Duration,
    fallback: bool,
}

struct CacheEntry {
    expires: Instant,
    ips: Vec<IpAddr>,
}

pub struct DohResolver {
    settings: Mutex<Settings>,
    cache: Mutex<HashMap<String, CacheEntry>>,
}

impl DohResolver {
    pub fn new(url: &str, bootstrap: Vec<IpAddr>, timeout_secs: f64, fallback: bool) -> DohResolver {
        DohResolver {
            settings: Mutex::new(Settings {
                endpoint: parse_endpoint(url),
                bootstrap,
                timeout: Duration::from_secs_f64(if timeout_secs > 0.1 {
                    timeout_secs
                } else {
                    8.0
                }),
                fallback,
            }),
            cache: Mutex::new(HashMap::new()),
        }
    }

    pub fn configure(&self, url: &str, bootstrap: Vec<IpAddr>, timeout_secs: f64, fallback: bool) {
        let mut settings = self.settings.lock().unwrap();
        settings.endpoint = parse_endpoint(url);
        settings.bootstrap = bootstrap;
        settings.timeout = Duration::from_secs_f64(if timeout_secs > 0.1 {
            timeout_secs
        } else {
            8.0
        });
        settings.fallback = fallback;
        drop(settings);
        self.cache.lock().unwrap().clear();
    }

    pub fn endpoint_url(&self) -> Option<String> {
        self.settings
            .lock()
            .unwrap()
            .endpoint
            .as_ref()
            .map(Endpoint::url)
    }

    fn snapshot(&self) -> Settings {
        self.settings.lock().unwrap().clone()
    }

    pub fn resolve(&self, host: &str) -> Result<Vec<IpAddr>, String> {
        if let Ok(ip) = host.parse::<IpAddr>() {
            return Ok(vec![ip]);
        }
        let key = host.trim_end_matches('.').to_ascii_lowercase();
        if let Some(entry) = self.cache.lock().unwrap().get(&key) {
            if entry.expires > Instant::now() && !entry.ips.is_empty() {
                return Ok(entry.ips.clone());
            }
        }

        let settings = self.snapshot();
        let mut doh_error: Option<String> = None;

        if let Some(endpoint) = settings.endpoint.clone() {
            match self.query_doh(&endpoint, &key, &settings) {
                Ok((ips, ttl)) => {
                    if !ips.is_empty() {
                        let ttl = ttl.clamp(30, 3600);
                        self.cache.lock().unwrap().insert(
                            key,
                            CacheEntry {
                                expires: Instant::now() + Duration::from_secs(ttl),
                                ips: ips.clone(),
                            },
                        );
                        return Ok(ips);
                    }
                    doh_error = Some("пустой ответ DoH".into());
                }
                Err(err) => doh_error = Some(err),
            }
        }

        if settings.fallback || settings.endpoint.is_none() {
            if let Ok(iter) = (host, 0u16).to_socket_addrs() {
                let ips: Vec<IpAddr> = iter.map(|s| s.ip()).collect();
                if !ips.is_empty() {
                    self.cache.lock().unwrap().insert(
                        key,
                        CacheEntry {
                            expires: Instant::now() + Duration::from_secs(60),
                            ips: ips.clone(),
                        },
                    );
                    return Ok(ips);
                }
            }
        }

        Err(match doh_error {
            Some(err) => format!("не удалось разрешить {host}: {err}"),
            None => format!("не удалось разрешить {host}"),
        })
    }

    fn query_doh(
        &self,
        endpoint: &Endpoint,
        host: &str,
        settings: &Settings,
    ) -> Result<(Vec<IpAddr>, u64), String> {
        let (packet, id) = build_query(host);

        let mut builder = reqwest::blocking::Client::builder()
            .timeout(settings.timeout)
            .use_rustls_tls();
        if let Some(ip) = settings.bootstrap.first() {
            builder = builder.resolve(&endpoint.host, SocketAddr::new(*ip, endpoint.port));
        }
        let client = builder.build().map_err(|e| format!("DoH client: {e}"))?;

        let response = client
            .post(endpoint.url())
            .header("content-type", "application/dns-message")
            .header("accept", "application/dns-message")
            .body(packet)
            .send()
            .map_err(|e| format!("DoH запрос: {e}"))?;

        if !response.status().is_success() {
            return Err(format!("DoH HTTP {}", response.status().as_u16()));
        }
        let body = response
            .bytes()
            .map_err(|e| format!("DoH чтение: {e}"))?;
        parse_response(&body, id)
    }
}

fn parse_endpoint(url: &str) -> Option<Endpoint> {
    if url.trim().is_empty() {
        None
    } else {
        Endpoint::parse(url).ok()
    }
}

fn build_query(host: &str) -> (Vec<u8>, u16) {
    let id = (std::process::id() as u16) ^ 0x5A5A ^ (Instant::now().elapsed().as_nanos() as u16);
    let mut buf = Vec::with_capacity(64);
    buf.extend_from_slice(&id.to_be_bytes());
    buf.extend_from_slice(&[0x01, 0x00]);
    buf.extend_from_slice(&[0x00, 0x01]);
    buf.extend_from_slice(&[0x00, 0x00]);
    buf.extend_from_slice(&[0x00, 0x00]);
    buf.extend_from_slice(&[0x00, 0x00]);

    for label in host.trim_end_matches('.').split('.') {
        let bytes = label.as_bytes();
        let len = bytes.len().min(63);
        buf.push(len as u8);
        buf.extend_from_slice(&bytes[..len]);
    }
    buf.push(0);
    buf.extend_from_slice(&[0x00, 0x01]);
    buf.extend_from_slice(&[0x00, 0x01]);
    (buf, id)
}

fn skip_name(buf: &[u8], mut pos: usize) -> Option<usize> {
    loop {
        let len = *buf.get(pos)? as usize;
        if len == 0 {
            return Some(pos + 1);
        }
        if len & 0xC0 == 0xC0 {
            return Some(pos + 2);
        }
        pos += 1 + len;
        if pos > buf.len() {
            return None;
        }
    }
}

fn parse_response(buf: &[u8], expected_id: u16) -> Result<(Vec<IpAddr>, u64), String> {
    if buf.len() < 12 {
        return Err("DNS: короткий ответ".into());
    }
    let id = u16::from_be_bytes([buf[0], buf[1]]);
    if id != expected_id {
        return Err("DNS: чужой id".into());
    }
    let qd = u16::from_be_bytes([buf[4], buf[5]]) as usize;
    let an = u16::from_be_bytes([buf[6], buf[7]]) as usize;

    let mut pos = 12usize;
    for _ in 0..qd {
        pos = skip_name(buf, pos).ok_or("DNS: вопрос")?;
        pos += 4;
    }

    let mut ips = Vec::new();
    let mut ttl_min = u64::MAX;
    for _ in 0..an {
        pos = skip_name(buf, pos).ok_or("DNS: имя ответа")?;
        if pos + 10 > buf.len() {
            return Err("DNS: обрезанный ответ".into());
        }
        let rtype = u16::from_be_bytes([buf[pos], buf[pos + 1]]);
        let ttl = u32::from_be_bytes([buf[pos + 4], buf[pos + 5], buf[pos + 6], buf[pos + 7]]);
        let rdlen = u16::from_be_bytes([buf[pos + 8], buf[pos + 9]]) as usize;
        pos += 10;
        if pos + rdlen > buf.len() {
            return Err("DNS: обрезанные данные".into());
        }
        if rtype == 1 && rdlen == 4 {
            ips.push(IpAddr::from([
                buf[pos],
                buf[pos + 1],
                buf[pos + 2],
                buf[pos + 3],
            ]));
            ttl_min = ttl_min.min(ttl as u64);
        }
        pos += rdlen;
    }

    Ok((ips, if ttl_min == u64::MAX { 60 } else { ttl_min }))
}
