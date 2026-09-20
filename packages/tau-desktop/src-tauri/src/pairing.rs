// Phase 27.D Milestone 3: TLS certificate pairing. tau-core generates one self-signed cert
// (packages/tau-core/src/tau_core/tls.py) and its fingerprint IS the pairing code - the same
// trust-on-first-use pattern as an SSH host-key fingerprint. This module's job is narrow: connect
// once with NO certificate validation (that's what the human comparison step is for - see
// `AcceptAnyAndCapture` below) purely to see what certificate is actually being presented, and
// format its fingerprint identically to tau-core's own `/api/tls/fingerprint` endpoint so a human
// can compare them at a glance. Unverified against a real build at the time of writing.

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use sha2::{Digest, Sha256};
use std::sync::{Arc, Mutex};
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;

/// SHA-256 fingerprint, colon-grouped uppercase hex - must match tau-core's `tls.fingerprint_
/// sha256()` byte-for-byte formatting, since a human compares these two strings directly.
pub fn format_fingerprint(der: &[u8]) -> String {
    let digest = Sha256::digest(der);
    digest.iter().map(|b| format!("{b:02X}")).collect::<Vec<_>>().join(":")
}

/// Deliberately accepts ANY certificate - this connection is unverified by design, exactly once,
/// during pairing. Security here comes entirely from the human comparing the captured
/// certificate's fingerprint against what tau-core's admin dashboard shows, not from this
/// verifier. Captures the presented certificate's raw bytes into `captured` for the caller to
/// read after the handshake completes.
#[derive(Debug)]
struct AcceptAnyAndCapture {
    captured: Arc<Mutex<Option<Vec<u8>>>>,
}

impl ServerCertVerifier for AcceptAnyAndCapture {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        *self.captured.lock().unwrap() = Some(end_entity.as_ref().to_vec());
        Ok(ServerCertVerified::assertion())
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
        // The full set rustls's own tests use for a permissive verifier - this connection never
        // actually needs the *signature* to be trustworthy (verify_tls1{2,3}_signature above
        // always accepts), only the certificate BYTES to be captured, so being permissive about
        // which schemes are "supported" doesn't weaken anything beyond what this verifier already
        // deliberately gives up.
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

#[tauri::command]
pub async fn fetch_pairing_fingerprint(host: String, port: u16) -> Result<String, String> {
    let _ = rustls::crypto::ring::default_provider().install_default();

    let captured = Arc::new(Mutex::new(None));
    let verifier = Arc::new(AcceptAnyAndCapture { captured: captured.clone() });
    let config = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth();
    let connector = TlsConnector::from(Arc::new(config));

    let tcp = TcpStream::connect((host.as_str(), port)).await.map_err(|e| e.to_string())?;
    let server_name = ServerName::try_from(host).map_err(|e| e.to_string())?.to_owned();
    connector.connect(server_name, tcp).await.map_err(|e| e.to_string())?;

    let cert_bytes = captured
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "no certificate was presented during the handshake".to_string())?;
    Ok(format_fingerprint(&cert_bytes))
}
