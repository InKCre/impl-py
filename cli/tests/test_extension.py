"""CLI requests and receipts at the Extension management v2 HTTP boundary."""

import json
import unittest

import httpx
from click.testing import CliRunner

from inkcre_cli.command import Invocation
from inkcre_cli.connection import Connection
from inkcre_cli.http import CoreRESTClient
from inkcre_cli.main import cli


class ExtensionSelectionTests(unittest.TestCase):
    def invoke(self, arguments, handler):
        invocation = Invocation()
        client = CoreRESTClient(
            Connection(base_url="http://core.test", jwt_secret="test-only-secret-at-least-32-bytes")
        )
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler))
        invocation._client = client
        try:
            return CliRunner().invoke(cli, ["--json", "extension", *arguments], obj=invocation)
        finally:
            invocation.close()

    def test_select_uses_v2_body_and_optional_target_peer(self):
        for distribution, peer in (("python", None), ("module_federation", "web-peer")):
            with self.subTest(distribution=distribution):
                requests = []

                def handle(request, requests=requests, distribution=distribution):
                    requests.append(request)
                    return httpx.Response(
                        200, json={"distribution": distribution, "enabled": False}
                    )

                arguments = [
                    "select",
                    "inkcre/rss",
                    "--distribution",
                    distribution,
                    "--version",
                    "1.2.3",
                ]
                if peer:
                    arguments += ["--peer", peer]
                result = self.invoke(arguments, handle)
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(len(requests), 1)
                request = requests[0]
                self.assertEqual(request.method, "POST")
                self.assertEqual(request.url.path, "/extensions/inkcre/rss/selection")
                self.assertEqual(dict(request.url.params), {"route_to_peer": peer} if peer else {})
                self.assertEqual(
                    json.loads(request.content), {"distribution": distribution, "version": "1.2.3"}
                )
                self.assertEqual(json.loads(result.output)["enabled"], False)

    def test_unselect_preserves_status_receipt(self):
        receipt = {"peer_id": "web-peer", "extensions": [{"distribution": None, "enabled": False}]}

        def handle(request):
            self.assertEqual(request.method, "DELETE")
            self.assertEqual(request.url.path, "/extensions/inkcre/rss/selection")
            self.assertEqual(dict(request.url.params), {"route_to_peer": "web-peer"})
            return httpx.Response(200, json=receipt)

        result = self.invoke(["unselect", "inkcre/rss", "--peer", "web-peer"], handle)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output), receipt)

    def test_config_schema_comes_from_current_peer_not_shared_identity(self):
        schema = {"type": "object", "properties": {"token": {"type": "string"}}}
        status = {
            "peer_id": "core-peer",
            "extensions": [{"name": "inkcre/rss", "config_schema": schema}],
        }

        def handle(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/extension-management/v2")
            self.assertEqual(
                json.loads(request.content), {"action": "status", "extension": "inkcre/rss"}
            )
            return httpx.Response(200, json=status)

        result = self.invoke(["config", "replace", "inkcre/rss", "--schema"], handle)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output), schema)
        result = self.invoke(["status", "inkcre/rss"], handle)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output), status)


if __name__ == "__main__":
    unittest.main()
