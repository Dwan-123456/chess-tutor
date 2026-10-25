"""Desktop runner with graceful shutdown when the last browser page closes."""
import uvicorn
from .server import app


def main():
    server = uvicorn.Server(uvicorn.Config(
        app, host='127.0.0.1', port=8765, timeout_graceful_shutdown=5))

    def shutdown():
        server.should_exit = True

    app.state.desktop_shutdown = shutdown
    server.run()


if __name__ == '__main__':
    main()
