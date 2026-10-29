//! scourge_verifierd — AICTE-side verifier (separate process).
//! One TCP connection per proof:
//!   prover -> "SCOURGE1 <challenge_id> <institution_id> <token>\n"
//!   verifier asks the app to validate + consume the challenge (POST /internal/challenges/consume)
//!   verifier -> "OK\n" | "ERR <reason>\n"; on OK the same connection carries the MPC-TLS session.
//! After the session the verifier posts FACTS (never registry decisions) to POST /internal/evidence.
//! Freshness: in MPC-TLS the verifier holds a share of the session keys, so the proven session
//! must be live and must start after the challenge was consumed. The bank never sees the challenge.

#[path = "common.rs"]
mod common;

use std::{
    pin::Pin,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    task::{Context, Poll},
    time::Instant,
};

use common::*;
use serde_json::{Value, json};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadBuf},
    net::{TcpListener, TcpStream},
};
use tokio_util::compat::TokioAsyncReadCompatExt;

use tlsn::{
    Session,
    config::verifier::VerifierConfig,
    connection::ServerName,
    verifier::{VerifierCommitStart, VerifierOutput},
    webpki::{CertificateDer, RootCertStore},
};

struct Counted {
    inner: TcpStream,
    rx: Arc<AtomicU64>,
}
impl AsyncRead for Counted {
    fn poll_read(mut self: Pin<&mut Self>, cx: &mut Context<'_>, buf: &mut ReadBuf<'_>) -> Poll<std::io::Result<()>> {
        let b = buf.filled().len();
        let r = Pin::new(&mut self.inner).poll_read(cx, buf);
        self.rx.fetch_add((buf.filled().len() - b) as u64, Ordering::Relaxed);
        r
    }
}
impl AsyncWrite for Counted {
    fn poll_write(mut self: Pin<&mut Self>, cx: &mut Context<'_>, b: &[u8]) -> Poll<std::io::Result<usize>> {
        Pin::new(&mut self.inner).poll_write(cx, b)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.inner).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.inner).poll_shutdown(cx)
    }
}

/// Minimal HTTP/1.1 JSON POST to the app's internal API (localhost only).
async fn post_json(addr: &str, path: &str, key: &str, body: &Value) -> anyhow::Result<(u16, Value)> {
    let b = serde_json::to_vec(body)?;
    let mut s = TcpStream::connect(addr).await?;
    let head = format!(
        "POST {path} HTTP/1.1\r\nHost: {addr}\r\nContent-Type: application/json\r\nX-Internal-Key: {key}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        b.len()
    );
    s.write_all(head.as_bytes()).await?;
    s.write_all(&b).await?;
    let mut resp = vec![];
    s.read_to_end(&mut resp).await?;
    let he = find(&resp, b"\r\n\r\n").ok_or_else(|| anyhow::anyhow!("bad response from app"))?;
    let code = status_code(&resp[..find(&resp, b"\r\n").unwrap()]).unwrap_or(0);
    let head_s = String::from_utf8_lossy(&resp[..he]).to_ascii_lowercase();
    let mut body_b = resp[he + 4..].to_vec();
    if head_s.contains("transfer-encoding: chunked") {
        body_b = dechunk(&body_b).map_err(|e| anyhow::anyhow!(e))?;
    }
    let v: Value = serde_json::from_slice(&body_b).unwrap_or(Value::Null);
    Ok((code, v))
}

async fn read_line(s: &mut TcpStream) -> std::io::Result<String> {
    let mut out = vec![];
    let mut b = [0u8; 1];
    while out.len() < 512 {
        s.read_exact(&mut b).await?;
        if b[0] == b'\n' {
            break;
        }
        out.push(b[0]);
    }
    Ok(String::from_utf8_lossy(&out).trim().to_string())
}

fn roots(cfg: &Config) -> RootCertStore {
    match cfg.trust.mode.as_str() {
        "test_ca" => RootCertStore { roots: vec![CertificateDer(std::fs::read(cfg.trust.ca_der.as_ref().unwrap()).expect("ca_der"))] },
        _ => RootCertStore::mozilla(),
    }
}

fn log(v: Value) {
    eprintln!("{v}");
}

#[tokio::main]
async fn main() {
    let path = std::env::args().nth(1).unwrap_or_else(|| "config/scourge.json".into());
    let cfg = match Config::load(&path) {
        Ok(c) => c,
        Err(e) => {
            log(json!({"startup": "refused", "reason": e}));
            std::process::exit(2);
        }
    };
    let key = match std::env::var("SCOURGE_INTERNAL_KEY") {
        Ok(k) if k.len() >= 32 => k,
        _ => {
            log(json!({"startup": "refused", "reason": "SCOURGE_INTERNAL_KEY missing or shorter than 32 chars"}));
            std::process::exit(2);
        }
    };
    let listener = match TcpListener::bind(&cfg.verifier_listen).await {
        Ok(l) => l,
        Err(e) => {
            log(json!({"startup": "refused", "reason": format!("bind {}: {e}", cfg.verifier_listen)}));
            std::process::exit(2);
        }
    };
    log(json!({"startup": "ok", "listen": cfg.verifier_listen, "trust": cfg.trust.mode, "allow_hosts": cfg.allow_hosts}));
    let key = Arc::new(key);
    loop {
        let Ok((sock, _)) = listener.accept().await else { continue };
        let cfg = cfg.clone();
        let key = key.clone();
        tokio::spawn(async move {
            if let Err(e) = handle(sock, cfg, key).await {
                log(json!({"conn_error": e.to_string()}));
            }
        });
    }
}

