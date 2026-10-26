#!/usr/bin/env python
"""make_testbed_pki.py -- one certificate authority and one certificate per SDC testbed participant.

Modelled on the working example in pat/certs (Draeger test PKI shipped with sdc11073):
  * CA: RSA 4096, Basic Constraints CA:TRUE (critical), Key Usage certSign+cRLSign (critical),
    Subject/Authority Key Identifier, self-signed.
  * Participant: RSA 2048, Extended Key Usage = serverAuth, clientAuth (serverAuth FIRST, because
    some stacks inspect only the first EKU), plus the two SDC EKUs from the
    pat example (1.2.840.10004.20701.1.1 provider, .1.2 consumer); Subject/Authority Key Identifier;
    Subject Alternative Name = participant name, localhost, 127.0.0.1, given IPs.
    Like the pat example there is NO Basic Constraints / Key Usage on participant certificates.
  * Private keys: PKCS#8 PEM ("BEGIN PRIVATE KEY") like the pat example, but UNENCRYPTED, because the
    provider stacks load the key file without a passphrase.
  * Signatures: SHA-256 with RSA.

Layout (default --out pki):
  pki/ca/ca.pem           CA certificate (public)
  pki/ca/ca.key           CA private key  -- never copied anywhere else
  pki/manifest.json       what was issued, with which IPs, fingerprints
  pki/<participant>/      the participant's key + certificate + CA under every file name the
                          stacks expect (see FILE_ALIASES), plus README.txt

Modes:
  issue (default)  python tools/make_testbed_pki.py --ip 10.0.0.1 10.0.0.2 [--force]
  --add            python tools/make_testbed_pki.py --add NAME [--role provider|consumer] [--ip ...]
                   issue ONE more participant with the existing CA; nothing else is touched
  --verify         python tools/make_testbed_pki.py --verify [NAME ...]
                   pure-Python chain/EKU/SAN check of participant certificates against pki/ca/ca.pem
  --selftest       python tools/make_testbed_pki.py --selftest [--server spo2] [--client dashboard]
                   mutual-TLS handshake over localhost (must pass) and the same handshake with a
                   client certificate from a foreign, temporary CA (must fail with unknown CA)

Safety: an existing CA or participant folder is only overwritten with --force.
This script never prints private key material.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import pathlib
import shutil
import socket
import ssl
import sys
import tempfile
import threading

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, ObjectIdentifier
except ImportError:  # pragma: no cover
    sys.stderr.write('ERROR: the "cryptography" package is required: pip install cryptography\n')
    sys.exit(2)

# ---------------------------------------------------------------- parameters
CA_KEY_BITS = 4096          # pat root: RSA 4096
LEAF_KEY_BITS = 2048        # pat user certificate: RSA 2048
CA_DAYS = 365 * 30
LEAF_DAYS = 365 * 20
COUNTRY = 'DE'
ORG = 'SDC Testbed'
CA_CN = 'SDC Testbed Root CA'

SDC_PROVIDER_EKU = ObjectIdentifier('1.2.840.10004.20701.1.1')   # present in pat user certificate
SDC_CONSUMER_EKU = ObjectIdentifier('1.2.840.10004.20701.1.2')   # present in pat user certificate

DEFAULT_PARTICIPANTS: dict[str, str] = {
    'dashboard': 'consumer',
    'testconsumer': 'consumer',
    'spo2': 'provider',
    'abp': 'provider',
    'ecg': 'provider',
    'vent': 'provider',
}

# File names the stacks expect. {name} = participant name.
#  sdc11073 certloader defaults: userkey.pem / usercert.pem / cacert.pem  (brief asks for sdccert.pem too)
#  Dashboard/device/ssl_builder.py first candidate: consumer.pem / consumer.key / ca1.pem
#  other stacks used during development: myProvider.pem / myProvider.key / ca1.pem (or provider.pem/provider.key/ca.pem)
#  ... or providerpublic.pem / providerprivate.pem / ca.pem
FILE_ALIASES: dict[str, dict[str, list[str]]] = {
    'common': {
        'key': ['userkey.pem', '{name}.key'],
        'cert': ['usercert.pem', 'sdccert.pem', '{name}.pem'],
        'ca': ['cacert.pem', 'ca.pem', 'ca1.pem'],
    },
    'consumer': {
        'key': ['consumer.key'],
        'cert': ['consumer.pem'],
        'ca': [],
    },
    'provider': {
        'key': ['provider.key', 'myProvider.key', 'providerprivate.pem'],
        'cert': ['provider.pem', 'myProvider.pem', 'providerpublic.pem'],
        'ca': [],
    },
}


# ---------------------------------------------------------------- helpers
def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _name(cn: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, COUNTRY),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])


def _write_private(path: pathlib.Path, key: rsa.RSAPrivateKey) -> None:
    data = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,          # "BEGIN PRIVATE KEY", as in pat (there: encrypted)
        serialization.NoEncryption(),
    )
    path.write_bytes(data)
    if os.name != 'nt':
        os.chmod(path, 0o600)


def _write_cert(path: pathlib.Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _fingerprint(cert: x509.Certificate) -> str:
    return cert.fingerprint(hashes.SHA256()).hex(':').upper()


def _parse_ips(values: list[str]) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    out = []
    for v in values:
        try:
            out.append(ipaddress.ip_address(v))
        except ValueError:
            raise SystemExit(f'ERROR: {v!r} is not an IP address')
    return out


def make_ca(cn: str = CA_CN) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=CA_KEY_BITS)
    name = _name(cn)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ski, critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_leaf(
    name: str,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=LEAF_KEY_BITS)
    now = _now()
    san_entries: list[x509.GeneralName] = [x509.DNSName(name), x509.DNSName('localhost'),
                                           x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
    for ip in ips:
        if ip != ipaddress.ip_address('127.0.0.1'):
            san_entries.append(x509.IPAddress(ip))
    ca_ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(name))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH,     # must be first: some stacks inspect only the first EKU
                ExtendedKeyUsageOID.CLIENT_AUTH,
                SDC_PROVIDER_EKU,
                SDC_CONSUMER_EKU,
            ]),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def write_participant(out: pathlib.Path, name: str, role: str, key: rsa.RSAPrivateKey,
                      cert: x509.Certificate, ca_cert: x509.Certificate) -> pathlib.Path:
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    written: dict[str, list[str]] = {'key': [], 'cert': [], 'ca': []}
    for group in ('common', role):
        for kind, names in FILE_ALIASES[group].items():
            for fname in names:
                fname = fname.format(name=name)
                data = {'key': key_pem, 'cert': cert_pem, 'ca': ca_pem}[kind]
                p = folder / fname
                p.write_bytes(data)
                if kind == 'key' and os.name != 'nt':
                    os.chmod(p, 0o600)
                written[kind].append(fname)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    readme = [
        f'Participant : {name}  (role: {role})',
        f'Subject     : {cert.subject.rfc4514_string()}',
        f'Issuer      : {cert.issuer.rfc4514_string()}',
        f'Valid       : {cert.not_valid_before_utc:%Y-%m-%d} .. {cert.not_valid_after_utc:%Y-%m-%d}',
        f'SHA-256     : {_fingerprint(cert)}',
        'SAN         : ' + ', '.join(str(v) for v in san),
        'EKU         : serverAuth, clientAuth, 1.2.840.10004.20701.1.1, 1.2.840.10004.20701.1.2',
        '',
        'Identical content under several names, so no file has to be renamed on the target machine:',
        '  private key (PKCS#8, unencrypted) : ' + ', '.join(written['key']),
        '  certificate                       : ' + ', '.join(written['cert']),
        '  CA certificate (trust anchor)     : ' + ', '.join(written['ca']),
        '',
        'sdc11073: mk_ssl_contexts_from_folder(<this folder>, private_key="userkey.pem",',
        '          certificate="sdccert.pem", ca_public_key="cacert.pem")',
        'Other stacks: ca1.pem / <name>.pem / <name>.key  or  ca.pem / providerpublic.pem / providerprivate.pem',
        '(copy this folder as the certificate directory of that stack)',
    ]
    (folder / 'README.txt').write_text('\n'.join(readme) + '\n', encoding='utf-8')
    return folder


# ---------------------------------------------------------------- manifest
def load_manifest(out: pathlib.Path) -> dict:
    p = out / 'manifest.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}


def save_manifest(out: pathlib.Path, manifest: dict) -> None:
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def load_ca(out: pathlib.Path) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    ca_dir = out / 'ca'
    key = serialization.load_pem_private_key((ca_dir / 'ca.key').read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate((ca_dir / 'ca.pem').read_bytes())
    return key, cert


def write_top_readme(out: pathlib.Path) -> None:
    text = f"""# Testbed PKI (generated by tools/make_testbed_pki.py)

