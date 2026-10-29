//! scourge_mockbank — SYNTHETIC net-banking server for the demo and tests. Not a bank.
//! Usage: scourge_mockbank <port> <cert.der> <key.pkcs8.der> <state.json>
//! The state file (edited by the portal's "Mock bank console") is re-read on every request:
//!   {"sessions": {"<cookie value>": "<customer id>"},
//!    "customers": {"<id>": {"name","pan","customer_id","deposits":[{"fd_number","principal",
//!                   "maturity","holders","lien","behaviour"}]}}}
//! Endpoints (cookie NB_SESSION required):
//!   GET /api/fd/details?fd=<FD number>   single-FD body, no customer data
//!   GET /api/accounts/summary            customer record (with PAN) + all deposits

use std::{collections::HashMap, convert::Infallible, sync::Arc, time::Duration};

use axum::{
    Router,
    body::Body,
    extract::{Query, State},
    http::{HeaderMap, Response, StatusCode, header},
    routing::get,
};
use futures_rustls::{
    TlsAcceptor,
    pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer},
    rustls::ServerConfig,
};
use hyper::{body::Incoming, server::conn::http1};
use hyper_util::rt::TokioIo;
use serde_json::{Value, json};
use tokio::net::TcpListener;
use tokio_util::compat::{FuturesAsyncReadCompatExt, TokioAsyncReadCompatExt};
use tower_service::Service;

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string()
}

/// Render one deposit as the bank's JSON according to its behaviour.
fn render(d: &Value) -> String {
    let b = s(d, "behaviour");
    let fd = if b == "unrelated" { "FD-0000999999".to_string() } else { s(d, "fd_number") };
    let principal = s(d, "principal");
    // optional test fixture: the exact status wording this bank shows (default ACTIVE)
    let mut status = d.get("status_text").and_then(|v| v.as_str()).unwrap_or("ACTIVE").to_string();
    let mut bal = principal.clone();
    let mut mat = s(d, "maturity");
    let mut lien = s(d, "lien");
    let holders = s(d, "holders");
    match b.as_str() {
        "closed" => {
            status = "CLOSED".into();
            bal = "0.00".into();
            lien = "NONE".into();
        }
        "reduced" => {
            let p: f64 = principal.parse().unwrap_or(0.0);
            bal = format!("{:.2}", p * 0.6);
        }
        "maturity_changed" => mat = "2027-07-01".into(),
        "lien_loan" => {
            let p: f64 = principal.parse().unwrap_or(0.0);
            lien = format!("LOAN {:.2} AGAINST DEPOSIT", p * 0.85);
        }
        _ => {}
    }
    let base = |extra_first: &str, skip: &str| {
        let mut parts = vec![format!("\"depositNo\":\"{fd}\"")];
        if !extra_first.is_empty() {
            parts.push(extra_first.to_string());
        }
        if skip != "status" {
            parts.push(format!("\"status\":\"{status}\""));
        }
        parts.push(format!("\"principal\":\"{principal}\""));
        parts.push(format!("\"currentBalance\":\"{bal}\""));
        parts.push("\"rate\":\"7.10\"".into());
        if skip != "maturity" {
            parts.push(format!("\"maturityDate\":\"{mat}\""));
        }
        parts.push(format!("\"holders\":\"{holders}\""));
        if skip != "lien" {
            parts.push(format!("\"lien\":\"{lien}\""));
        }
        parts.push("\"nominee\":\"NOT REGISTERED\"".into());
        format!("{{{}}}", parts.join(","))
    };
    match b.as_str() {
        "no_lien" => base("", "lien"),
        "no_status" => base("", "status"),
        "no_maturity" => base("", "maturity"),
        "duplicate_status" => base("\"status\":\"CLOSED\"", ""),
        "escaped_ws" => format!(
            "{{ \"depositNo\" : \"{fd}\" ,\n  \"status\" :\t\"{status}\", \"principal\": \"{principal}\", \"currentBalance\" : \"{bal}\",\n \"maturityDate\":\"{mat}\", \"holders\": \"{}\", \"lien\": \"{lien}\", \"nominee\": \"A \\\"QUOTED\\\" NAME \\\\ X\" }}",
            holders.replace('"', "\\\"")
        ),
        _ => base("", ""),
    }
}

fn json_resp(body: String, chunked: bool) -> Response<Body> {
    let b = if chunked {
        let parts: Vec<Result<String, Infallible>> = body.as_bytes().chunks(40).map(|c| Ok(String::from_utf8_lossy(c).to_string())).collect();
        Body::from_stream(futures::stream::iter(parts))
    } else {
        Body::from(body)
    };
    Response::builder().header(header::CONTENT_TYPE, "application/json").body(b).unwrap()
}

