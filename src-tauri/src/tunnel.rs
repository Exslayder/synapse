// VLESS/Reality link parsing, DPAPI-protected secret storage and Xray config
// generation. Secrets are never logged.

use serde_json::json;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use url::Url;

#[derive(Clone, Debug)]
pub struct VlessConfig {
    pub uuid: String,
    pub host: String,
    pub port: u16,
    pub sni: String,
    pub public_key: String,
    pub short_id: String,
    pub flow: String,
    pub fingerprint: String,
    pub spider_x: String,
}

fn is_hex(value: &str) -> bool {
    !value.is_empty() && value.len() % 2 == 0 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

fn is_uuid(value: &str) -> bool {
    if value.len() != 36 {
        return false;
    }
    for (index, byte) in value.bytes().enumerate() {
        let hyphen = matches!(index, 8 | 13 | 18 | 23);
        if hyphen {
            if byte != b'-' {
                return false;
            }
        } else if !byte.is_ascii_hexdigit() {
            return false;
        }
    }
    true
}

fn is_safe_host(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 253
        && !value
            .bytes()
            .any(|b| b <= b' ' || b == b'/' || b == b'\\' || b == b'\0')
}

pub fn parse_link(link: &str) -> Result<VlessConfig, String> {
    let link = link.trim();
    if link.is_empty() {
        return Err("Введите VPN-ссылку".into());
    }
    let url = Url::parse(link).map_err(|_| "Неверная VPN-ссылка".to_string())?;
    if url.scheme() != "vless" {
        return Err("Поддерживается только ссылка vless://".into());
    }
    let uuid = url.username().to_string();
    if !is_uuid(&uuid) {
        return Err("В ссылке нет корректного UUID".into());
    }
    let host = url
        .host_str()
        .ok_or_else(|| "В ссылке нет адреса сервера".to_string())?
        .to_string();
    if !is_safe_host(&host) {
        return Err("Некорректный адрес сервера".into());
    }
    let port = url.port().unwrap_or(443);
    if port == 0 {
        return Err("Некорректный порт VPN".into());
    }

    let params: HashMap<String, String> = url.query_pairs().into_owned().collect();
    let param = |name: &str| params.get(name).cloned().unwrap_or_default();

    if param("security") != "reality" {
        return Err("Поддерживается только VLESS Reality".into());
    }
    if param("type") != "tcp" {
        return Err("Поддерживается только транспорт TCP".into());
    }
    let sni = param("sni");
    if !is_safe_host(&sni) {
        return Err("В ссылке нет корректного SNI".into());
    }
    let public_key = param("pbk");
    if public_key.len() < 32
        || public_key.len() > 64
        || !public_key
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'=')
    {
        return Err("В ссылке нет корректного публичного ключа".into());
    }
    let short_id = param("sid");
    if !short_id.is_empty() && (!is_hex(&short_id) || short_id.len() > 16) {
        return Err("В ссылке некорректный shortId".into());
    }
    let flow = param("flow");
    if !flow.is_empty() && flow != "xtls-rprx-vision" {
        return Err("Поддерживается только flow xtls-rprx-vision".into());
    }
    let fingerprint = match param("fp").as_str() {
        "" => "chrome".to_string(),
        value @ ("chrome" | "firefox" | "safari" | "ios" | "android" | "edge" | "random" | "randomized") => {
            value.to_string()
        }
        _ => return Err("Неизвестный fingerprint в ссылке".into()),
    };
    let mut spider_x = param("spx");
    if spider_x.is_empty() {
        spider_x = "/".to_string();
    }
    if !spider_x.starts_with('/') || spider_x.len() > 256 {
        return Err("Некорректный spiderX в ссылке".into());
    }

    Ok(VlessConfig {
        uuid,
        host,
        port,
        sni,
        public_key,
        short_id,
        flow,
        fingerprint,
        spider_x,
    })
}

pub fn xray_config(config: &VlessConfig, socks_port: u16) -> serde_json::Value {
    json!({
        "log": { "loglevel": "warning", "access": "none" },
        "inbounds": [{
            "tag": "socks-in",
            "listen": "127.0.0.1",
            "port": socks_port,
            "protocol": "socks",
            "settings": { "auth": "noauth", "udp": false }
        }],
        "outbounds": [{
            "tag": "proxy",
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": config.host,
                    "port": config.port,
                    "users": [{
                        "id": config.uuid,
                        "encryption": "none",
                        "flow": config.flow
                    }]
                }]
            },
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "serverName": config.sni,
                    "fingerprint": config.fingerprint,
                    "publicKey": config.public_key,
                    "shortId": config.short_id,
                    "spiderX": config.spider_x
                }
            }
        }, {
            "tag": "direct",
            "protocol": "freedom"
        }]
    })
}

fn secret_path(base: &Path) -> PathBuf {
    base.join("tunnel.dat")
}

