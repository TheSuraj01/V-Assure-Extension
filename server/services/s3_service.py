"""
AWS S3 Service
~~~~~~~~~~~~~~

Replaces Google Sheets as the source of the dynamic step-patterns Excel file.

Responsibilities:
- Download an Excel (.xlsx) object from a private S3 bucket entirely into
  memory (io.BytesIO) — zero disk I/O.
- Return the buffer seeked to position 0, ready for openpyxl.load_workbook().

Credentials are resolved via the standard boto3 credential chain:
  1. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables  (recommended)
  2. ~/.aws/credentials file
  3. IAM instance profile / ECS task role  (for cloud deployments)

Configuration (read from environment):
  AWS_ACCESS_KEY_ID       : AWS access key  (or use IAM role)
  AWS_SECRET_ACCESS_KEY   : AWS secret key  (or use IAM role)
  AWS_REGION              : AWS region, e.g. "us-east-1"  (default: us-east-1)
  S3_BUCKET               : S3 bucket name
  S3_KEY                  : Object key (path inside bucket), e.g. "dynamic_step_patterns.xlsx"
"""

import io
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from utils import setup_logger

logger = setup_logger(__name__)


class S3Service:
    """
    Downloads an Excel file from AWS S3 into memory.

    All public methods are static — no instance state is needed.
    A new boto3 client is created per call so that credential rotation
    (e.g., via IAM role) is picked up automatically.
    """

    @staticmethod
    def _make_client():
        """
        Build a boto3 S3 client from environment variables.

        Falls back gracefully: if AWS_ACCESS_KEY_ID is not set, boto3 uses
        the default credential chain (IAM role, instance profile, etc.).

        Supports S3_ENDPOINT_URL for S3-compatible services (MinIO, etc.).
        """
        region = os.getenv("AWS_REGION", "us-east-1").strip()
        access_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
        endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip()

        kwargs = {"region_name": region}
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url

        return boto3.client("s3", **kwargs)

    @staticmethod
    def download_excel_as_bytes(
        bucket: str,
        key: str,
    ) -> io.BytesIO:
        """
        Download an S3 object entirely into memory.

        Parameters
        ----------
        bucket : str
            S3 bucket name (e.g. "my-vassure-bucket").
        key : str
            Object key inside the bucket (e.g. "templates/dynamic_step_patterns.xlsx").

        Returns
        -------
        io.BytesIO
            Buffer seeked to position 0, ready to pass to openpyxl.load_workbook().

        Raises
        ------
        ValueError
            If bucket or key are empty.
        ClientError / BotoCoreError
            If the S3 request fails (permissions, missing object, network, etc.).
        """
        if not bucket:
            raise ValueError("S3_BUCKET is not configured")
        if not key:
            raise ValueError("S3_KEY is not configured")

        logger.info(
            "[S3] Downloading Excel | bucket=%s | key=%s",
            bucket,
            key,
        )

        client = S3Service._make_client()
        buffer = io.BytesIO()

        try:
            response = client.get_object(Bucket=bucket, Key=key)
            content_length = response.get("ContentLength", "?")
            content_type = response.get("ContentType", "?")

            logger.info(
                "[S3] Object found | size=%s bytes | content-type=%s",
                content_length,
                content_type,
            )

            # Stream body in 256 KB chunks to handle large files efficiently
            body = response["Body"]
            chunk_size = 256 * 1024  # 256 KB
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                buffer.write(chunk)

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            error_msg = exc.response.get("Error", {}).get("Message", str(exc))
            logger.error(
                "[S3] Download failed | code=%s | message=%s | bucket=%s | key=%s",
                error_code,
                error_msg,
                bucket,
                key,
            )
            raise

        except BotoCoreError as exc:
            logger.error(
                "[S3] BotoCore error during download | %s | bucket=%s | key=%s",
                exc,
                bucket,
                key,
            )
            raise

        buffer.seek(0)
        downloaded_bytes = buffer.getbuffer().nbytes

        logger.info(
            "[S3] Download complete | bytes=%d | bucket=%s | key=%s",
            downloaded_bytes,
            bucket,
            key,
        )

        return buffer

    @staticmethod
    def download_from_env() -> io.BytesIO:
        """
        Convenience wrapper that reads bucket and key from environment variables.

        Reads:
            S3_BUCKET  – bucket name
            S3_KEY     – object key

        Raises ValueError if either env var is not set.
        """
        bucket = os.getenv("S3_BUCKET", "").strip()
        key = os.getenv("S3_KEY", "").strip()

        if not bucket:
            raise ValueError(
                "S3_BUCKET environment variable is not set. "
                "Please add it to your .env file."
            )
        if not key:
            raise ValueError(
                "S3_KEY environment variable is not set. "
                "Please add it to your .env file."
            )

        return S3Service.download_excel_as_bytes(bucket=bucket, key=key)

    @staticmethod
    def check_object_exists(bucket: str, key: str) -> bool:
        """
        Quickly check whether an S3 object exists (head_object — no download).

        Returns True if the object exists and is accessible, False otherwise.
        """
        try:
            client = S3Service._make_client()
            client.head_object(Bucket=bucket, Key=key)
            logger.info("[S3] Object exists | bucket=%s | key=%s", bucket, key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                logger.warning(
                    "[S3] Object not found | bucket=%s | key=%s", bucket, key
                )
            else:
                logger.warning(
                    "[S3] head_object failed | code=%s | bucket=%s | key=%s",
                    code, bucket, key,
                )
            return False
        except BotoCoreError as exc:
            logger.warning("[S3] BotoCore error on head_object | %s", exc)
            return False