fn err(code: StatusCode, msg: &str) -> Response<Body> {
    Response::builder().status(code).header(header::CONTENT_TYPE, "application/json").body(Body::from(json!({"error": msg}).to_string())).unwrap()
}

fn customer(state_path: &str, h: &HeaderMap) -> Result<Value, Response<Body>> {
    let st: Value = std::fs::read_to_string(state_path).ok().and_then(|t| serde_json::from_str(&t).ok()).ok_or_else(|| err(StatusCode::SERVICE_UNAVAILABLE, "bank state unavailable"))?;
    let cookie = h.get(header::COOKIE).and_then(|v| v.to_str().ok()).unwrap_or("");
    let sess = cookie.split(';').find_map(|c| c.trim().strip_prefix("NB_SESSION=")).unwrap_or("");
    let cid = st["sessions"].get(sess).and_then(|v| v.as_str()).ok_or_else(|| err(StatusCode::UNAUTHORIZED, "login required"))?;
    Ok(st["customers"][cid].clone())
}

async fn special(b: &str) -> Option<Response<Body>> {
    match b {
        "redirect" => Some(Response::builder().status(StatusCode::TEMPORARY_REDIRECT).header(header::LOCATION, "/login").body(Body::empty()).unwrap()),
        "login" => Some(
            Response::builder()
                .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
                .body(Body::from("<html><title>NetBanking Login</title><form>User ID <input name=u></form></html>"))
                .unwrap(),
        ),
        "slow" => {
            tokio::time::sleep(Duration::from_secs(8)).await;
            None
        }
        _ => None,
    }
}

async fn details(State(sp): State<Arc<String>>, h: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response<Body> {
    let c = match customer(&sp, &h) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let fd = q.get("fd").cloned().unwrap_or_default();
    let deps = c["deposits"].as_array().cloned().unwrap_or_default();
    let Some(d) = deps.iter().find(|d| s(d, "fd_number") == fd) else { return err(StatusCode::NOT_FOUND, "no such deposit for this customer") };
    let b = s(d, "behaviour");
    if let Some(r) = special(&b).await {
        return r;
    }
    if b == "malformed" {
        return json_resp(format!(r#"{{"depositNo":"{fd}","status":"ACTIVE","currentBalance":"1000000.0"#), false);
    }
    if b == "two_fdrs" {
        let all: Vec<String> = deps.iter().map(render).collect();
        return json_resp(format!(r#"{{"deposits":[{}]}}"#, all.join(",")), false);
    }
    json_resp(render(d), b == "chunked")
}

async fn summary(State(sp): State<Arc<String>>, h: HeaderMap) -> Response<Body> {
    let c = match customer(&sp, &h) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let deps: Vec<String> = c["deposits"].as_array().cloned().unwrap_or_default().iter().map(render).collect();
    json_resp(
        format!(
            r#"{{"customer":{{"name":"{}","pan":"{}","customerId":"{}"}},"deposits":[{}],"savings":[{{"accountNo":"SB-SYN-1111","balance":"431200.55"}}]}}"#,
            s(&c, "name"),
            s(&c, "pan"),
            s(&c, "customer_id"),
            deps.join(",")
        ),
        false,
    )
}

#[tokio::main]
async fn main() {
    let a: Vec<String> = std::env::args().collect();
    if a.len() != 5 {
        eprintln!("usage: scourge_mockbank <port> <cert.der> <key.der> <state.json>");
        std::process::exit(2);
    }
    let port: u16 = a[1].parse().expect("port");
    let cert = CertificateDer::from(std::fs::read(&a[2]).expect("cert"));
    let key = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(std::fs::read(&a[3]).expect("key")));
    let cfg = ServerConfig::builder().with_no_client_auth().with_single_cert(vec![cert], key).expect("tls config");
    let acceptor = TlsAcceptor::from(Arc::new(cfg));
    let app = Router::new()
        .route("/api/fd/details", get(details))
        .route("/api/accounts/summary", get(summary))
        .with_state(Arc::new(a[4].clone()));
    let l = TcpListener::bind(("127.0.0.1", port)).await.expect("bind");
    eprintln!("{}", json!({"mockbank": "SYNTHETIC", "port": port}));
    loop {
        let Ok((sck, _)) = l.accept().await else { continue };
        let acceptor = acceptor.clone();
        let app = app.clone();
        tokio::spawn(async move {
            let tls = match acceptor.accept(sck.compat()).await {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("{}", json!({"tls_accept_error": e.to_string()}));
                    return;
                }
            };
            let io = TokioIo::new(tls.compat());
            let svc = hyper::service::service_fn(move |req: hyper::Request<Incoming>| app.clone().call(req));
            let _ = http1::Builder::new().keep_alive(false).serve_connection(io, svc).await;
        });
    }
}