pub fn secret_exists(base: &Path) -> bool {
    secret_path(base).is_file()
}

pub fn save_secret(base: &Path, link: &str) -> Result<(), String> {
    parse_link(link)?;
    let encrypted = protect_secret(link)?;
    let mut data = Vec::with_capacity(4 + encrypted.len());
    data.extend_from_slice(b"SYN1");
    data.extend_from_slice(&encrypted);
    fs::create_dir_all(base).map_err(|e| e.to_string())?;
    let path = secret_path(base);
    let temp = base.join("tunnel.dat.tmp");
    fs::write(&temp, data).map_err(|e| e.to_string())?;
    fs::rename(&temp, &path).map_err(|e| e.to_string())?;
    Ok(())
}

pub fn load_secret(base: &Path) -> Result<Option<String>, String> {
    let path = secret_path(base);
    let data = match fs::read(&path) {
        Ok(data) => data,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(err) => return Err(err.to_string()),
    };
    if data.len() < 5 || &data[..4] != b"SYN1" {
        return Err("Файл VPN-секрета повреждён".into());
    }
    let link = unprotect_secret(&data[4..])?;
    parse_link(&link)?;
    Ok(Some(link))
}

pub fn clear_secret(base: &Path) -> Result<(), String> {
    let path = secret_path(base);
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(err.to_string()),
    }
}

#[cfg(windows)]
fn protect_secret(text: &str) -> Result<Vec<u8>, String> {
    use windows::Win32::Foundation::{LocalFree, HLOCAL};
    use windows::Win32::Security::Cryptography::{
        CryptProtectData, CRYPT_INTEGER_BLOB, CRYPTPROTECT_UI_FORBIDDEN,
    };

    let mut input_bytes = text.as_bytes().to_vec();
    let input = CRYPT_INTEGER_BLOB {
        cbData: input_bytes.len() as u32,
        pbData: input_bytes.as_mut_ptr(),
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptProtectData(
            &input,
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
        .map_err(|err| format!("DPAPI: {err}"))?;
    }
    let encrypted = unsafe {
        std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec()
    };
    unsafe {
        let _ = LocalFree(Some(HLOCAL(output.pbData as *mut _)));
    }
    Ok(encrypted)
}

#[cfg(windows)]
fn unprotect_secret(data: &[u8]) -> Result<String, String> {
    use windows::Win32::Foundation::{LocalFree, HLOCAL};
    use windows::Win32::Security::Cryptography::{
        CryptUnprotectData, CRYPT_INTEGER_BLOB, CRYPTPROTECT_UI_FORBIDDEN,
    };

    let mut input_bytes = data.to_vec();
    let input = CRYPT_INTEGER_BLOB {
        cbData: input_bytes.len() as u32,
        pbData: input_bytes.as_mut_ptr(),
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptUnprotectData(
            &input,
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
        .map_err(|err| format!("DPAPI: {err}"))?;
    }
    let decrypted = unsafe {
        std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec()
    };
    unsafe {
        let _ = LocalFree(Some(HLOCAL(output.pbData as *mut _)));
    }
    String::from_utf8(decrypted).map_err(|_| "VPN-секрет повреждён".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    const LINK: &str = "vless://11111111-1111-1111-1111-111111111111@203.0.113.10:443?type=tcp&security=reality&pbk=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&fp=chrome&sni=example.com&sid=0123456789abcdef&spx=%2F&flow=xtls-rprx-vision#SynapseVPN";

    #[test]
    fn parses_vless_link() {
        let parsed = parse_link(LINK).unwrap();
        assert_eq!(parsed.host, "203.0.113.10");
        assert_eq!(parsed.port, 443);
        assert_eq!(parsed.spider_x, "/");
        assert_eq!(parsed.flow, "xtls-rprx-vision");
    }

    #[test]
    fn rejects_non_vless_and_wrong_security() {
        assert!(parse_link("https://example.com").is_err());
        assert!(parse_link("vless://bad@example.com:443?security=tls").is_err());
        assert!(parse_link("vless://11111111-1111-1111-1111-111111111111@example.com:443?security=reality&type=tcp&sni=x&pbk=short").is_err());
    }

    #[test]
    fn builds_xray_json_without_touching_link() {
        let parsed = parse_link(LINK).unwrap();
        let value = xray_config(&parsed, 10808);
        assert_eq!(value["inbounds"][0]["port"], 10808);
        assert_eq!(value["outbounds"][0]["protocol"], "vless");
        assert_eq!(value["outbounds"][0]["streamSettings"]["security"], "reality");
    }

    #[cfg(windows)]
    #[test]
    fn dpapi_roundtrip() {
        let encrypted = protect_secret("secret-value").unwrap();
        assert_eq!(unprotect_secret(&encrypted).unwrap(), "secret-value");
    }
}
