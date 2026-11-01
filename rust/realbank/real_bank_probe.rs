//! SIH26265 smallest useful experiment: run a bank-session proof against a REAL bank page,
//! entirely on the tester's own machine (prover and AICTE-style verifier in one process).
//!
//! Privacy: header values (cookies, tokens) are never printed. Revealed field VALUES are not
//! printed either unless SHOW_VALUES=1; by default only "found / length" is reported, so the
//! output can be shared with the team.
//!
//! Env:
//!   URL=https://host/path              (required) the request that returns the FD details
//!   HEADERS_FILE=headers.txt           optional "Name: value" lines copied from browser dev-tools
//!   REVEAL=depositNo,status,...        JSON keys ("key":"value") or literal labels to reveal
//!   ALLOW=onlinesbi.sbi,bank.in        allowed domain suffixes (verifier side)
//!   MAX_SENT=4096 MAX_RECV=65536       MPC budgets in bytes (traffic scales with MAX_SENT; see BUDGETS.md)
//!   HTTPS_PROXY=http://host:port       optional CONNECT proxy (used only to reach the bank)

use std::{
    env,
    future::IntoFuture,
    pin::Pin,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    task::{Context, Poll},
    time::Instant,
};

use anyhow::{Context as _, Result};
use http_body_util::{BodyExt, Full};
use hyper::{Request, Uri, body::Bytes};
use hyper_util::rt::TokioIo;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, DuplexStream, ReadBuf};
use tokio_util::compat::{FuturesAsyncReadCompatExt, TokioAsyncReadCompatExt};

use tlsn::{
    Session,
    config::{
        prove::ProveConfig, prover::ProverConfig, tls::TlsClientConfig, tls_commit::mpc::MpcTlsConfig,
        verifier::VerifierConfig,
    },
    connection::ServerName,
    transcript::PartialTranscript,
    verifier::{VerifierCommitStart, VerifierOutput},
    webpki::RootCertStore,
};

struct Counted {
    inner: DuplexStream,
    tx: Arc<AtomicU64>,
}
impl AsyncRead for Counted {
    fn poll_read(mut self: Pin<&mut Self>, cx: &mut Context<'_>, buf: &mut ReadBuf<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.inner).poll_read(cx, buf)
    }
}
impl AsyncWrite for Counted {
    fn poll_write(mut self: Pin<&mut Self>, cx: &mut Context<'_>, b: &[u8]) -> Poll<std::io::Result<usize>> {
        let r = Pin::new(&mut self.inner).poll_write(cx, b);
        if let Poll::Ready(Ok(n)) = &r {
            self.tx.fetch_add(*n as u64, Ordering::Relaxed);
        }
        r
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.inner).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.inner).poll_shutdown(cx)
    }
}

fn envs(k: &str, d: &str) -> String {
    env::var(k).unwrap_or(d.into())
}

/// Spans to reveal for one key: JSON `"key":"value"` (or `"key":number`) if present, else the literal label.
fn spans_for(buf: &[u8], key: &str) -> Vec<(usize, usize)> {
    let mut out = vec![];
    let pat = format!("\"{key}\":");
    let mut from = 0;
    while let Some(p) = buf[from..].windows(pat.len()).position(|w| w == pat.as_bytes()) {
        let s = from + p;
        let mut e = s + pat.len();
        if buf.get(e) == Some(&b'"') {
            e += 1;
            while e < buf.len() && buf[e] != b'"' {
                e += 1;
            }
            e += 1;
        } else {
            while e < buf.len() && !b",}]".contains(&buf[e]) {
                e += 1;
            }
        }
        out.push((s, e.min(buf.len())));
        from = e.min(buf.len());
    }
    if out.is_empty() {
        if let Some(p) = buf.windows(key.len()).position(|w| w == key.as_bytes()) {
            out.push((p, p + key.len()));
        }
    }
    out
}

async fn connect(host: &str, port: u16) -> Result<tokio::net::TcpStream> {
    if let Ok(proxy) = env::var("HTTPS_PROXY").or_else(|_| env::var("https_proxy")) {
        let pu: Uri = proxy.parse()?;
        let mut s = tokio::net::TcpStream::connect((pu.host().unwrap(), pu.port_u16().unwrap_or(80))).await?;
        s.write_all(format!("CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n").as_bytes()).await?;
        let mut buf = vec![0u8; 1];
        let mut head = vec![];
        while !head.ends_with(b"\r\n\r\n") {
            s.read_exact(&mut buf).await?;
            head.push(buf[0]);
        }
        let line = String::from_utf8_lossy(&head);
        anyhow::ensure!(line.contains(" 200"), "proxy refused CONNECT: {}", line.lines().next().unwrap_or(""));
        s.set_nodelay(true)?;
        Ok(s)
    } else {
        let s = tokio::net::TcpStream::connect((host, port)).await?;
        s.set_nodelay(true)?;
        Ok(s)
    }
}

