//! Scourge shared types. The verifier produces cryptographically grounded FACTS only;
//! the app (Python) owns challenges, the registry and the registry-match policy.

#![allow(dead_code)]

use std::{collections::BTreeMap, fs, ops::Range};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[path = "jspan.rs"]
pub mod jspan;

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct Endpoint {
    pub method: String,
    pub path_prefix: String,
    /// "body": whole response body disclosed (single-FD endpoints); "object": only the FD object.
    pub disclosure: String,
}

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct BankSchema {
    pub name: String,
    pub endpoints: Vec<Endpoint>,
    pub content_type: String,
    pub select_key: String,
    pub fields: BTreeMap<String, String>,
    pub required: Vec<String>,
    pub active_values: Vec<String>,
    #[serde(default)]
    pub lien_ok_contains: Vec<String>,
    #[serde(default)]
    pub lien_bad_contains: Vec<String>,
    #[serde(default)]
    pub holder_must_contain: Option<String>,
}

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct Trust {
    pub mode: String,
    #[serde(default)]
    pub ca_der: Option<String>,
}

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct Config {
    pub allow_hosts: Vec<String>,
    pub trust: Trust,
    pub banks: BTreeMap<String, BankSchema>,
    pub max_sent: usize,
    pub max_recv: usize,
    pub verifier_listen: String,
    pub app_internal: String,
}

impl Config {
    /// Fail closed: any problem is a startup error.
    pub fn load(path: &str) -> Result<Config, String> {
        let s = fs::read_to_string(path).map_err(|e| format!("cannot read config {path}: {e}"))?;
        let c: Config = serde_json::from_str(&s).map_err(|e| format!("invalid config: {e}"))?;
        if c.allow_hosts.is_empty() {
            return Err("allow_hosts is empty: refusing to start (fail closed)".into());
        }
        for h in &c.allow_hosts {
            if h.is_empty() || h.contains('*') || h.contains('/') || h.contains(':') {
                return Err(format!("allow_hosts entry '{h}' must be an exact hostname"));
            }
            if !c.banks.contains_key(h) {
                return Err(format!("allow-listed host '{h}' has no bank schema"));
            }
        }
        match c.trust.mode.as_str() {
            "mozilla" => {}
            "test_ca" if c.trust.ca_der.is_some() => {}
            m => return Err(format!("invalid trust mode '{m}'")),
        }
        if c.max_sent == 0 || c.max_recv == 0 {
            return Err("max_sent/max_recv must be > 0".into());
        }
        Ok(c)
    }
}

pub fn find(h: &[u8], n: &[u8]) -> Option<usize> {
    h.windows(n.len()).position(|w| w == n)
}

pub fn dechunk(b: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = vec![];
    let mut i = 0;
    loop {
        let le = find(&b[i..], b"\r\n").ok_or("chunk: no size line")? + i;
        let size_s = std::str::from_utf8(&b[i..le]).map_err(|_| "chunk: bad size")?;
        let size = usize::from_str_radix(size_s.split(';').next().unwrap().trim(), 16).map_err(|_| "chunk: bad size")?;
        i = le + 2;
        if size == 0 {
            return Ok(out);
        }
        if i + size + 2 > b.len() || &b[i + size..i + size + 2] != b"\r\n" {
            return Err("chunk: truncated".into());
        }
        out.extend_from_slice(&b[i..i + size]);
        i += size + 2;
    }
}

pub fn status_code(line: &[u8]) -> Option<u16> {
    let s = std::str::from_utf8(line).ok()?;
    let mut it = s.split(' ');
    if !it.next()?.starts_with("HTTP/1.") {
        return None;
    }
    it.next()?.parse().ok()
}

pub struct Revealed {
    pub server_name: String,
    pub sent: Vec<u8>,
    pub sent_ranges: Vec<Range<usize>>,
    pub recv: Vec<u8>,
    pub recv_ranges: Vec<Range<usize>>,
    pub sent_len: usize,
    pub recv_len: usize,
}

/// The challenge as the app reports it when the verifier consumes it.
#[derive(Deserialize, Clone, Debug)]
pub struct ChallengeInfo {
    pub challenge_id: String,
    pub fd_number: String,
    pub bank_host: String,
}

