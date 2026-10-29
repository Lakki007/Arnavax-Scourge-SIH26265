//! scourge_prove — institution-side prover.
//! Emits one JSON line per real execution stage on stdout (used by the portal for live progress).
//! Replays ONE request inside an MPC-TLS session with AICTE's verifier and discloses only:
//!   request: "METHOD <approved path prefix>" (query string and all headers hidden)
//!   response: status line, Content-Type, Date, Transfer-Encoding (if any), and either the whole
//!   body (single-FD endpoints) or only the selected FD object.
//! Never prints header values, cookies, tokens or body values.

#[path = "common.rs"]
mod common;

use std::future::IntoFuture;

use anyhow::{Context as _, Result, bail};
use clap::Parser;
use common::*;
use http_body_util::{BodyExt, Full};
use hyper::{Request, Uri, body::Bytes};
use hyper_util::rt::TokioIo;
use serde_json::json;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
};
use tokio_util::compat::{FuturesAsyncReadCompatExt, TokioAsyncReadCompatExt};

use tlsn::{
    Session,
    config::{prove::ProveConfig, prover::ProverConfig, tls::TlsClientConfig, tls_commit::mpc::MpcTlsConfig},
    connection::ServerName,
    webpki::{CertificateDer, RootCertStore},
};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    config: String,
    #[arg(long, default_value = "127.0.0.1:7047")]
    verifier: String,
    #[arg(long)]
    challenge_id: String,
    #[arg(long)]
    institution: String,
    #[arg(long)]
    token: String,
    #[arg(long)]
    fd_number: String,
    #[arg(long)]
    url: String,
    #[arg(long, default_value = "GET")]
    method: String,
    #[arg(long)]
    headers_file: Option<String>,
    #[arg(long)]
    body_file: Option<String>,
    #[arg(long, default_value_t = 4096)]
    max_sent: usize,
    #[arg(long, default_value_t = 65536)]
    max_recv: usize,
    /// TEST ONLY: connect to this address instead of resolving the host.
    #[arg(long)]
    connect_addr: Option<String>,
    /// TEST ONLY: extra CA (DER) the prover trusts (simulates a dishonest prover).
    #[arg(long)]
    extra_ca_der: Vec<String>,
    /// Institution-side only: save the full bank RESPONSE (never the request, which holds cookies)
    /// to this local file so the institution can see what it received. Never sent to the verifier.
    #[arg(long)]
    save_response: Option<String>,
    /// TEST ONLY: withhold this JSON key's bytes inside the disclosed object.
    #[arg(long)]
    malicious_hide: Option<String>,
}

static T0: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
fn out(stage: &str, v: serde_json::Value) {
    let ms = T0.get_or_init(std::time::Instant::now).elapsed().as_millis();
    println!("{}", json!({"stage": stage, "detail": v, "t_ms": ms}));
}

#[tokio::main]
async fn main() {
    let a = Args::parse();
    out("started", json!({"challenge_id": a.challenge_id}));
    match run(&a).await {
        Ok(()) => {
            out("submitted", json!("proof submitted; the verifier now evaluates it"));
        }
        Err(e) => {
            out("aborted", json!(format!("{e:#}")));
            std::process::exit(3);
        }
    }
}

async fn connect_bank(host: &str, port: u16, test_addr: &Option<String>) -> Result<TcpStream> {
    if let Some(a) = test_addr {
        let s = TcpStream::connect(a).await?;
        s.set_nodelay(true)?;
        return Ok(s);
    }
    if let Ok(proxy) = std::env::var("HTTPS_PROXY").or_else(|_| std::env::var("https_proxy")) {
        let pu: Uri = proxy.parse()?;
        let mut s = TcpStream::connect((pu.host().unwrap(), pu.port_u16().unwrap_or(80))).await?;
        s.write_all(format!("CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n").as_bytes()).await?;
        let mut head = vec![];
        let mut b = [0u8; 1];
        while !head.ends_with(b"\r\n\r\n") && head.len() < 4096 {
            s.read_exact(&mut b).await?;
            head.push(b[0]);
        }
        if !String::from_utf8_lossy(&head).starts_with("HTTP/1.1 200") && !String::from_utf8_lossy(&head).starts_with("HTTP/1.0 200") {
            bail!("proxy refused CONNECT");
        }
        s.set_nodelay(true)?;
        return Ok(s);
    }
    let s = TcpStream::connect((host, port)).await?;
    s.set_nodelay(true)?;
    Ok(s)
}

