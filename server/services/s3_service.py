"""
S3 API Service
~~~~~~~~~~~~~~

Replaces the boto3 S3 client with HTTP calls to the lightweight S3 proxy API
running at http://172.27.222.7:8080.

API Endpoints Used
------------------
  GET  /list?prefix=<prefix>        — list objects in the bucket
  GET  /download?key=<key>          — stream-download a single object
  POST /upload?key=<key>            — upload a file (multipart/form-data)
  GET  /find?filename=<name>        — search objects by filename

Configuration (read from environment)
--------------------------------------
  S3_API_BASE_URL  : Base URL of the S3 proxy API  (default: http://172.27.222.7:8080)
  S3_BUCKET        : Bucket name — used only for logging; the proxy manages the bucket.
  S3_KEY           : Object key (path inside bucket), e.g. "dynamic_step_patterns.xlsx"
"""

import io
import os

import requests

from utils import setup_logger

logger = setup_logger(__name__)

DEFAULT_API_BASE = "http://172.27.222.7:8080"

# Timeout (seconds) for the download request.  Large Excel files may take
# a few seconds — 30 s gives plenty of headroom without hanging forever.
DOWNLOAD_TIMEOUT = 30

# Chunk size for streaming the download body into memory.
CHUNK_SIZE = 256 * 1024  # 256 KB


