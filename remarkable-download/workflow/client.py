"""
client.py
=========
SSH / SCP transport layer for communicating with the reMarkable tablet.

Two classes:

* :class:`RMClient`      — low-level SSH connection wrapper (Paramiko + SCP).
* :class:`FileDownloader` — high-level per-document asset downloader that
  uses an :class:`RMClient` to pull exactly the files needed for one rebuild.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


import io
import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import paramiko
from scp import SCPClient

from config import (
    RM_ROOT,
    SSH_BANNER_TIMEOUT,
    SSH_CONNECT_TIMEOUT,
    SCP_TIMEOUT,
    PROFILES,
    PROFILE_ORDER,
    MANUAL_HOST,
    MANUAL_USER,
    MANUAL_PASSWORD,
    MANUAL_SSH_KEY,
    MANUAL_PORT,
)

log = logging.getLogger("rm-rebuilder.client")


# ---------------------------------------------------------------------------
# RMClient
# ---------------------------------------------------------------------------

class RMClient:
    """SSH/SCP interface to the reMarkable tablet.

    Wraps a :class:`paramiko.SSHClient` and an :class:`scp.SCPClient`.
    Read the ~/.ssh/config file and connect, fallback to hardcoded config if NONE is found

    Attributes:
        username:   SSH login username (default ``"root"``).
        password:   SSH password; ``None`` for key-based authentication.
        key_path:   Path to an SSH private key file in PEM or OpenSSH format.

    Example::

        client = RMClient()
        client.connect()
        client.download("/home/root/.../file.rm", "/tmp/file.rm")
        client.disconnect()
    """
    def __init__(
        self,
        username: str = "root",
        password: Optional[str] = None,
        key_path: Optional[str] = None,
    ) -> None:
        self.username   = username
        self.password   = password
        self.key_path   = key_path

        self._ssh: Optional[paramiko.SSHClient] = None
        self._scp: Optional[SCPClient]          = None

    def _ensure_connected(self) -> None:
        """Raise a clear error if SSH/SCP handles are not initialized."""
        if self._ssh is None or self._scp is None:
            raise RuntimeError(
                "RMClient is not connected. Call connect() before remote file operations."
            )

    def resolve_ssh_alias(self,alias:str) -> dict | None:
        """
        Input: alias name
        look up alias in ~/.ssh/config
        Returns: hostname, user, port, identityfile (optional)
        """
        import paramiko
        ssh_config_path = Path.home() / ".ssh" / "config"
        if not ssh_config_path.exists():
            return None
        cfg = paramiko.SSHConfig()
        with ssh_config_path.open() as f:
            cfg.parse(f)
        host_cfg = cfg.lookup(alias)
        # paramiko returns the alias itself as 'hostname' when there is no match
        if host_cfg.get("hostname", alias) == alias and alias not in ssh_config_path.read_text():
            return None
        params: dict = {
            "hostname": host_cfg.get("hostname", alias),
            "user":     host_cfg.get("user", "root"),
            "port":     int(host_cfg.get("port", 22)),
        }
        identity = host_cfg.get("identityfile")
        if identity:
            raw = identity[0] if isinstance(identity, list) else identity
            params["identityfile"] = str(Path(raw).expanduser())
        return params

    def connect(self, profile: str | None) -> tuple["paramiko.SSHClient", "paramiko.SFTPClient", str]:
        """
        Establish an SSH connection to the reMarkable.

        Tries each alias in *profile* order (or a single alias if *profile* is set).
        Falls back to MANUAL_HOST if all alias attempts fail.

        Args:
            profile: Optional[str] -> A specific profile to connect to.
        Returns: 
            (ssh_client, sftp_client, alias_used).
        """
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        aliases = [PROFILES[profile]] if profile else [PROFILES[p] for p in PROFILE_ORDER]
        last_error: Exception | None = None

        for alias in aliases:
            params = self.resolve_ssh_alias(alias)
            if not params:
                log.debug("SSH alias %r not found in ~/.ssh/config, skipping.", alias)
                continue
            try:
                kwargs: dict = {
                    "hostname": params["hostname"],
                    "port":     params["port"],
                    "username": params["user"],
                    "timeout":  8,
                }
                if "identityfile" in params:
                    kwargs["key_filename"] = params["identityfile"]
                elif MANUAL_PASSWORD:
                    kwargs["password"] = MANUAL_PASSWORD

                log.info("Trying %s (%s)…", alias, params["hostname"])
                client.connect(**kwargs)
                sftp = client.open_sftp()
                self._ssh = client
                self._scp = SCPClient(client.get_transport(), socket_timeout=SCP_TIMEOUT)
                log.info("Connected via %s.", alias)
                return client, sftp, alias
            except Exception as exc:
                log.debug("  %s failed: %s", alias, exc)
                last_error = exc

        # Manual fallback
        if MANUAL_HOST:
            try:
                log.info("Trying manual host %s…", MANUAL_HOST)
                kwargs = {
                    "hostname": MANUAL_HOST,
                    "port":     MANUAL_PORT,
                    "username": MANUAL_USER,
                    "timeout":  8,
                }
                if MANUAL_SSH_KEY:
                    kwargs["key_filename"] = str(MANUAL_SSH_KEY)
                elif MANUAL_PASSWORD:
                    kwargs["password"] = MANUAL_PASSWORD
                client.connect(**kwargs)
                sftp = client.open_sftp()
                self._ssh = client
                self._scp = SCPClient(client.get_transport(), socket_timeout=SCP_TIMEOUT)
                return client, sftp, f"manual:{MANUAL_HOST}"
            except Exception as exc:
                last_error = exc

        log.error("Could not connect to reMarkable.  Last error: %s", last_error)
        sys.exit(1)
    
    def disconnect(self) -> None:
        """Close the SCP channel and the underlying SSH connection."""
        if self._scp:
            self._scp.close()
        if self._ssh:
            self._ssh.close()
        log.info("Disconnected.")

    #=====================================================
    # Remote file operations
    #=====================================================
    def file_exists(self, remote_path: str) -> bool:
        """Check whether a path exists on the tablet filesystem.

        Args:
            remote_path: Absolute path on the tablet.

        Returns:
            ``True`` if the file exists, ``False`` otherwise.
        """
        self._ensure_connected()
        _, stdout, _ = self._ssh.exec_command(
            f'test -f "{remote_path}" && echo yes || echo no'
        )
        return stdout.read().decode().strip() == "yes"

    def download(self, remote_path: str, local_path: str) -> None:
        """Download a single file from the tablet via SCP.

        Args:
            remote_path: Absolute path on the tablet.
            local_path:  Destination path on the local machine.
        """
        self._ensure_connected()
        log.debug("SCP ← %s", remote_path)
        self._scp.get(remote_path, local_path)

    def download_directory(self, remote_dir: str, local_dir: str) -> None:
        """Recursively download an entire remote directory via SCP.

        Args:
            remote_dir: Absolute path to the remote directory.
            local_dir:  Destination directory on the local machine.
        """
        self._ensure_connected()
        log.debug("SCP dir ← %s", remote_dir)
        self._scp.get(remote_dir, local_dir, recursive=True)

    def list_dir(self, remote_dir: str) -> List[str]:
        """List filenames inside a remote directory (names only, not full paths).

        Args:
            remote_dir: Absolute path to the remote directory.

        Returns:
            A list of filename strings.  Returns an empty list if the
            directory is absent or empty.
        """
        self._ensure_connected()
        _, stdout, _ = self._ssh.exec_command(
            f'ls "{remote_dir}" 2>/dev/null'
        )
        output = stdout.read().decode().strip()
        return [x for x in output.splitlines() if x] if output else []


class FileDownloader:
    """
    Downloads all files required to reconstruct specified documents from the reMarkable tablet.

    This class uses a connected :class:`RMClient` to selectively fetch:

        <uuid>.metadata         (required — document name, type)
        <uuid>.content          (required — page list with redirect indices)
        <uuid>.pdf              (optional — absent for pure notebooks)
        <uuid>/<page_uuid>.rm   (one per page — stroke binary data)

    Attributes:
        client:   RMClient instance connected to the tablet.
        work_dir: Local directory for downloaded files.
        uuids:    List of document UUIDs to download.
    """

    def __init__(
        self,
        client: RMClient,
        work_dir: Path,
        uuids: List[str],
    ) -> None:
        """
        Initialize the downloader for one or more documents.

        Args:
            client:   A connected RMClient instance.
            work_dir: Directory where files will be downloaded.
            uuids:    List of document UUIDs to download.
        """
        self.client = client
        self.work_dir = work_dir
        self.uuids = uuids
        self._remotes = [f"{RM_ROOT}/{uuid}" for uuid in uuids]

    def download_all(self) -> None:
        """Execute all download steps for all specified documents.

        Downloads ``.metadata``, ``.content``, the optional ``.pdf``, and
        every ``.rm`` stroke file found in the ``<uuid>/`` subdirectory for each uuid in uuids.

        Raises:
            FileNotFoundError: If ``.metadata`` or ``.content`` are absent on
                the tablet for any uuid.
        """
        for uuid in self.uuids:
            self._get(uuid, ".metadata", required=True)
            self._get(uuid, ".content", required=True)
            self._get(uuid, ".pdf", required=False)
            self._download_rm_folder(uuid)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get(self, uuid: str, suffix: str, *, required: bool) -> None:
        """Download ``<uuid><suffix>`` from the tablet.

        Args:
            uuid:     Document UUID
            suffix:   File extension including the leading dot
                (e.g. ``".metadata"``).
            required: If ``True`` and the remote file is absent, raises
                :exc:`FileNotFoundError`.

        Raises:
            FileNotFoundError: When *required* is ``True`` and the remote
                file does not exist.
        """
        remote = f"{RM_ROOT}/{uuid}{suffix}"
        local  = str(self.work_dir / f"{uuid}{suffix}")
        if self.client.file_exists(remote):
            self.client.download(remote, local)
        elif required:
            raise FileNotFoundError(
                f"Required remote file missing: {remote}"
            )
        else:
            log.debug("Optional file absent (skipped): %s", remote)

    def _download_rm_folder(self, uuid: str) -> None:
        """Download all ``.rm`` stroke files from the ``<uuid>/`` subdirectory.

        Creates the mirror directory under :attr:`work_dir` and downloads each
        ``.rm`` file individually (avoids SCP recursive issues on some hosts).
        Logs a warning and returns silently if no ``.rm`` files are present.
        """
        remote_dir = f"{RM_ROOT}/{uuid}"
        local_dir  = str(self.work_dir / uuid)
        os.makedirs(local_dir, exist_ok=True)

        rm_files = [
            f for f in self.client.list_dir(remote_dir)
            if f.endswith(".rm")
        ]
        if not rm_files:
            log.warning("No .rm files found inside %s", remote_dir)
            return

        for rm_file in rm_files:
            self.client.download(
                f"{remote_dir}/{rm_file}",
                os.path.join(local_dir, rm_file),
            )
        log.info("Downloaded %d .rm file(s) for %s.", len(rm_files), uuid)