async fn run(a: &Args) -> Result<()> {
    let cfg_text = std::fs::read_to_string(&a.config)?;
    let cfg: Config = serde_json::from_str(&cfg_text)?; // prover only uses the public bank schemas
    let uri: Uri = a.url.parse()?;
    if uri.scheme_str() != Some("https") {
        bail!("only https:// URLs are accepted");
    }
    let host = uri.host().context("no host")?.to_string();
    let schema = cfg.banks.get(&host).with_context(|| format!("host {host} has no approved bank schema"))?;
    let path = uri.path().to_string();
    let ep = schema
        .endpoints
        .iter()
        .find(|e| e.method == a.method && path.starts_with(&e.path_prefix))
        .with_context(|| format!("{} {} is not an approved endpoint for {host}", a.method, path))?
        .clone();
    if a.body_file.is_some() && a.method == "GET" {
        bail!("--body-file given with GET");
    }

    // 1. Challenge handshake with AICTE's verifier.
    out("verifier_connect", json!(a.verifier));
    let mut vs = TcpStream::connect(&a.verifier).await.context("verifier unreachable")?;
    vs.set_nodelay(true)?;
    vs.write_all(format!("SCOURGE1 {} {} {}\n", a.challenge_id, a.institution, a.token).as_bytes()).await?;
    let mut line = vec![];
    let mut b = [0u8; 1];
    while line.len() < 256 {
        vs.read_exact(&mut b).await?;
        if b[0] == b'\n' {
            break;
        }
        line.push(b[0]);
    }
    let reply = String::from_utf8_lossy(&line).to_string();
    if reply != "OK" {
        bail!("verifier refused the challenge: {}", reply.trim_start_matches("ERR "));
    }
    out("challenge_accepted", json!("challenge valid and consumed by the verifier"));

    // 2. MPC-TLS session with the bank.
    let session = Session::new(vs.compat());
    let (driver, mut handle) = session.split();
    let driver_task = tokio::spawn(driver);
    let prover = handle
        .new_prover(ProverConfig::builder().build()?)?
        .commit(MpcTlsConfig::builder().max_sent_data(a.max_sent).max_recv_data(a.max_recv).build()?)
        .await?;
    let mut roots = RootCertStore::mozilla();
    for p in &a.extra_ca_der {
        roots.roots.push(CertificateDer(std::fs::read(p)?));
    }
    out("bank_connect", json!(host));
    let sock = connect_bank(&host, uri.port_u16().unwrap_or(443), &a.connect_addr).await.context("bank unreachable")?;
    out("mpc_tls", json!("TLS session with the bank, keys shared with the verifier"));
    let tls_cfg = TlsClientConfig::builder().server_name(ServerName::Dns(host.as_str().try_into()?)).root_store(roots).build()?;
    let (tls, prover) = prover.connect(tls_cfg, sock.compat())?;
    let prover_task = tokio::spawn(prover.into_future());
    let (mut sender, conn) = hyper::client::conn::http1::handshake(TokioIo::new(tls.compat())).await?;
    tokio::spawn(conn);

    let body = match &a.body_file {
        Some(p) => std::fs::read(p)?,
        None => vec![],
    };
    let mut rb = Request::builder().method(a.method.as_str()).uri(uri.path_and_query().unwrap().as_str()).header("Host", host.as_str());
    if let Some(p) = &a.headers_file {
        for line in std::fs::read_to_string(p)?.lines() {
            if let Some((k, v)) = line.split_once(':') {
                let (k, kl) = (k.trim(), k.trim().to_ascii_lowercase());
                if k.is_empty() || k.starts_with(':') || ["host", "accept-encoding", "connection", "content-length", "transfer-encoding"].contains(&kl.as_str()) {
                    continue;
                }
                rb = rb.header(k, v.trim());
            }
        }
    }
    rb = rb.header("Accept-Encoding", "identity").header("Connection", "close");
    if !body.is_empty() {
        rb = rb.header("Content-Length", body.len().to_string());
    }
    let resp = sender.send_request(rb.body(Full::new(Bytes::from(body)))?).await.context("bank closed the connection")?;
    let _ = resp.into_body().collect().await;
    let mut prover = prover_task.await??;
    out("response", json!({"bytes": prover.transcript().received().len()}));

    // 3. Decide what to disclose (fail closed on anything unexpected).
    let sent = prover.transcript().sent().to_vec();
    let recv = prover.transcript().received().to_vec();
    let prefix = format!("{} {}", ep.method, ep.path_prefix);
    if !sent.starts_with(prefix.as_bytes()) {
        bail!("request line does not start with the approved prefix");
    }
    if let Some(p) = &a.save_response {
        std::fs::write(p, &recv).context("save response locally")?;
    }
    let mut cfgb = ProveConfig::builder(prover.transcript());
    cfgb.server_identity();
    cfgb.reveal_sent(&(0..prefix.len()))?;

    let sl = find(&recv, b"\r\n").context("no status line")?;
    cfgb.reveal_recv(&(0..sl))?;
    let hend = find(&recv, b"\r\n\r\n").context("no header terminator")?;
    let mut chunked = false;
    let mut i = sl + 2;
    while i < hend + 2 {
        let e = i + find(&recv[i..], b"\r\n").unwrap();
        let line = String::from_utf8_lossy(&recv[i..e]).to_ascii_lowercase();
        if line.starts_with("content-type:") || line.starts_with("date:") || line.starts_with("transfer-encoding:") {
            cfgb.reveal_recv(&(i..e))?;
            chunked |= line.starts_with("transfer-encoding:") && line.contains("chunked");
        }
        i = e + 2;
    }
    let status = status_code(&recv[..sl]).unwrap_or(0);
    let local = json!({"http_status": status, "request_bytes": sent.len(), "response_bytes": recv.len(), "disclosure_mode": ep.disclosure});
    if status == 200 {
        let raw_body = &recv[hend + 4..];
        let body_plain = if chunked { dechunk(raw_body).map_err(|e| anyhow::anyhow!(e))? } else { raw_body.to_vec() };
        match jspan::parse(&body_plain) {
            Err(e) => out("select", json!({"warning": format!("response is not valid JSON: {e}"), "note": "status line still proven"})),
            Ok(doc) => {
                let total = doc.count_with_key(&schema.select_key);
                let mut objs = vec![];
                doc.find_objects(&schema.select_key, &a.fd_number, &mut objs);
                out("select", json!({"fd_records_in_response": total, "matching_this_fd": objs.len(), "disclosure": ep.disclosure}));
                if ep.disclosure == "body" {
                    if total > 1 {
                        bail!("privacy guard: body disclosure would reveal {total} deposits; use an object-mode endpoint");
                    }
                    cfgb.reveal_recv(&(hend + 2..recv.len()))?; // anchored by the blank line before the body
                } else {
                    if chunked {
                        bail!("object disclosure on a chunked body is unsupported (fail closed)");
                    }
                    if objs.len() == 1 {
                        let (s, e) = (hend + 4 + objs[0].start, hend + 4 + objs[0].end);
                        match &a.malicious_hide {
                            Some(k) => {
                                // TEST ONLY: hide one key/value inside the object.
                                let p = format!("\"{k}\"");
                                let ks = s + find(&recv[s..e], p.as_bytes()).context("key not in object")?;
                                let ke = ks + find(&recv[ks..e], b",").unwrap_or(e - 1 - ks);
                                cfgb.reveal_recv(&(s..ks))?;
                                cfgb.reveal_recv(&(ke..e))?;
                            }
                            None => {
                                cfgb.reveal_recv(&(s..e))?;
                            }
                        }
                    } else {
                        out("select", json!({"warning": "no unique record for this FD; disclosing headers only"}));
                    }
                }
            }
        }
    }
    out("prove", json!("sending selective-disclosure proof"));
    prover.prove(&cfgb.build()?).await?;
    prover.close().await?;
    handle.close();
    driver_task.await??;
    out("proved", local);
    Ok(())
}
