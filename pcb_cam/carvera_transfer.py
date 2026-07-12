"""Transfer finished Carvera NC files through the Controller's Wi-Fi protocol.

Makera's Controller uses a small line-command protocol over TCP followed by a
slightly extended XMODEM upload.  This module deliberately handles only file
management and upload; it never selects or starts a machine job.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import select
import socket
import time
from pathlib import Path, PurePosixPath
from typing import Callable


DEFAULT_HOST = "192.168.1.27"
DEFAULT_PORT = 2222
REMOTE_ROOT = PurePosixPath("/sd")
PACKET_SIZE = 128
RETRY_COUNT = 10
COMMAND_SETTLE_SECONDS = 0.15

SOH = b"\x01"
EOT = b"\x04"
ACK = b"\x06"
NAK = b"\x15"
CAN = b"\x18"
CRC_REQUEST = b"C"
PAD = b"\x1a"


class CarveraTransferError(RuntimeError):
    """Raised when the Carvera does not accept a folder or file transfer."""


def _crc16_xmodem(data: bytes) -> int:
    """Return the CRC-16/XMODEM checksum used by Makera's Controller."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _safe_remote_folder(folder: str) -> PurePosixPath:
    """Validate a folder below /sd that is safe for the Controller command parser."""
    raw = PurePosixPath(folder.replace("\\", "/"))
    if raw.is_absolute() or not raw.parts:
        raise CarveraTransferError("Carvera folder must be a non-empty path below /sd.")

    safe_parts: list[str] = []
    for part in raw.parts:
        if part in ("", ".", "..") or not all(char.isascii() and (char.isalnum() or char in "._-") for char in part):
            raise CarveraTransferError(
                "Carvera folder components may contain only letters, numbers, dots, underscores, and hyphens."
            )
        safe_parts.append(part)
    return REMOTE_ROOT.joinpath(*safe_parts)


def _default_remote_folder(project_name: str) -> str:
    """Return a Controller-safe work-folder name from a local project directory."""
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", project_name).strip("._-")
    if not component or not component[0].isalnum():
        component = "pcb-project"
    return f"PCB-CAM/{component}"


def _file_md5(path: Path) -> bytes:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().encode("ascii")


def _machine_files(project_dir: Path) -> list[Path]:
    cut_dir = project_dir / "cut"
    if not cut_dir.is_dir():
        raise CarveraTransferError(f"Carvera NC output folder was not found: {cut_dir}")
    files = sorted(path for path in cut_dir.rglob("*.nc") if path.is_file())
    if not files:
        raise CarveraTransferError(f"No .nc files were found in: {cut_dir}")

    names = [path.name for path in files]
    if len(names) != len(set(names)):
        raise CarveraTransferError("NC output contains duplicate filenames in nested folders.")
    return files