#[tokio::main]
async fn main() {
    let url = env::var("URL").expect("set URL");
    let uri: Uri = url.parse().expect("bad URL");
    let host = uri.host().unwrap().to_string();
    let max_sent: usize = envs("MAX_SENT", "4096").parse().unwrap();
    let max_recv: usize = envs("MAX_RECV", "65536").parse().unwrap();
    let keys: Vec<String> = envs("REVEAL", "").split(',').filter(|s| !s.is_empty()).map(String::from).collect();

    let (p, v) = tokio::io::duplex(1 << 23);
    let up = Arc::new(AtomicU64::new(0));
    let p = Counted { inner: p, tx: up.clone() };
    let t0 = Instant::now();
    let res = tokio::try_join!(prover(p, &uri, &host, max_sent, max_recv, &keys), verifier(v, max_recv));
    let secs = t0.elapsed().as_secs_f64();
    match res {
        Ok((sent_len, (t, server))) => {
            let recv = t.received_unsafe(t.len_received()).unwrap();
            let status = String::from_utf8_lossy(&recv[..recv.iter().position(|&b| b == b'\r' || b == 0).unwrap_or(0)]).to_string();
            let show = env::var("SHOW_VALUES").is_ok();
            let mut fields = vec![];
            for k in &keys {
                let sp = spans_for(&recv, k);
                let shown: Vec<String> = sp
                    .iter()
                    .map(|(s, e)| {
                        if show { String::from_utf8_lossy(&recv[*s..*e]).replace('"', "'") } else { format!("{} bytes", e - s) }
                    })
                    .collect();
                fields.push(format!("{{\"key\":\"{k}\",\"found\":{},\"revealed\":{:?}}}", !sp.is_empty(), shown));
            }
            println!(
                "{{\"ok\":true,\"server\":\"{server}\",\"status_line\":\"{status}\",\"secs\":{secs:.1},\"request_bytes\":{sent_len},\"response_bytes\":{},\"disclosed_response_bytes\":{},\"prover_to_verifier_MB\":{:.1},\"fields\":[{}]}}",
                recv.len(),
                recv.iter().filter(|&&b| b != 0).count(),
                up.load(Ordering::Relaxed) as f64 / 1e6,
                fields.join(",")
            );
        }
        Err(e) => println!("{{\"ok\":false,\"secs\":{secs:.1},\"error\":\"{}\"}}", format!("{e:#}").replace('"', "'")),
    }
}