/// Facts the proof establishes. `gate` is "rejected", "insufficient_evidence" or "facts_ok".
/// The app applies the registry policy only when gate == "facts_ok".
pub fn facts(cfg: &Config, ch: &ChallengeInfo, r: &Revealed) -> Value {
    let mut st = serde_json::Map::new();
    let mut labels: Vec<Value> = vec![];
    let finish = |st: serde_json::Map<String, Value>, gate: &str, why: Option<&str>, labels: Vec<Value>, extra: Value| {
        json!({"gate": gate, "reason": why, "stages": st, "byte_map": {
            "sent_len": r.sent_len, "recv_len": r.recv_len,
            "sent_ranges": r.sent_ranges.iter().map(|x| [x.start, x.end]).collect::<Vec<_>>(),
            "recv_ranges": r.recv_ranges.iter().map(|x| [x.start, x.end]).collect::<Vec<_>>(),
            "recv_labels": labels,
            // exactly what AICTE received: the disclosed byte ranges and their text
            "sent_revealed": r.sent_ranges.iter().map(|x| json!([x.start, x.end, String::from_utf8_lossy(&r.sent[x.clone()])])).collect::<Vec<_>>(),
            "recv_revealed": r.recv_ranges.iter().map(|x| json!([x.start, x.end, String::from_utf8_lossy(&r.recv[x.clone()])])).collect::<Vec<_>>()},
            "disclosed": extra})
    };

    let allowed = cfg.allow_hosts.iter().any(|h| h == &r.server_name);
    st.insert("tls_proof".into(), json!("verified"));
    st.insert("server_identity".into(), json!({"host": r.server_name, "allow_listed": allowed, "trust": cfg.trust.mode}));
    if !allowed {
        return finish(st, "rejected", Some("host_not_allow_listed"), labels, Value::Null);
    }
    if r.server_name != ch.bank_host {
        return finish(st, "rejected", Some("host_differs_from_challenge_bank"), labels, Value::Null);
    }
    let schema = &cfg.banks[&r.server_name];

    let Some(first) = r.sent_ranges.iter().find(|x| x.start == 0) else {
        return finish(st, "rejected", Some("request_line_not_disclosed"), labels, Value::Null);
    };
    let shown = String::from_utf8_lossy(&r.sent[first.clone()]).to_string();
    let ep = schema.endpoints.iter().find(|e| shown == format!("{} {}", e.method, e.path_prefix)).cloned();
    st.insert("request_policy".into(), json!({"disclosed_request_prefix": shown, "matches_schema_endpoint": ep.is_some(),
        "other_request_ranges_disclosed": r.sent_ranges.len() - 1}));
    let Some(ep) = ep else { return finish(st, "rejected", Some("request_not_an_approved_endpoint"), labels, Value::Null) };

    let st_range = r.recv_ranges.iter().find(|x| x.start == 0).cloned();
    let status = st_range.as_ref().and_then(|x| status_code(&r.recv[x.clone()]));
    let body_range = if ep.disclosure == "body" {
        r.recv_ranges.iter().find(|x| x.end == r.recv_len && x.start > 0 && r.recv[x.start..].starts_with(b"\r\n")).cloned()
    } else {
        r.recv_ranges.iter().find(|x| x.start > 0 && r.recv[x.start] == b'{').cloned()
    };
    let mut headers = BTreeMap::new();
    for x in &r.recv_ranges {
        if Some(x) == st_range.as_ref() {
            labels.push(json!({"range": [x.start, x.end], "label": "status line"}));
            continue;
        }
        if Some(x) == body_range.as_ref() {
            labels.push(json!({"range": [x.start, x.end], "label": if ep.disclosure == "body" { "response body" } else { "FD record" }}));
            continue;
        }
        let line = String::from_utf8_lossy(&r.recv[x.clone()]).to_string();
        if let Some((k, v)) = line.split_once(':') {
            if !line.contains('\r') && !line.contains('\n') {
                headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
                labels.push(json!({"range": [x.start, x.end], "label": format!("{} header", k.trim().to_ascii_lowercase())}));
                continue;
            }
        }
        labels.push(json!({"range": [x.start, x.end], "label": "other"}));
    }
    let ct_ok = headers.get("content-type").map(|v| v.to_ascii_lowercase().starts_with(&schema.content_type)).unwrap_or(false);
    let http_class = match status {
        None => "status_not_disclosed",
        Some(200) if ct_ok => "usable",
        Some(200) => "unexpected_content_type",
        Some(301 | 302 | 303 | 307 | 308) => "redirect",
        Some(401 | 403) => "auth_failed",
        Some(_) => "http_error",
    };
    st.insert("http".into(), json!({"status": status, "class": http_class, "content_type": headers.get("content-type"),
        "server_date_as_returned": headers.get("date"), "status_line": st_range.map(|x| String::from_utf8_lossy(&r.recv[x]).to_string())}));
    if http_class != "usable" {
        return finish(st, "insufficient_evidence", Some(http_class), labels, Value::Null);
    }

    let Some(br) = body_range else {
        st.insert("schema".into(), json!({"ok": false, "error": "FD record was not disclosed"}));
        return finish(st, "insufficient_evidence", Some("fd_record_not_disclosed"), labels, Value::Null);
    };
    let raw = if ep.disclosure == "body" { r.recv[br.start + 2..br.end].to_vec() } else { r.recv[br.clone()].to_vec() };
    if raw.contains(&0u8) {
        st.insert("schema".into(), json!({"ok": false, "error": "disclosed span contains hidden bytes"}));
        return finish(st, "insufficient_evidence", Some("partial_disclosure"), labels, Value::Null);
    }
    let chunked = headers.get("transfer-encoding").map(|v| v.to_ascii_lowercase().contains("chunked")).unwrap_or(false);
    let raw = if chunked && ep.disclosure == "body" {
        match dechunk(&raw) {
            Ok(b) => b,
            Err(e) => {
                st.insert("schema".into(), json!({"ok": false, "error": e}));
                return finish(st, "insufficient_evidence", Some("bad_chunking"), labels, Value::Null);
            }
        }
    } else {
        raw
    };
    let disclosed_text = String::from_utf8_lossy(&raw).to_string();
    let doc = match jspan::parse(&raw) {
        Ok(d) => d,
        Err(e) => {
            st.insert("schema".into(), json!({"ok": false, "error": format!("response is not valid JSON ({e})")}));
            return finish(st, "insufficient_evidence", Some("malformed_json"), labels, json!({"text": disclosed_text}));
        }
    };
    let mut objs = vec![];
    doc.find_objects(&schema.select_key, &ch.fd_number, &mut objs);
    let total = doc.count_with_key(&schema.select_key);
    st.insert("schema".into(), json!({"ok": true, "disclosure": ep.disclosure, "fd_records_disclosed": total, "matching_fd_records": objs.len(),
        "object_context": if ep.disclosure == "body" { "whole body disclosed: position verified" } else { "record only: position in the response is not verifiable" }}));
    if objs.is_empty() {
        let why = if total > 0 { "fd_number_differs_from_challenge" } else { "no_fd_record" };
        return finish(st, "rejected", Some(why), labels, json!({"text": disclosed_text}));
    }
    if objs.len() > 1 {
        return finish(st, "insufficient_evidence", Some("fd_record_ambiguous"), labels, json!({"text": disclosed_text}));
    }
    let obj = objs[0];
    let mut fields = serde_json::Map::new();
    for (logical, key) in &schema.fields {
        let v = match obj.get_unique(key) {
            Err(_) => json!({"state": "ambiguous_duplicate_key", "key": key}),
            Ok(None) => json!({"state": "not_exposed", "key": key}),
            Ok(Some(n)) => match n.scalar() {
                Some(v) => json!({"state": "proven", "key": key, "value": v}),
                None => json!({"state": "unsupported_type", "key": key}),
            },
        };
        fields.insert(logical.clone(), v);
    }
    let text = String::from_utf8_lossy(&raw[obj.start..obj.end]).to_string();
    let mut out = finish(st, "facts_ok", None, labels, json!({"text": text}));
    out["fields"] = Value::Object(fields);
    out
}
