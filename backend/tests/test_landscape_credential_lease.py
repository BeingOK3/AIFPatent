from __future__ import annotations

import unittest

from idea.model_client import RuntimeModelConfig
from landscape.credential_lease import CredentialLeaseError, CredentialVault


class LandscapeCredentialLeaseTests(unittest.TestCase):
    def setUp(self):
        self.vault=CredentialVault(lease_ms=1000)
        self.config=RuntimeModelConfig(base_url="https://example.test/v1",api_key="secret-value",model="fixture")

    def test_secret_is_memory_only_and_repr_safe(self):
        self.vault.put("run",self.config)
        lease=self.vault.acquire("run","worker",now=0)
        self.assertNotIn("secret-value",repr(lease))
        self.assertTrue(self.vault.has_credentials("run"))

    def test_active_lease_excludes_other_worker_then_expires(self):
        self.vault.put("run",self.config)
        self.vault.acquire("run","worker-1",now=0)
        with self.assertRaisesRegex(CredentialLeaseError,"another worker"):
            self.vault.acquire("run","worker-2",now=999)
        self.assertEqual(self.vault.acquire("run","worker-2",now=1000).owner,"worker-2")

    def test_restart_or_revoke_requires_credentials_again(self):
        with self.assertRaisesRegex(CredentialLeaseError,"required"):
            CredentialVault().acquire("run","worker")
        self.vault.put("run",self.config); self.vault.revoke("run")
        with self.assertRaises(CredentialLeaseError): self.vault.acquire("run","worker")


if __name__ == "__main__": unittest.main()