async fn prover<T: AsyncWrite + AsyncRead + Send + Unpin + 'static>(
    vsock: T,
    uri: &Uri,
    host: &str,
    max_sent: usize,
    max_recv: usize,
    keys: &[String],
) -> Result<usize> {
    let session = Session::new(vsock.compat());
    let (driver, mut handle) = session.split();
    let driver_task = tokio::spawn(driver);
    let prover = handle
        .new_prover(ProverConfig::builder().build()?)?
        .commit(MpcTlsConfig::builder().max_sent_data(max_sent).max_recv_data(max_recv).build()?)
        .await?;
    // Attack test hooks: a dishonest prover can point at its own server and trust its own CA.
    let sock = if let Ok(addr) = env::var("CONNECT_ADDR") {
        let s = tokio::net::TcpStream::connect(addr).await?;
        s.set_nodelay(true)?;
        s
    } else {
        connect(host, uri.port_u16().unwrap_or(443)).await.context("reach bank")?
    };
    let roots = if env::var("PROVER_TRUSTS_OWN_CA").is_ok() {
        RootCertStore { roots: vec![tlsn::webpki::CertificateDer(tlsn_server_fixture_certs::CA_CERT_DER.to_vec())] }
    } else {
        RootCertStore::mozilla()
    };
    let tls_cfg = TlsClientConfig::builder()
        .server_name(ServerName::Dns(host.try_into()?))
        .root_store(roots)
        .build()?;
    let (tls, prover) = prover.connect(tls_cfg, sock.compat())?;
    let prover_task = tokio::spawn(prover.into_future());
    let (mut sender, conn) = hyper::client::conn::http1::handshake(TokioIo::new(tls.compat())).await?;
    tokio::spawn(conn);

    // METHOD=POST with BODY_FILE=<file holding the exact request payload from DevTools> for banks whose FD/account API is a POST.
    // The body (like the headers) stays hidden in the proof; only the request line is revealed.
    let method = envs("METHOD", "GET").to_ascii_uppercase();
    if method != "GET" && method != "POST" { anyhow::bail!("METHOD must be GET or POST"); }
    let body: Vec<u8> = match env::var("BODY_FILE") {
        Ok(p) => std::fs::read_to_string(p)?.trim_end_matches(['\r', '\n']).as_bytes().to_vec(),
        Err(_) => Vec::new(),
    };
    if method == "GET" && !body.is_empty() { anyhow::bail!("BODY_FILE is only for METHOD=POST"); }
    let mut rb = Request::builder().uri(uri.path_and_query().unwrap().as_str()).method(method.as_str()).header("Host", host);
    if let Ok(path) = env::var("HEADERS_FILE") {
        for line in std::fs::read_to_string(path)?.lines() {
            if let Some((k, v)) = line.split_once(':') {
                let k = k.trim();
                let kl = k.to_ascii_lowercase();
                if k.is_empty() || k.starts_with(':') || ["host", "accept-encoding", "connection", "content-length"].contains(&kl.as_str()) {
                    continue;
                }
                rb = rb.header(k, v.trim());
            }
        }
    }
    rb = rb.header("Accept-Encoding", "identity").header("Connection", "close");
    if method == "POST" { rb = rb.header("Content-Length", body.len().to_string()); }
    let resp = sender.send_request(rb.body(Full::new(Bytes::from(body)))?).await?;
    let _ = resp.into_body().collect().await;

    let mut prover = prover_task.await??;
    let sent = prover.transcript().sent().to_vec();
    let recv = prover.transcript().received().to_vec();
    let mut b = ProveConfig::builder(prover.transcript());
    b.server_identity();
    // Sent: reveal only the request line (method + path); all headers incl. cookies stay hidden.
    let le = sent.windows(2).position(|w| w == b"\r\n").unwrap();
    b.reveal_sent(&(0..le))?;
    // Received: status line + Date header + requested fields only.
    let sl = recv.windows(2).position(|w| w == b"\r\n").unwrap_or(0);
    b.reveal_recv(&(0..sl))?;
    if let Some(d) = recv.windows(6).position(|w| w.eq_ignore_ascii_case(b"\r\ndate")) {
        let e = d + 2 + recv[d + 2..].windows(2).position(|w| w == b"\r\n").unwrap_or(0);
        b.reveal_recv(&(d + 2..e))?;
    }
    for k in keys {
        for (s, e) in spans_for(&recv, k) {
            b.reveal_recv(&(s..e))?;
        }
    }
    prover.prove(&b.build()?).await?;
    prover.close().await?;
    handle.close();
    driver_task.await??;
    Ok(sent.len())
}

async fn verifier<T: AsyncWrite + AsyncRead + Send + Sync + Unpin + 'static>(
    psock: T,
    max_recv: usize,
) -> Result<(PartialTranscript, String)> {
    let session = Session::new(psock.compat());
    let (driver, mut handle) = session.split();
    let driver_task = tokio::spawn(driver);
    // The verifier trusts only the public Web PKI (Mozilla roots), never a root the prover supplies.
    // Self-test only: PROVER_TRUSTS_OWN_CA makes both sides trust the TLSNotary fixture CA. Real runs use Mozilla roots.
    let vroots = if env::var("PROVER_TRUSTS_OWN_CA").is_ok() {
        RootCertStore { roots: vec![tlsn::webpki::CertificateDer(tlsn_server_fixture_certs::CA_CERT_DER.to_vec())] }
    } else {
        RootCertStore::mozilla()
    };
    let verifier = handle.new_verifier(VerifierConfig::builder().root_store(vroots).build()?)?;
    let verifier = match verifier.commit().await? {
        VerifierCommitStart::Mpc(v) => {
            if v.config().max_recv_data() > max_recv {
                v.reject(Some("too large")).await?;
                anyhow::bail!("rejected config");
            }
            v.accept().await?.run().await?
        }
        VerifierCommitStart::Proxy(v) => {
            v.reject(Some("MPC only")).await?;
            anyhow::bail!("proxy mode refused");
        }
    };
    let verifier = verifier.verify().await?;
    let (VerifierOutput { server_name, transcript, .. }, verifier) = verifier.accept().await?;
    verifier.close().await?;
    handle.close();
    driver_task.await??;
    let ServerName::Dns(name) = server_name.context("server name not revealed")?;
    let name = name.as_str().to_string();
    let allow = envs("ALLOW", "");
    anyhow::ensure!(
        allow.is_empty() || allow.split(',').any(|d| name == d || name.ends_with(&format!(".{d}"))),
        "server '{name}' not on the bank allow-list"
    );
    Ok((transcript.context("no transcript")?, name))
}