class CarveraConnection:
    """A narrow client for the Controller-compatible TCP and XMODEM protocol."""

    def __init__(self, host: str, port: int, *, timeout: float = 15.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket: socket.socket | None = None

    def __enter__(self) -> "CarveraConnection":
        try:
            self.socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as error:
            raise CarveraTransferError(
                f"Could not connect to Carvera at {self.host}:{self.port}: {error}"
            ) from error
        return self

    def __exit__(self, *_: object) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def _socket(self) -> socket.socket:
        if self.socket is None:
            raise CarveraTransferError("Carvera connection is not open.")
        return self.socket

    def _send(self, data: bytes) -> None:
        try:
            self._socket().sendall(data)
        except OSError as error:
            raise CarveraTransferError(f"Carvera connection was lost while sending data: {error}") from error

    def _read_byte(self, timeout: float) -> bytes | None:
        try:
            readable, _, _ = select.select([self._socket()], [], [], timeout)
            if not readable:
                return None
            value = self._socket().recv(1)
        except OSError as error:
            raise CarveraTransferError(f"Carvera connection was lost while receiving data: {error}") from error
        if not value:
            raise CarveraTransferError("Carvera closed the connection during a transfer.")
        return value

    def _drain_messages(self) -> None:
        """Discard textual command responses before initiating XMODEM."""
        while True:
            value = self._read_byte(0)
            if value is None:
                return

    def _wait_for(self, expected: set[bytes], timeout: float, *, context: str) -> bytes:
        deadline = time.monotonic() + timeout
        cancellations = 0
        while time.monotonic() < deadline:
            value = self._read_byte(max(0.0, deadline - time.monotonic()))
            if value is None:
                break
            if value in expected:
                return value
            if value == CAN:
                cancellations += 1
                if cancellations >= 2:
                    raise CarveraTransferError(f"Carvera canceled {context}.")
            # Folder commands may report ordinary text before the XMODEM C byte.
        expected_text = ", ".join(repr(value) for value in sorted(expected))
        raise CarveraTransferError(f"Timed out waiting for {context} ({expected_text}).")

    def _command(self, command: str) -> None:
        self._send(command.encode("ascii"))

    def ensure_directory(self, directory: PurePosixPath) -> None:
        if not directory.is_relative_to(REMOTE_ROOT):
            raise CarveraTransferError(f"Refusing to create a folder outside {REMOTE_ROOT}: {directory}")

        current = REMOTE_ROOT
        for component in directory.relative_to(REMOTE_ROOT).parts:
            current /= component
            self._command(f"mkdir {current} -e\n")
            time.sleep(COMMAND_SETTLE_SECONDS)
            self._drain_messages()

    def upload(self, local_file: Path, remote_file: PurePosixPath, progress: Callable[[str], None]) -> None:
        self._drain_messages()
        self._command(f"upload {remote_file}\n")
        self._xmodem_upload(local_file, progress)

    def _xmodem_upload(self, local_file: Path, progress: Callable[[str], None]) -> None:
        start = self._wait_for({CRC_REQUEST, NAK}, self.timeout, context=f"XMODEM start for {local_file.name}")
        crc_mode = start == CRC_REQUEST
        digest = _file_md5(local_file)
        total_size = local_file.stat().st_size
        sent_size = 0
        sequence = 0

        with local_file.open("rb") as stream:
            block = digest
            while block:
                payload = bytes((len(block),)) + block.ljust(PACKET_SIZE, PAD)
                header = SOH + bytes((sequence, 0xFF - sequence))
                if crc_mode:
                    checksum = _crc16_xmodem(payload).to_bytes(2, "big")
                else:
                    checksum = bytes((sum(payload) & 0xFF,))
                packet = header + payload + checksum

                acknowledged = False
                for _ in range(RETRY_COUNT + 1):
                    self._send(packet)
                    response = self._wait_for({ACK, NAK, CAN}, self.timeout, context=f"acknowledgement for block {sequence}")
                    if response == ACK:
                        acknowledged = True
                        break
                    if response == CAN:
                        next_value = self._read_byte(1.0)
                        if next_value == CAN:
                            raise CarveraTransferError(f"Carvera canceled upload of {local_file.name}.")
                if not acknowledged:
                    self._send(CAN * 3)
                    raise CarveraTransferError(f"Carvera did not acknowledge block {sequence} of {local_file.name}.")

                if sequence:
                    sent_size += len(block)
                    progress(f"upload: {local_file.name} {sent_size}/{total_size} bytes")
                sequence = (sequence + 1) % 256
                block = stream.read(PACKET_SIZE)

        for _ in range(RETRY_COUNT + 1):
            self._send(EOT)
            response = self._wait_for({ACK, NAK, CAN}, self.timeout, context=f"final acknowledgement for {local_file.name}")
            if response == ACK:
                return
            if response == CAN:
                next_value = self._read_byte(1.0)
                if next_value == CAN:
                    raise CarveraTransferError(f"Carvera canceled upload of {local_file.name}.")
        self._send(CAN * 3)
        raise CarveraTransferError(f"Carvera did not finish upload of {local_file.name}.")


def transfer_project(
    project_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    remote_folder: str | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] = print,
) -> PurePosixPath:
    """Create the remote work folder and transfer every finished ``cut/*.nc`` file."""
    files = _machine_files(project_dir)
    folder = remote_folder or _default_remote_folder(project_dir.name)
    remote_dir = _safe_remote_folder(folder)
    progress(f"Carvera work folder: {remote_dir}")
    for file in files:
        progress(f"Carvera file: {file.name}")
    if dry_run:
        progress("Carvera transfer dry run: no folders or files were sent.")
        return remote_dir

    with CarveraConnection(host, port) as carvera:
        progress(f"Carvera connected: {host}:{port}")
        carvera.ensure_directory(remote_dir)
        for file in files:
            remote_file = remote_dir / file.name
            progress(f"Carvera upload: {file.name} -> {remote_file}")
            carvera.upload(file, remote_file, progress)
    progress(f"Carvera transfer complete: {remote_dir}")
    return remote_dir


def command_carvera_upload(args: argparse.Namespace) -> int:
    # Import here to keep this protocol module independent of FlatCAM imports.
    from .flatcam_project import host_path

    project_dir = host_path(args.project_dir).resolve()
    transfer_project(
        project_dir,
        host=args.host,
        port=args.port,
        remote_folder=args.remote_folder,
        dry_run=args.dry_run,
    )
    return 0


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    upload = subparsers.add_parser(
        "carvera-upload",
        help="create a Carvera work folder and upload finished cut/*.nc files",
    )
    upload.add_argument("project_dir", help="PCB project directory containing cut/*.nc files")
    upload.add_argument("--host", default=DEFAULT_HOST, help=f"Carvera IP or hostname (default: {DEFAULT_HOST})")
    upload.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Carvera Controller TCP port (default: {DEFAULT_PORT})")
    upload.add_argument(
        "--remote-folder",
        help="folder below /sd; defaults to PCB-CAM/<project-folder-name>",
    )
    upload.add_argument("--dry-run", action="store_true", help="validate files and show the planned remote paths without connecting")
    upload.set_defaults(func=command_carvera_upload)