async fn handle(mut sock: TcpStream, cfg: Config, key: Arc<String>) -> anyhow::Result<()> {
    sock.set_nodelay(true)?;
    let line = read_line(&mut sock).await?;
    let parts: Vec<&str> = line.split(' ').collect();
    if parts.len() != 4 || parts[0] != "SCOURGE1" {
        sock.write_all(b"ERR bad_hello\n").await?;
        return Ok(());
    }
    let (cid, inst, token) = (parts[1], parts[2], parts[3]);
    let (code, resp) = match post_json(&cfg.app_internal, "/internal/challenges/consume", &key, &json!({"challenge_id": cid, "institution_id": inst, "token": token})).await {
        Ok(x) => x,
        Err(e) => {
            log(json!({"challenge": cid, "error": format!("app unreachable: {e}")}));
            sock.write_all(b"ERR registry_unavailable\n").await?;
            return Ok(());
        }
    };
    if code != 200 {
        let reason = resp.get("reason").and_then(|r| r.as_str()).unwrap_or("challenge_refused").to_string();
        sock.write_all(format!("ERR {reason}\n").as_bytes()).await?;
        return Ok(());
    }
    let ch: ChallengeInfo = serde_json::from_value(resp)?;
    sock.write_all(b"OK\n").await?;
    log(json!({"challenge": ch.challenge_id, "state": "consumed; MPC session starting"}));

    let t0 = Instant::now();
    let rx = Arc::new(AtomicU64::new(0));
    let outcome = verify_session(Counted { inner: sock, rx: rx.clone() }, &cfg).await;
    let elapsed = (t0.elapsed().as_secs_f64() * 100.0).round() / 100.0;
    let mut out = match outcome {
        Ok((r, committed)) => {
            let mut f = facts(&cfg, &ch, &r);
            f["metrics"] = json!({"request_bytes_actual": r.sent_len, "response_bytes_actual": r.recv_len,
                "max_sent_committed": committed.0, "max_recv_committed": committed.1,
                "mpc_bytes_prover_to_verifier": rx.load(Ordering::Relaxed), "elapsed_s": elapsed});
            f
        }
        Err(e) => json!({"gate": "rejected", "reason": "tls_proof_failed",
            "stages": {"tls_proof": format!("failed: {e}")},
            "metrics": {"mpc_bytes_prover_to_verifier": rx.load(Ordering::Relaxed), "elapsed_s": elapsed}}),
    };
    out["challenge_id"] = json!(ch.challenge_id);
    out["verifier_trust"] = json!(cfg.trust.mode);
    match post_json(&cfg.app_internal, "/internal/evidence", &key, &out).await {
        Ok((200, _)) => log(json!({"challenge": ch.challenge_id, "gate": out["gate"], "posted": true})),
        Ok((c, v)) => log(json!({"challenge": ch.challenge_id, "post_failed": c, "resp": v})),
        Err(e) => log(json!({"challenge": ch.challenge_id, "post_failed": e.to_string()})),
    }
    Ok(())
}

async fn verify_session(sock: Counted, cfg: &Config) -> anyhow::Result<(Revealed, (usize, usize))> {
    let session = Session::new(sock.compat());
    let (driver, mut handle) = session.split();
    let driver_task = tokio::spawn(driver);
    let verifier = handle.new_verifier(VerifierConfig::builder().root_store(roots(cfg)).build()?)?;
    let (verifier, committed) = match verifier.commit().await? {
        VerifierCommitStart::Mpc(v) => {
            let (s, r) = (v.config().max_sent_data(), v.config().max_recv_data());
            if s > cfg.max_sent || r > cfg.max_recv {
                v.reject(Some("limits above verifier policy")).await?;
                anyhow::bail!("committed limits {s}/{r} exceed verifier policy {}/{}", cfg.max_sent, cfg.max_recv);
            }
            (v.accept().await?.run().await?, (s, r))
        }
        VerifierCommitStart::Proxy(v) => {
            v.reject(Some("MPC-TLS only")).await?;
            anyhow::bail!("proxy mode refused");
        }
    };
    let verifier = verifier.verify().await?;
    let (VerifierOutput { server_name, transcript, .. }, verifier) = verifier.accept().await?;
    verifier.close().await?;
    handle.close();
    driver_task.await??;
    let ServerName::Dns(name) = server_name.ok_or_else(|| anyhow::anyhow!("server name not disclosed"))?;
    let t = transcript.ok_or_else(|| anyhow::anyhow!("no transcript disclosed"))?;
    let (sl, rl) = (t.len_sent(), t.len_received());
    Ok((
        Revealed {
            server_name: name.as_str().to_string(),
            sent: t.sent_unsafe(sl)?,
            sent_ranges: t.sent_authed().iter().collect(),
            recv: t.received_unsafe(rl)?,
            recv_ranges: t.received_authed().iter().collect(),
            sent_len: sl,
            recv_len: rl,
        },
        committed,
    ))
}
