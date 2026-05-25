"""
Google Sheets Synchronization Service

Downloads a Google Sheet as xlsx entirely into memory (BytesIO).
No file is ever written to disk — zero disk I/O.

Works for any Google Sheet shared as "Anyone with the link can view".
No API key or OAuth token required.

Approach:
  Step 1: Extract the spreadsheet ID from the sharing URL.
  Step 2: Build the export URL:
          https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx
  Step 3: Stream the xlsx bytes into a BytesIO buffer in RAM.
  Step 4: Return the buffer seeked to 0, ready for openpyxl.load_workbook().
"""

import io
import re

import httpx

from utils import setup_logger

logger = setup_logger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


class GoogleSheetsService:
    """
    Downloads a Google Sheet as xlsx into memory without any API credentials.
    Works for sheets shared as 'Anyone with the link can view'.
    """

    @staticmethod
    def extract_spreadsheet_id(sharing_url: str) -> str:
        """
        Extracts the spreadsheet ID from a Google Sheets sharing URL.

        Handles formats:
          - https://docs.google.com/spreadsheets/d/{ID}/edit?usp=sharing
          - https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0
          - https://docs.google.com/spreadsheets/d/{ID}/pub
          - https://docs.google.com/spreadsheets/d/{ID}
        """
        match = re.search(
            r"/spreadsheets/d/([a-zA-Z0-9_-]+)",
            sharing_url,
        )
        if not match:
            raise ValueError(
                f"Cannot extract spreadsheet ID from URL: {sharing_url}"
            )
        return match.group(1)

    @staticmethod
    def build_export_url(spreadsheet_id: str) -> str:
        """
        Builds the direct export URL for downloading as xlsx.
        """
        return (
            f"https://docs.google.com/spreadsheets/d/"
            f"{spreadsheet_id}/export?format=xlsx"
        )

    @staticmethod
    def download_sheet_as_bytes(sharing_url: str) -> io.BytesIO:
        """
        Downloads a Google Sheet as xlsx entirely into memory.

        Returns:
            io.BytesIO seeked to position 0, ready to pass directly to
            openpyxl.load_workbook(data).

        No file is written to disk.

        Raises:
            ValueError            - invalid URL or private sheet (HTML returned)
            httpx.HTTPStatusError - non-2xx HTTP response
        """
        logger.info(
            "Google Sheets download initiated | url=%s",
            sharing_url,
        )

        spreadsheet_id = GoogleSheetsService.extract_spreadsheet_id(sharing_url)
        export_url = GoogleSheetsService.build_export_url(spreadsheet_id)

        logger.info(
            "Resolved export URL | spreadsheet_id=%s | export_url=%s",
            spreadsheet_id,
            export_url,
        )

        buffer = io.BytesIO()

        with httpx.Client(
            timeout=10.0,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        ) as client:
            with client.stream("GET", export_url) as response:
                if not response.is_success:
                    body = response.read().decode("utf-8", errors="ignore")[:500]
                    logger.error(
                        "Google Sheets download failed | status=%s | body=%s",
                        response.status_code,
                        body,
                    )
                    response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                logger.info("Download content-type: %s", content_type)

                # Guard: private sheet -> Google redirects to an HTML login page
                if "text/html" in content_type:
                    body_preview = response.read().decode("utf-8", errors="ignore")[:500]
                    logger.error(
                        "Google Sheets returned HTML instead of xlsx. "
                        "The sheet may not be publicly shared. Preview: %s",
                        body_preview,
                    )
                    raise ValueError(
                        "Google Sheets returned an HTML page instead of the xlsx file. "
                        "Ensure the sheet is shared as 'Anyone with the link can view'."
                    )

                for chunk in response.iter_bytes(chunk_size=8192):
                    if chunk:
                        buffer.write(chunk)

        buffer.seek(0)

        logger.info(
            "Google Sheet loaded into memory | bytes=%d",
            buffer.getbuffer().nbytes,
        )

        return buffer
