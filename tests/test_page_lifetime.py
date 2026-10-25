import asyncio
import unittest
from webapp.page_lifetime import PageLifetime


class PageLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_page_disconnect_stops_once(self):
        calls = []
        lifetime = PageLifetime(lambda: calls.append(True), grace_s=0.01)
        first, second = lifetime.connect(), lifetime.connect()
        lifetime.disconnect(first)
        await asyncio.sleep(0.02)
        self.assertEqual(calls, [])
        lifetime.disconnect(second)
        await asyncio.sleep(0.03)
        self.assertEqual(calls, [True])
        lifetime.disconnect(second)
        await asyncio.sleep(0.02)
        self.assertEqual(calls, [True])
        await lifetime.close()

    async def test_refresh_reconnect_cancels_shutdown(self):
        calls = []
        lifetime = PageLifetime(lambda: calls.append(True), grace_s=0.02)
        old = lifetime.connect()
        lifetime.disconnect(old)
        new = lifetime.connect()
        await asyncio.sleep(0.04)
        self.assertEqual(calls, [])
        lifetime.disconnect(new)
        await asyncio.sleep(0.04)
        self.assertEqual(calls, [True])
        await lifetime.close()

    async def test_no_page_and_service_cleanup(self):
        calls = []
        lifetime = PageLifetime(lambda: calls.append(True), grace_s=0.01)
        await asyncio.sleep(0.02)
        self.assertEqual(calls, [])
        lifetime.disconnect(lifetime.connect())
        await lifetime.close()
        await asyncio.sleep(0.02)
        self.assertEqual(calls, [])
