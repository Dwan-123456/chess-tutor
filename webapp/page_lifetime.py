"""Stop the desktop service after its last browser page disconnects."""
import asyncio


class PageLifetime:
    def __init__(self, shutdown, grace_s=3.0):
        self.shutdown = shutdown
        self.grace_s = grace_s
        self.pages = set()
        self.pending = None
        self.stopped = False

    def connect(self):
        page = object()
        self.pages.add(page)
        if self.pending:
            self.pending.cancel()
            self.pending = None
        return page

    def disconnect(self, page):
        self.pages.discard(page)
        if not self.pages and not self.stopped and self.pending is None:
            self.pending = asyncio.create_task(self._finish())

    async def _finish(self):
        await asyncio.sleep(self.grace_s)
        if not self.pages and not self.stopped:
            self.stopped = True
            self.shutdown()

    async def close(self):
        if self.pending:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
            self.pending = None
