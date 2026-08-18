import logging
from datetime import datetime, timedelta
from io import BytesIO

import imagehash
from PIL import Image

from src.config.settings import (
    TG_IMAGE_DUPLICATE_DAYS,
    TG_IMAGE_DUPLICATE_THRESHOLD,
)
from src.database.operations import ImageFingerprint, db


logger = logging.getLogger(__name__)
_last_cleanup_date = None


class ImageDuplicateCheckError(Exception):
    pass


def _cutoff_time():
    return datetime.now() - timedelta(days=TG_IMAGE_DUPLICATE_DAYS)


def _cleanup_expired_records(cutoff):
    global _last_cleanup_date

    today = datetime.now().date()
    if _last_cleanup_date == today:
        return
    db.delete(ImageFingerprint, ImageFingerprint.created_at < cutoff)
    _last_cleanup_date = today


def _has_recent_file_unique_id(file_unique_ids, cutoff):
    if not file_unique_ids:
        return False
    return bool(
        db.select(
            ImageFingerprint,
            (ImageFingerprint.created_at >= cutoff)
            & (ImageFingerprint.file_unique_id.in_(file_unique_ids)),
        )
    )


def _get_recent_hashes(cutoff):
    rows = db.select(
        ImageFingerprint,
        ImageFingerprint.created_at >= cutoff,
    )
    return [int(row["image_hash"], 16) for row in rows]


async def check_photo_duplicates(context, photo_items):
    try:
        return await _check_photo_duplicates(context, photo_items)
    except Exception as error:
        logger.warning("Failed to check image duplicate: %s", error)
        raise ImageDuplicateCheckError from error


async def _check_photo_duplicates(context, photo_items):
    if not photo_items:
        return False, []

    cutoff = _cutoff_time()
    _cleanup_expired_records(cutoff)

    file_unique_ids = [
        item["file_unique_id"]
        for item in photo_items
        if item.get("file_unique_id")
    ]
    if _has_recent_file_unique_id(file_unique_ids, cutoff):
        return True, []

    fingerprints = []
    for item in photo_items:
        telegram_file = await context.bot.get_file(item["file_id"])
        image_bytes = await telegram_file.download_as_bytearray()
        with Image.open(BytesIO(image_bytes)) as image:
            image_hash = str(imagehash.phash(image))
        fingerprints.append(
            {
                "media_id": item["file_id"],
                "file_unique_id": item.get("file_unique_id"),
                "image_hash": image_hash,
            }
        )

    recent_hashes = _get_recent_hashes(cutoff)
    for fingerprint in fingerprints:
        new_hash = int(fingerprint["image_hash"], 16)
        if any(
            (stored_hash ^ new_hash).bit_count()
            <= TG_IMAGE_DUPLICATE_THRESHOLD
            for stored_hash in recent_hashes
        ):
            return True, []

    return False, fingerprints


def save_photo_fingerprints(operation_key, submitter_id, fingerprints):
    with db.Session.begin() as session:
        session.add_all(
            [
                ImageFingerprint(
                    operation_key=operation_key,
                    submitter_id=str(submitter_id),
                    media_id=fingerprint["media_id"],
                    file_unique_id=fingerprint["file_unique_id"],
                    image_hash=fingerprint["image_hash"],
                )
                for fingerprint in fingerprints
            ]
        )
