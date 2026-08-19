import asyncio
import tempfile
import uuid
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from project_link_fall_response.event_store import EventStore
from project_link_fall_response.fall_http_gateway import FallGatewayApp


class FakeBridge:
    def __init__(self):
        self.dispatched = []
        self.cancelled = []

    def readiness(self):
        return {
            "coordinator_ready": True,
            "camera_ready": True,
            "notification_ready": True,
        }

    def dispatch(self, event):
        self.dispatched.append(event["event_id"])
        return True

    def cancel(self, event_id):
        self.cancelled.append(event_id)


def test_android_http_contract_end_to_end():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "events.sqlite3")
            bridge = FakeBridge()
            application = FallGatewayApp(store, bridge, "secret", Path(directory) / "model.pt").build()
            client = TestClient(TestServer(application))
            await client.start_server()
            try:
                assert (await client.get("/health")).status == 401
                headers = {"X-Fall-Guard-Token": "secret"}
                health = await client.get("/health", headers=headers)
                assert health.status == 200
                event_id = str(uuid.uuid4())
                payload = {
                    "event_id": event_id,
                    "mode": "demo",
                    "occurred_at_ms": 1787131200000,
                    "device_name": "http-test",
                    "cancel_window_ms": 15000,
                    "imu": None,
                }
                created = await client.post("/api/fall", headers=headers, json=payload)
                assert created.status == 202
                assert bridge.dispatched == [event_id]
                duplicate = await client.post("/api/fall", headers=headers, json=payload)
                assert duplicate.status == 200
                assert bridge.dispatched == [event_id]
                queried = await client.get(f"/api/fall/{event_id}", headers=headers)
                queried_payload = await queried.json()
                assert queried_payload["status"] == "accepted"
                assert queried_payload["stage"] == "accepted"
                cancelled = await client.post(
                    f"/api/fall/{event_id}/cancel", headers=headers, json={}
                )
                assert (await cancelled.json())["status"] == "cancelled"
                assert bridge.cancelled == [event_id]
            finally:
                await client.close()

    asyncio.run(scenario())
