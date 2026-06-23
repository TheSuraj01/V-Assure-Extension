"""
S3 API Service
~~~~~~~~~~~~~~

HTTP client for the lightweight S3 proxy API.

  base_url → read from S3_API_BASE_URL environment variable
  key      → read from config.json → s3.key

API Endpoints Used
------------------
  GET  /list?prefix=<prefix>        — list objects in the bucket
  GET  /download?key=<key>          — stream-download a single object
  POST /upload?key=<key>            — upload a file (multipart/form-data)
  GET  /find?filename=<name>        — search objects by filename
"""

import io
import os
import pathlib

import requests

from utils import setup_logger

logger = setup_logger(__name__)


# Timeout (seconds) for download/upload requests.
DOWNLOAD_TIMEOUT = 30
UPLOAD_TIMEOUT = 60  # uploads may be slower for large files

# Chunk size for streaming the download body into memory.
CHUNK_SIZE = 256 * 1024  # 256 KB


class S3Service:
    """
    Downloads / uploads Excel files via the S3 proxy HTTP API.

    base_url is read from the S3_API_BASE_URL env var.
    All public methods are static — no instance state is needed.
    """

    @staticmethod
    def _base_url() -> str:
        """Return the S3 proxy base URL from the S3_API_BASE_URL env var."""
        url = os.getenv("S3_API_BASE_URL", "").strip()
        if not url:
            raise ValueError(
                "S3_API_BASE_URL environment variable is not set. "
                "Please add it to your .env file."
            )
        return url.rstrip("/")

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def download_excel_as_bytes(key: str) -> io.BytesIO:
        """
        Download an S3 object entirely into memory via GET /download.

        Parameters
        ----------
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
            raise ValueError("key must not be empty")

        base = S3Service._base_url()

        logger.info("[S3-API] Downloading | key=%s | api=%s", key, base)

        url = f"{base}/download"
        buffer = io.BytesIO()

        try:
            with requests.Session() as session:
                resp = session.get(
                    url,
                    params={"key": key},
                    stream=True,
                    timeout=DOWNLOAD_TIMEOUT,
                )
                resp.raise_for_status()

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
            status = exc.response.status_code if exc.response is not None else "?"
            logger.error(
                "[S3-API] HTTP error during download | status=%s | key=%s | url=%s",
                status, key, url,
            )
            raise

        except requests.ConnectionError:
            logger.error(
                "[S3-API] Connection refused — is the S3 proxy running at %s?", base,
            )
            raise

        except requests.Timeout:
            logger.error(
                "[S3-API] Download timed out after %ss | key=%s",
                DOWNLOAD_TIMEOUT, key,
            )
            raise

        except requests.RequestException as exc:
            logger.error("[S3-API] Download failed | %s | key=%s", exc, key)
            raise

        buffer.seek(0)
        logger.info(
            "[S3-API] Download complete | bytes=%d | key=%s",
            buffer.getbuffer().nbytes, key,
        )
        return buffer

    @staticmethod
    def check_object_exists(key: str) -> bool:
        """
        Check whether an object exists via GET /find.

        The /find endpoint returns HTTP 404 when no match is found —
        this is treated as False, not an error.

        Returns True if the exact key is found, False otherwise.
        Never raises — logs warnings on failure.
        """
        if not key:
            logger.warning("[S3-API] check_object_exists called with empty key")
            return False

        # /find matches only the final filename segment
        filename = key.split("/")[-1]
        url = f"{S3Service._base_url()}/find"

        try:
            with requests.Session() as session:
                resp = session.get(
                    url,
                    params={"filename": filename},
                    timeout=DOWNLOAD_TIMEOUT,
                )

                if resp.status_code == 404:
                    logger.warning("[S3-API] Object not found | key=%s", key)
                    return False

                resp.raise_for_status()
                data = resp.json()

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            logger.warning(
                "[S3-API] check_object_exists HTTP error | status=%s | key=%s",
                status, key,
            )
            return False

        except requests.RequestException as exc:
            logger.warning(
                "[S3-API] check_object_exists request failed | %s | key=%s", exc, key,
            )
            return False

        # Verify an exact key match (filename may appear under multiple paths)
        results = data.get("results", [])
        matched = any(r.get("key") == key for r in results)

        if matched:
            logger.info("[S3-API] Object exists | key=%s", key)
            return True

        logger.warning(
            "[S3-API] Filename found but no exact key match | filename=%s | key=%s",
            filename, key,
        )
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
            Destination object key (e.g. "templates/report.xlsx").

        Returns
        -------
        dict
            JSON response from the API.

        Raises
        ------
        FileNotFoundError
            If local_path does not exist.
        ValueError
            If key is empty.
        requests.RequestException
            If the HTTP request fails.
        """
        if not key:
            raise ValueError("key must not be empty")

        path = pathlib.Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {local_path}")

        url = f"{S3Service._base_url()}/upload"
        logger.info("[S3-API] Uploading | local=%s | key=%s", local_path, key)

        try:
            with open(path, "rb") as fh:
                with requests.Session() as session:
                    resp = session.post(
                        url,
                        params={"key": key},
                        files={"file": (path.name, fh)},
                        timeout=UPLOAD_TIMEOUT,
                    )
            resp.raise_for_status()

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            logger.error(
                "[S3-API] HTTP error during upload | status=%s | key=%s | url=%s",
                status, key, url,
            )
            raise

        except requests.ConnectionError:
            logger.error(
                "[S3-API] Connection refused during upload — is the proxy running at %s?",
                S3Service._base_url(),
            )
            raise

        except requests.Timeout:
            logger.error(
                "[S3-API] Upload timed out after %ss | key=%s",
                UPLOAD_TIMEOUT, key,
            )
            raise

        data = resp.json()
        logger.info("[S3-API] Upload complete | key=%s | response=%s", key, data)
        return data

    @staticmethod
    def list_files(prefix: str = "") -> dict:
        """
        List objects via GET /list.

        Parameters
        ----------
        prefix : str
            Optional prefix to filter results (e.g. "templates/").

        Returns
        -------
        dict
            JSON response with bucket, prefix, count, and objects list.
        """
        params = {"prefix": prefix} if prefix else None
        url = f"{S3Service._base_url()}/list"

        try:
            with requests.Session() as session:
                resp = session.get(url, params=params, timeout=DOWNLOAD_TIMEOUT)
                resp.raise_for_status()
                return resp.json()

        except requests.RequestException as exc:
            logger.error("[S3-API] list_files failed | %s", exc)
            raise

    @staticmethod
    def find_file(filename: str) -> dict:
        """
        Search for a file by its exact filename via GET /find.

        The /find endpoint returns HTTP 404 when no objects match.
        This method converts that into an empty result dict rather than raising.

        Parameters
        ----------
        filename : str
            Filename to search for (e.g. "dynamic_step_patterns.xlsx").
            Match is case-sensitive and checks only the final key segment.

        Returns
        -------
        dict
            On success : {"filename": ..., "count": N, "results": [...]}
            Not found  : {"filename": ..., "count": 0, "results": []}
        """
        if not filename:
            raise ValueError("filename must not be empty")

        url = f"{S3Service._base_url()}/find"

        try:
            with requests.Session() as session:
                resp = session.get(
                    url,
                    params={"filename": filename},
                    timeout=DOWNLOAD_TIMEOUT,
                )

                if resp.status_code == 404:
                    logger.info("[S3-API] find_file: no results | filename=%s", filename)
                    return {"filename": filename, "count": 0, "results": []}

                resp.raise_for_status()
                return resp.json()

        except requests.RequestException as exc:
            logger.error("[S3-API] find_file failed | %s", exc)
            raise