class S3Service:
    """
    Downloads / uploads Excel files via the S3 proxy HTTP API.

    All public methods are static — no instance state is needed.
    A new requests.Session is created per call so that connection reuse is
    scoped to a single operation (safe for multi-worker deployments).
    """

    @staticmethod
    def _base_url() -> str:
        """Return the configured API base URL (no trailing slash)."""
        return os.getenv("S3_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")

    @staticmethod
    def _get(path: str, params: dict = None, stream: bool = False) -> requests.Response:
        """
        Fire a GET request to the S3 proxy API.

        Raises requests.RequestException on network / HTTP errors.
        """
        url = f"{S3Service._base_url()}{path}"
        logger.debug("[S3-API] GET %s | params=%s", url, params)
        resp = requests.get(url, params=params, stream=stream, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        return resp

    # Public API  (same interface as the previous boto3 implementation)

    @staticmethod
    def download_excel_as_bytes(
        bucket: str,
        key: str,
    ) -> io.BytesIO:
        """
        Download an S3 object entirely into memory via GET /download.

        Parameters
        ----------
        bucket : str
            S3 bucket name — used only for log messages; the proxy server
            manages which bucket to use.
        key : str
            Object key inside the bucket
            (e.g. "templates/dynamic_step_patterns.xlsx").

        Returns
        -------
        io.BytesIO
            Buffer seeked to position 0, ready to pass to openpyxl.load_workbook().

        Raises
        ------
        ValueError
            If key is empty.
        requests.RequestException
            If the HTTP request fails (network error, 4xx/5xx, etc.).
        """
        if not key:
            raise ValueError("S3_KEY is not configured")

        logger.info(
            "[S3-API] Downloading | bucket=%s | key=%s | api=%s",
            bucket or "(proxy-managed)",
            key,
            S3Service._base_url(),
        )

        buffer = io.BytesIO()

        try:
            resp = S3Service._get("/download", params={"key": key}, stream=True)

            content_length = resp.headers.get("Content-Length", "?")
            content_type = resp.headers.get("Content-Type", "?")

            logger.info(
                "[S3-API] Download response | size=%s bytes | content-type=%s",
                content_length,
                content_type,
            )

            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    buffer.write(chunk)

        except requests.HTTPError as exc:
            logger.error(
                "[S3-API] HTTP error during download | status=%s | key=%s | url=%s",
                exc.response.status_code if exc.response is not None else "?",
                key,
                S3Service._base_url(),
            )
            raise

        except requests.ConnectionError:
            logger.error(
                "[S3-API] Connection refused — is the S3 proxy running at %s?",
                S3Service._base_url(),
            )
            raise

        except requests.Timeout:
            logger.error(
                "[S3-API] Download timed out after %ss | key=%s",
                DOWNLOAD_TIMEOUT,
                key,
            )
            raise

        except requests.RequestException as exc:
            logger.error("[S3-API] Download failed | %s | key=%s", exc, key)
            raise

        buffer.seek(0)
        downloaded_bytes = buffer.getbuffer().nbytes

        logger.info(
            "[S3-API] Download complete | bytes=%d | key=%s",
            downloaded_bytes,
            key,
        )

        return buffer

    @staticmethod
    def download_from_env() -> io.BytesIO:
        """
        Convenience wrapper that reads bucket and key from environment variables.

        Reads:
            S3_BUCKET  — bucket name (informational only for this proxy)
            S3_KEY     — object key

        Raises ValueError if S3_KEY is not set.
        """
        bucket = os.getenv("S3_BUCKET", "").strip()
        key = os.getenv("S3_KEY", "").strip()

        if not key:
            raise ValueError(
                "S3_KEY environment variable is not set. "
                "Please add it to your .env file."
            )

        return S3Service.download_excel_as_bytes(bucket=bucket, key=key)

    @staticmethod
    def check_object_exists(bucket: str, key: str) -> bool:
        """
        Quickly check whether an object exists via GET /find.

        Returns True if the object is found, False otherwise.
        Never raises — logs warnings on failure.
        """
        if not key:
            logger.warning("[S3-API] check_object_exists called with empty key")
            return False

        # Extract just the filename portion for the /find search
        filename = key.split("/")[-1] if "/" in key else key

        try:
            resp = S3Service._get("/find", params={"filename": filename})
            data = resp.json()
            count = data.get("count", 0)

            if count > 0:
                # Verify an exact key match in the results (not just filename)
                results = data.get("results", [])
                matched = any(r.get("key") == key for r in results)
                if matched:
                    logger.info("[S3-API] Object exists | key=%s", key)
                    return True
                # Filename found but key path differs — treat as not found
                logger.warning(
                    "[S3-API] Filename found but no exact key match | filename=%s key=%s",
                    filename,
                    key,
                )
                return False

            logger.warning("[S3-API] Object not found | key=%s", key)
            return False

        except requests.HTTPError as exc:
            logger.warning(
                "[S3-API] check_object_exists HTTP error | status=%s | key=%s",
                exc.response.status_code if exc.response is not None else "?",
                key,
            )
            return False

        except requests.RequestException as exc:
            logger.warning("[S3-API] check_object_exists request failed | %s | key=%s", exc, key)
            return False

    @staticmethod
    def upload_file(local_path: str, key: str) -> dict:
        """
        Upload a local file to S3 via POST /upload.

        Parameters
        ----------
        local_path : str
            Absolute or relative path to the local file to upload.
        key : str
            Destination object key in the S3 bucket.

        Returns
        -------
        dict
            JSON response from the API (e.g. {"message": "Upload successful", ...}).

        Raises
        ------
        FileNotFoundError
            If local_path does not exist.
        requests.RequestException
            If the HTTP request fails.
        """
        import pathlib

        path = pathlib.Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {local_path}")

        url = f"{S3Service._base_url()}/upload"
        logger.info("[S3-API] Uploading | local=%s | key=%s", local_path, key)

        with open(path, "rb") as fh:
            resp = requests.post(
                url,
                params={"key": key},
                files={"file": fh},
                timeout=DOWNLOAD_TIMEOUT,
            )

        resp.raise_for_status()
        data = resp.json()

        logger.info(
            "[S3-API] Upload complete | key=%s | response=%s",
            key,
            data,
        )

        return data

    @staticmethod
    def list_files(prefix: str = "") -> dict:
        """
        List objects in the S3 bucket via GET /list.

        Parameters
        ----------
        prefix : str
            Optional prefix to filter results (e.g. "templates/").

        Returns
        -------
        dict
            JSON response from the API containing bucket name and object list.
        """
        params = {}
        if prefix:
            params["prefix"] = prefix

        try:
            resp = S3Service._get("/list", params=params or None)
            return resp.json()
        except requests.RequestException as exc:
            logger.error("[S3-API] list_files failed | %s", exc)
            raise

    @staticmethod
    def find_file(filename: str) -> dict:
        """
        Search for a file by name via GET /find.

        Parameters
        ----------
        filename : str
            Filename to search for (e.g. "dynamic_step_patterns.xlsx").

        Returns
        -------
        dict
            JSON response with count and results list.
        """
        try:
            resp = S3Service._get("/find", params={"filename": filename})
            return resp.json()
        except requests.RequestException as exc:
            logger.error("[S3-API] find_file failed | %s", exc)
            raise
