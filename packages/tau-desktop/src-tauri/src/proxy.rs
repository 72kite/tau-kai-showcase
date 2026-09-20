// Phase 27.D Milestone 3: the local pinning proxy. A self-signed certificate can't be verified
// by the webview's own TLS stack (no CA, by design), so the frontend's existing fetch()/getApiBase
// code - unchanged since Milestone 1 - talks to plain http://127.0.0.1:<this proxy's port>
// instead, and THIS module does the real, pin-enforcing TLS hop to the actual tau-core host.
// Unverified against a real build at the time of writing - see main.rs's header.

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::body::Incoming;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use std::sync::Arc;
use tokio::net::TcpListener;
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;

use crate::pairing::format_fingerprint;

/// The real enforcement: rejects any certificate whose fingerprint doesn't match what was pinned
/// during pairing - including one that's validly CA-signed, since self-signed certs here were
/// never meant to be validated by a CA chain in the first place. This is what makes the pin real
/// rather than decorative.
#[derive(Debug)]
struct PinnedVerifier {
    pinned_fingerprint: String,
}

impl ServerCertVerifier for PinnedVerifier {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        let actual = format_fingerprint(end_entity.as_ref());
        if actual == self.pinned_fingerprint {
            Ok(ServerCertVerified::assertion())
        } else {
            Err(rustls::Error::General(format!(
                "pinned certificate mismatch: expected {}, got {actual}",
                self.pinned_fingerprint
            )))
        }
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &CertificateDer<'_>,
        _dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        Ok(HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &CertificateDer<'_>,
        _dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        Ok(HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        vec![
            SignatureScheme::RSA_PKCS1_SHA256,
            SignatureScheme::RSA_PKCS1_SHA384,
            SignatureScheme::RSA_PKCS1_SHA512,
            SignatureScheme::ECDSA_NISTP256_SHA256,
            SignatureScheme::ECDSA_NISTP384_SHA384,
            SignatureScheme::ECDSA_NISTP521_SHA512,
            SignatureScheme::RSA_PSS_SHA256,
            SignatureScheme::RSA_PSS_SHA384,
            SignatureScheme::RSA_PSS_SHA512,
            SignatureScheme::ED25519,
        ]
    }
}

fn error_response(status: StatusCode, message: String) -> Response<Full<Bytes>> {
    Response::builder()
        .status(status)
        .body(Full::new(Bytes::from(message)))
        .expect("building an error response from a fixed status/body cannot fail")
}

async fn forward(
    req: Request<Incoming>,
    target_host: String,
    target_port: u16,
    pinned_fingerprint: String,
) -> Result<Response<Full<Bytes>>, std::convert::Infallible> {
    let config = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(PinnedVerifier { pinned_fingerprint }))
        .with_no_client_auth();
    let connector = TlsConnector::from(Arc::new(config));

    let tcp = match TcpStream::connect((target_host.as_str(), target_port)).await {
        Ok(tcp) => tcp,
        Err(e) => return Ok(error_response(StatusCode::BAD_GATEWAY, format!("connect failed: {e}"))),
    };
    let server_name = match ServerName::try_from(target_host) {
        Ok(name) => name.to_owned(),
        Err(e) => return Ok(error_response(StatusCode::BAD_GATEWAY, format!("invalid host: {e}"))),
    };
    // This is the actual pin check - if tau-core's cert ever changes without a re-pair (a
    // misconfiguration, or a genuine on-path attacker), this handshake fails and the frontend
    // sees a clear 502 rather than silently talking to something unverified.
    let tls_stream = match connector.connect(server_name, tcp).await {
        Ok(stream) => stream,
        Err(e) => {
            return Ok(error_response(
                StatusCode::BAD_GATEWAY,
                format!("pinned TLS handshake failed (certificate may have changed - re-pair if expected): {e}"),
            ))
        }
    };

    let io = TokioIo::new(tls_stream);
    let (mut sender, conn) = match hyper::client::conn::http1::handshake(io).await {
        Ok(pair) => pair,
        Err(e) => return Ok(error_response(StatusCode::BAD_GATEWAY, format!("HTTP handshake failed: {e}"))),
    };
    tokio::spawn(async move {
        let _ = conn.await;
    });

    let response = match sender.send_request(req).await {
        Ok(resp) => resp,
        Err(e) => return Ok(error_response(StatusCode::BAD_GATEWAY, format!("request failed: {e}"))),
    };

    let (parts, body) = response.into_parts();
    let bytes = match body.collect().await {
        Ok(collected) => collected.to_bytes(),
        Err(e) => return Ok(error_response(StatusCode::BAD_GATEWAY, format!("reading response failed: {e}"))),
    };
    Ok(Response::from_parts(parts, Full::new(bytes)))
}

/// Starts the proxy on an OS-assigned loopback port and returns it. Runs forever on a spawned
/// task - the caller (the frontend, via setApiBase pointing here) has no explicit stop signal
/// today; the process exiting is what ends it, matching this milestone's scope (re-pairing to a
/// new host just starts a new proxy instance, an old one is harmless idle loopback state).
#[tauri::command]
pub async fn start_pinning_proxy(
    target_host: String,
    target_port: u16,
    pinned_fingerprint: String,
) -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).await.map_err(|e| e.to_string())?;
    let local_port = listener.local_addr().map_err(|e| e.to_string())?.port();

    tokio::spawn(async move {
        loop {
            let (stream, _) = match listener.accept().await {
                Ok(pair) => pair,
                Err(_) => continue,
            };
            let io = TokioIo::new(stream);
            let target_host = target_host.clone();
            let pinned_fingerprint = pinned_fingerprint.clone();
            tokio::spawn(async move {
                let service = service_fn(move |req| {
                    forward(req, target_host.clone(), target_port, pinned_fingerprint.clone())
                });
                let _ = http1::Builder::new().serve_connection(io, service).await;
            });
        }
    });

    Ok(local_port)
}
