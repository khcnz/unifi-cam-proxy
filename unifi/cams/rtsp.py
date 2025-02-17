import argparse
import logging
import subprocess
import tempfile
from pathlib import Path

from aiohttp import web

from PIL import Image

from unifi.cams.base import UnifiCamBase


class RTSPCam(UnifiCamBase):
    def __init__(self, args: argparse.Namespace, logger: logging.Logger) -> None:
        super().__init__(args, logger)
        self.args = args
        self.event_id = 0
        self.snapshot_dir = tempfile.mkdtemp()
        self.snapshot_stream = None
        self.runner = None
        if not self.args.snapshot_url:
            self.start_snapshot_stream()

    @classmethod
    def add_parser(cls, parser: argparse.ArgumentParser) -> None:
        super().add_parser(parser)
        parser.add_argument("--source", "-s", required=True, help="Stream source")
        parser.add_argument(
            "--http-api",
            default=0,
            type=int,
            help="Specify a port number to enable the HTTP API (default: disabled)",
        )
        parser.add_argument(
            "--snapshot-url",
            "-i",
            default=None,
            type=str,
            required=False,
            help="HTTP endpoint to fetch snapshot image from",
        )

    def start_snapshot_stream(self) -> None:
        if not self.snapshot_stream or self.snapshot_stream.poll() is not None:
            cmd = (
                f"ffmpeg -nostdin -y -re -rtsp_transport {self.args.rtsp_transport} "
                f'-i "{self .args.source}" '
                "-vf fps=1 "
                f"-update 1 {self.snapshot_dir}/screen.jpg"
            )
            self.logger.info(f"Spawning stream for snapshots: {cmd}")
            self.snapshot_stream = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True
            )

    async def get_snapshot(self) -> Path:
        img_file = Path(self.snapshot_dir, "screen.jpg")
        if self.args.snapshot_url:
            img_file_fullres = Path(self.snapshot_dir, "screen_fullres.jpg")
            self.logger.info(f"Downloading snapshot from {self.args.snapshot_url}")
            if await self.fetch_to_file(self.args.snapshot_url, img_file_fullres):
                size = 1920, 1080
                self.logger.info(f"Resizing image to {size}")
                with Image.open(img_file_fullres) as im:
                    im.thumbnail(size)
                    im.save(img_file, "JPEG")
            else:
                 self.logger.info(f"Could not download screenshot")
        else:
            self.start_snapshot_stream()
        return img_file

    async def run(self) -> None:
        if self.args.http_api:
            self.logger.info(f"Enabling HTTP API on port {self.args.http_api}")

            app = web.Application()

            async def start_motion(request):
                self.logger.debug("Starting motion")
                await self.trigger_motion_start()
                return web.Response(text="ok")

            async def stop_motion(request):
                self.logger.debug("Starting motion")
                await self.trigger_motion_stop()
                return web.Response(text="ok")

            app.add_routes([web.get("/start_motion", start_motion)])
            app.add_routes([web.get("/stop_motion", stop_motion)])

            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, port=self.args.http_api)
            await site.start()

    async def close(self) -> None:
        await super().close()
        if self.runner:
            await self.runner.cleanup()

        if self.snapshot_stream:
            self.snapshot_stream.kill()

    def get_stream_source(self, stream_index: str) -> str:
        return self.args.source
