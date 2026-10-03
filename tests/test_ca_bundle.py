"""Guard the CA roots that `fetch-data` needs to verify the data source (issue #724).

The data source (survey.tmiph.metro.tokyo.lg.jp) is served by a SECOM certificate. Its current
chain ends at Security Communication RootCA2 via SECOM Passport for Web SR 3.0 CA, but SR 3.0 is
discontinued and the renewal due by 2026-10-04 moves to SECOM Passport for Web OV RSA CA 2024,
which chains to SECOM TLS RSA Root CA 2024. requests verifies TLS against the certifi bundle
pinned in uv.lock, and the first certifi release that ships SECOM TLS RSA Root CA 2024 is
2026.7.22; with an older bundle every request fails with CERTIFICATE_VERIFY_FAILED unless the
server also sends the cross-root certificate.

Security Communication RootCA2 is still required by the current chain and the cross-root path,
but browsers plan to end trust in it by 2027-04-15. If certifi drops RootCA2 and this test fails,
check the data source's certificate path first, then revisit the RootCA2 assertion.

Roots are matched by the SHA-256 of their DER encoding, not by label: certifi has changed label
spellings between releases. The check is offline and never connects to the data source.
"""

import hashlib
import re
import ssl
from pathlib import Path

import certifi
import pytest
import requests

# DER SHA-256 fingerprints, identical to the "SHA256 Fingerprint" comments in certifi's cacert.pem.
REQUIRED_ROOTS = {
    "SECOM TLS RSA Root CA 2024": "1435f225c5d252d7a21948cc3ce62aecfa88001e3dd72d1cc3555100eb372f93",
    "Security Communication RootCA2": "513b2cecb810d4cde5dd85391adfc6c2dd60d87bb736d2b521484aa47a0ebef6",
}
PEM_CERTIFICATE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)


def _bundle_fingerprints(bundle_path: str) -> set[str]:
    pem_text = Path(bundle_path).read_text(encoding="utf-8")
    return {hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() for pem in PEM_CERTIFICATE.findall(pem_text)}


def test_requests_default_ca_bundle_is_certifi():
    # Act / Assert: the bundle checked below is the one production requests verifies against.
    assert certifi.where() == requests.utils.DEFAULT_CA_BUNDLE_PATH


@pytest.mark.parametrize(("root_name", "fingerprint"), list(REQUIRED_ROOTS.items()), ids=list(REQUIRED_ROOTS))
def test_certifi_bundle_contains_data_source_root(root_name, fingerprint):
    # Arrange
    bundle_path = certifi.where()

    # Act
    fingerprints = _bundle_fingerprints(bundle_path)

    # Assert: report only the missing root, not the whole fingerprint set.
    if fingerprint not in fingerprints:
        pytest.fail(
            f"{root_name} (SHA-256 {fingerprint}) is missing from certifi {certifi.__version__} "
            f"({bundle_path}); run `uv lock -P certifi==<a release that ships this root>`.",
            pytrace=False,
        )