* `ca/ca.pem`  - trust anchor of the whole testbed (copy to every machine).
* `ca/ca.key`  - CA private key. Stays here. Never copy it to a participant.
* `<participant>/` - that participant's private key, certificate and the CA certificate under all
  file names the stacks expect (see README.txt inside each folder).
* `manifest.json` - issued participants, IPs used in SAN, SHA-256 fingerprints.

Re-issue everything: `python tools/make_testbed_pki.py --ip ... --force`
Add one participant:  `python tools/make_testbed_pki.py --add NAME --role provider|consumer`
Check:                `python tools/make_testbed_pki.py --verify` and `--selftest`
"""
    (out / 'README.md').write_text(text, encoding='utf-8')


# ---------------------------------------------------------------- commands
def cmd_issue(out: pathlib.Path, participants: dict[str, str], ips: list[str], force: bool) -> int:
    ca_dir = out / 'ca'
    if ca_dir.exists() and not force:
        print(f'REFUSED: {ca_dir} already exists. Use --force to re-issue the whole set, '
              f'or --add NAME to add one participant with the existing CA.')
        return 1
    existing = [n for n in participants if (out / n).exists()]
    if existing and not force:
        print(f'REFUSED: participant folder(s) already exist: {existing}. Use --force.')
        return 1
    ip_objs = _parse_ips(ips)
    if force:
        for n in [*participants, 'ca']:
            shutil.rmtree(out / n, ignore_errors=True)
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = make_ca()
    _write_private(ca_dir / 'ca.key', ca_key)
    _write_cert(ca_dir / 'ca.pem', ca_cert)
    print(f'CA      : {ca_dir / "ca.pem"}  ({CA_KEY_BITS}-bit RSA, SHA-256 {_fingerprint(ca_cert)})')
    manifest = {
        'created': _now().isoformat(timespec='seconds'),
        'ca': {'subject': ca_cert.subject.rfc4514_string(), 'sha256': _fingerprint(ca_cert),
               'not_after': ca_cert.not_valid_after_utc.isoformat()},
        'ips': [str(i) for i in ip_objs],
        'participants': {},
    }
    for name, role in participants.items():
        key, cert = make_leaf(name, ca_key, ca_cert, ip_objs)
        folder = write_participant(out, name, role, key, cert, ca_cert)
        manifest['participants'][name] = {
            'role': role, 'sha256': _fingerprint(cert), 'not_after': cert.not_valid_after_utc.isoformat(),
        }
        print(f'issued  : {name:13s} role={role:9s} -> {folder}')
    save_manifest(out, manifest)
    write_top_readme(out)
    return 0


def cmd_add(out: pathlib.Path, name: str, role: str, ips: list[str] | None, force: bool) -> int:
    if not (out / 'ca' / 'ca.key').exists():
        print(f'ERROR: no CA in {out / "ca"}; issue the set first.')
        return 1
    if (out / name).exists() and not force:
        print(f'REFUSED: {out / name} already exists. Use --force to re-issue this participant.')
        return 1
    manifest = load_manifest(out)
    ip_list = ips if ips is not None else manifest.get('ips', [])
    ca_key, ca_cert = load_ca(out)
    key, cert = make_leaf(name, ca_key, ca_cert, _parse_ips(ip_list))
    if force:
        shutil.rmtree(out / name, ignore_errors=True)
    folder = write_participant(out, name, role, key, cert, ca_cert)
    manifest.setdefault('participants', {})[name] = {
        'role': role, 'sha256': _fingerprint(cert), 'not_after': cert.not_valid_after_utc.isoformat(),
    }
    save_manifest(out, manifest)
    print(f'added   : {name} role={role} -> {folder}  (SAN IPs: {ip_list})')
    return 0


def verify_leaf(cert: x509.Certificate, ca_cert: x509.Certificate, expect_name: str | None = None) -> list[str]:
    """Return a list of problems (empty = OK). Pure cryptography, no OpenSSL CLI."""
    problems: list[str] = []
    if cert.issuer != ca_cert.subject:
        problems.append('issuer does not match CA subject')
    try:
        ca_cert.public_key().verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(),
                                    cert.signature_hash_algorithm)
    except Exception as exc:  # noqa: BLE001
        problems.append(f'signature not made by CA ({type(exc).__name__})')
    try:
        aki = cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier
        ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
        if aki != ski:
            problems.append('AuthorityKeyIdentifier does not match CA SubjectKeyIdentifier')
    except x509.ExtensionNotFound:
        problems.append('missing AKI/SKI')
    now = _now()
    if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
        problems.append('outside validity period')
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if ExtendedKeyUsageOID.SERVER_AUTH not in eku or ExtendedKeyUsageOID.CLIENT_AUTH not in eku:
            problems.append('EKU lacks serverAuth and/or clientAuth')
        if list(eku)[0] != ExtendedKeyUsageOID.SERVER_AUTH:
            problems.append('serverAuth is not the first EKU')
    except x509.ExtensionNotFound:
        problems.append('no ExtendedKeyUsage')
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns = san.get_values_for_type(x509.DNSName)
        ipv = [str(i) for i in san.get_values_for_type(x509.IPAddress)]
        if 'localhost' not in dns or '127.0.0.1' not in ipv:
            problems.append('SAN lacks localhost / 127.0.0.1')
        if expect_name and expect_name not in dns:
            problems.append(f'SAN lacks DNS:{expect_name}')
    except x509.ExtensionNotFound:
        problems.append('no SubjectAlternativeName')
    if not isinstance(cert.public_key(), rsa.RSAPublicKey) or cert.public_key().key_size != LEAF_KEY_BITS:
        problems.append(f'key is not RSA {LEAF_KEY_BITS}')
    return problems


def cmd_verify(out: pathlib.Path, names: list[str]) -> int:
    _, ca_cert = load_ca(out)
    if not names:
        names = sorted(p.name for p in out.iterdir() if p.is_dir() and p.name != 'ca')
    rc = 0
    print(f'trust anchor: {out / "ca" / "ca.pem"}  SHA-256 {_fingerprint(ca_cert)}')
    for name in names:
        cert_path = out / name / 'sdccert.pem'
        if not cert_path.exists():
            print(f'FAIL  {name}: {cert_path} missing')
            rc = 1
            continue
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        # key/cert consistency without touching key material output
        key = serialization.load_pem_private_key((out / name / 'userkey.pem').read_bytes(), password=None)
        if key.public_key().public_numbers() != cert.public_key().public_numbers():
            print(f'FAIL  {name}: userkey.pem does not belong to sdccert.pem')
            rc = 1
            continue
        problems = verify_leaf(cert, ca_cert, expect_name=name)
        # all alias files must be byte-identical to the canonical ones
        for kind, canon in (('cert', 'sdccert.pem'), ('key', 'userkey.pem'), ('ca', 'cacert.pem')):
            canon_bytes = (out / name / canon).read_bytes()
            for group in FILE_ALIASES.values():
                for fname in group[kind]:
                    p = out / name / fname.format(name=name)
                    if p.exists() and p.read_bytes() != canon_bytes:
                        problems.append(f'alias {p.name} differs from {canon}')
        if problems:
            rc = 1
            print(f'FAIL  {name}: ' + '; '.join(problems))
        else:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            print(f'OK    {name:13s} chain+EKU+SAN ok; SAN={", ".join(str(v) for v in san)}; '
                  f'expires {cert.not_valid_after_utc:%Y-%m-%d}')
    return rc


# ---------------------------------------------------------------- handshake self-test
def _handshake(server_dir: pathlib.Path, client_cert: pathlib.Path, client_key: pathlib.Path,
               client_ca: pathlib.Path, server_ca: pathlib.Path) -> tuple[str | None, str | None, str | None]:
    """Run one mutual-TLS handshake over 127.0.0.1.

    Returns (server_error, client_error, peer_cn_seen_by_server).
    """
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(certfile=str(server_dir / 'sdccert.pem'), keyfile=str(server_dir / 'userkey.pem'))
    srv_ctx.load_verify_locations(cafile=str(server_ca))
    srv_ctx.verify_mode = ssl.CERT_REQUIRED

    cli_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cli_ctx.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))
    cli_ctx.load_verify_locations(cafile=str(client_ca))
    cli_ctx.verify_mode = ssl.CERT_REQUIRED
    cli_ctx.check_hostname = True

    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.bind(('127.0.0.1', 0))
    lsock.listen(1)
    port = lsock.getsockname()[1]
    result: dict[str, str | None] = {'server_error': None, 'client_error': None, 'peer_cn': None}

    def server() -> None:
        try:
            conn, _ = lsock.accept()
            conn.settimeout(5)
            with srv_ctx.wrap_socket(conn, server_side=True) as tls:
                peer = tls.getpeercert()
                for rdn in peer.get('subject', ()):
                    for k, v in rdn:
                        if k == 'commonName':
                            result['peer_cn'] = v
                data = tls.recv(16)
                tls.sendall(b'pong' if data else b'')
        except Exception as exc:  # noqa: BLE001
            result['server_error'] = f'{type(exc).__name__}: {exc}'
        finally:
            lsock.close()

    th = threading.Thread(target=server, daemon=True)
    th.start()
    try:
        raw = socket.create_connection(('127.0.0.1', port), timeout=5)
        with cli_ctx.wrap_socket(raw, server_hostname='localhost') as tls:
            tls.sendall(b'ping')
            tls.recv(16)          # with TLS 1.3 the server's alert arrives here, not in the handshake
    except Exception as exc:  # noqa: BLE001
        result['client_error'] = f'{type(exc).__name__}: {exc}'
    th.join(timeout=10)
    return result['server_error'], result['client_error'], result['peer_cn']


def cmd_selftest(out: pathlib.Path, server: str, client: str) -> int:
    rc = 0
    ca_pem = out / 'ca' / 'ca.pem'
    server_dir, client_dir = out / server, out / client
    for d in (server_dir, client_dir):
        if not d.exists():
            print(f'ERROR: {d} missing')
            return 1

    print(f'[b] mutual TLS over 127.0.0.1: server={server} (CERT_REQUIRED, trusts ca.pem), client={client}')
    s_err, c_err, peer_cn = _handshake(server_dir, client_dir / 'sdccert.pem', client_dir / 'userkey.pem',
                                       ca_pem, ca_pem)
    if s_err or c_err or peer_cn != client:
        rc = 1
        print(f'    FAIL  server_error={s_err!r} client_error={c_err!r} peer_cn={peer_cn!r}')
    else:
        print(f'    PASS  handshake ok, server saw client certificate CN={peer_cn!r}, hostname check on')

    print(f'[c] same handshake, client certificate from a FOREIGN temporary CA (must fail: unknown CA)')
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='sdc_foreign_ca_'))
    try:
        f_key, f_cert = make_ca('Foreign Test CA (temporary)')
        l_key, l_cert = make_leaf(client, f_key, f_cert, [])
        _write_private(tmp / 'foreign_client.key', l_key)
        _write_cert(tmp / 'foreign_client.pem', l_cert)
        _write_cert(tmp / 'foreign_ca.pem', f_cert)
        # client trusts the REAL CA (so it accepts the server) but presents a FOREIGN-signed certificate
        s_err, c_err, peer_cn = _handshake(server_dir, tmp / 'foreign_client.pem', tmp / 'foreign_client.key',
                                           ca_pem, ca_pem)
        txt = f'{s_err or ""} | {c_err or ""}'.lower()
        if peer_cn is None and ('unknown ca' in txt or 'unknown_ca' in txt or 'certificate verify failed' in txt):
            print(f'    PASS  rejected as expected. server: {s_err}')
            print(f'          client: {c_err}')
        else:
            rc = 1
            print(f'    FAIL  expected unknown-CA rejection; server_error={s_err!r} client_error={c_err!r} peer_cn={peer_cn!r}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f'    temporary folder {tmp} removed: {not tmp.exists()}')
    return rc


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent.parent / 'pki',
                    help='PKI folder (default: <repo>/pki)')
    ap.add_argument('--ip', nargs='*', default=None, metavar='IP',
                    help='IP addresses to put into every SAN (in addition to name, localhost, 127.0.0.1)')
    ap.add_argument('--force', action='store_true', help='overwrite an existing CA / participant')
    ap.add_argument('--participants', nargs='*', default=None, metavar='NAME[:ROLE]',
                    help=f'override the default set {list(DEFAULT_PARTICIPANTS)}; ROLE = provider|consumer')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--add', metavar='NAME', help='issue one more participant with the existing CA')
    mode.add_argument('--verify', nargs='*', metavar='NAME', help='verify participant certificates against the CA')
    mode.add_argument('--selftest', action='store_true', help='mutual-TLS handshake tests over localhost')
    ap.add_argument('--role', choices=('provider', 'consumer'), default='provider', help='role for --add')
    ap.add_argument('--server', default='spo2', help='server participant for --selftest')
    ap.add_argument('--client', default='dashboard', help='client participant for --selftest')
    args = ap.parse_args(argv)

    out: pathlib.Path = args.out
    if args.add:
        return cmd_add(out, args.add, args.role, args.ip, args.force)
    if args.verify is not None:
        return cmd_verify(out, args.verify)
    if args.selftest:
        return cmd_selftest(out, args.server, args.client)

    participants = dict(DEFAULT_PARTICIPANTS)
    if args.participants:
        participants = {}
        for item in args.participants:
            n, _, r = item.partition(':')
            participants[n] = r or 'provider'
    return cmd_issue(out, participants, args.ip or [], args.force)


if __name__ == '__main__':
    sys.exit(main())
