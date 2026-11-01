"""
Notification service for SubGen-Azure-Batch.

This module provides notification functionality for transcription events,
including per-file failures and batch completion summaries. Currently supports
Pushover.

Design:
- Single responsibility: Only handles outbound notifications
- Called by TranscriptionService for job failures and by the batch router on completion
- Async-first design for non-blocking notifications
- Graceful degradation: Notification failures don't break transcription flow
"""

import asyncio
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Optional

import aiohttp

from app.config import NotificationConfig, get_settings

logger = logging.getLogger(__name__)


def _media_summary(file_paths: list[str], media_roots: list[str]) -> list[str]:
    """Group unique media paths by show/season or movie without filesystem access."""
    groups: Counter[str] = Counter()
    roots = [Path(root) for root in media_roots]
    for raw_path in dict.fromkeys(file_paths):
        path = Path(raw_path)
        relative = next(
            (path.relative_to(root) for root in roots if root in path.parents),
            path,
        )
        if re.fullmatch(
            r"(?:season|seizoen)[ _.-]*\d+|s\d+|specials",
            path.parent.name,
            flags=re.IGNORECASE,
        ) and len(relative.parts) >= 3:
            label = f"{path.parent.parent.name} / {path.parent.name}"
        elif len(relative.parts) >= 2 and not relative.is_absolute():
            label = relative.parts[0]
        else:
            label = path.stem
        label = " ".join(label.split())
        groups[label] += 1
    lines = []
    for label, count in list(groups.items())[:6]:
        display = label if len(label) <= 100 else f"{label[:97]}..."
        lines.append(f"- {display} ({count} file{'s' if count != 1 else ''})")
    if len(groups) > 6:
        lines.append(f"... and {len(groups) - 6} more titles")
    return lines


class NotificationService:
    """
    Singleton service for sending notifications.
    
    Usage:
        # Get instance
        notifier = NotificationService.get_instance()
        
        # Send failure notification
        await notifier.notify_job_failed(
            file_path="/tv/Show/episode.mkv",
            error="Azure transcription failed: timeout",
            job_id="abc123"
        )
    """
    
    _instance: Optional["NotificationService"] = None
    
    PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
    
    def __init__(self, config: NotificationConfig):
        """Initialize with configuration."""
        self._config: NotificationConfig = config
        self._session: Optional[aiohttp.ClientSession] = None
    
    @classmethod
    def get_instance(cls) -> "NotificationService":
        """Get or create the singleton instance."""
        if cls._instance is None:
            config = cls._load_config()
            cls._instance = cls(config)
        return cls._instance
    
    @classmethod
    def _load_config(cls) -> NotificationConfig:
        """Load notification configuration from application settings."""
        return get_settings().notification
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        if cls._instance and cls._instance._session:
            # Schedule session close if there's an event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(cls._instance._close_session())
            except RuntimeError:
                pass
        cls._instance = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def _close_session(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def close(self) -> None:
        """Close the notification service."""
        await self._close_session()
    
    @property
    def config(self) -> NotificationConfig:
        """Get the current configuration."""
        return self._config
    
    async def send_pushover(
        self,
        title: str,
        message: str,
        url: Optional[str] = None,
        url_title: Optional[str] = None,
    ) -> bool:
        """
        Send a notification via Pushover.
        
        Args:
            title: Notification title.
            message: Notification message body.
            url: Optional URL to include.
            url_title: Optional title for the URL.
            
        Returns:
            True if notification was sent successfully.
        """
        if not self._config.pushover_configured:
            logger.debug("Pushover not configured, skipping notification")
            return False
        
        payload = {
            "token": self._config.pushover_api_token,
            "user": self._config.pushover_user_key,
            "title": title,
            "message": message,
            "priority": 0,  # Normal priority
        }
        
        if url:
            payload["url"] = url
        if url_title:
            payload["url_title"] = url_title
        
        try:
            session = await self._get_session()
            async with session.post(self.PUSHOVER_API_URL, data=payload) as response:
                if response.status == 200:
                    logger.info(f"Pushover notification sent successfully: {title}")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Pushover notification failed (HTTP {response.status}): {error_text}")
                    return False
                    
        except Exception:
            logger.exception("Pushover notification request failed")
            return False
    
    async def notify_job_failed(
        self,
        file_path: str,
        error: str,
        job_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> bool:
        """
        Send notification when a transcription job fails.
        
        Args:
            file_path: Path to the media file that failed.
            error: Error message describing the failure.
            job_id: Optional job ID for reference.
            source: Optional source of the job (e.g., "Plex", "Bazarr", "UI").
            
        Returns:
            True if notification was sent successfully.
        """
        if not self._config.notify_on_failure:
            logger.debug("Failure notifications disabled, skipping")
            return False
        
        if not self._config.is_configured:
            logger.debug("No notification service configured")
            return False
        
        # Format the notification
        file_name = Path(file_path).name
        title = "⚠️ SubGen-Azure-Batch: Transcription Failed"
        
        # Build message
        lines = [f"File: {file_name}"]
        if source:
            lines.append(f"Source: {source}")
        if job_id:
            lines.append(f"Job ID: {job_id[:8]}...")
        lines.append(f"\nError: {error}")
        
        message = "\n".join(lines)
        
        # Send via configured services
        success = False
        
        if self._config.pushover_configured:
            success = await self.send_pushover(
                title=title,
                message=message,
            )
        
        return success

    async def notify_batch_completion(
        self,
        completed_jobs: int,
        failed_jobs: int,
        skipped_jobs: int,
        batch_id: str | None = None,
        file_paths: list[str] | None = None,
        languages: list[str] | None = None,
        failed_files: list[str] | None = None,
    ) -> bool:
        """Send a batch summary when its configured outcome is reached."""
        if failed_jobs:
            if not self._config.notify_on_failure:
                logger.debug("Batch error notifications disabled, skipping")
                return False
            title = "⚠️ SubGen-Azure-Batch: Batch Completed with Errors"
        else:
            if not self._config.notify_on_success:
                logger.debug("Batch success notifications disabled, skipping")
                return False
            title = "✅ SubGen-Azure-Batch: Batch Completed Successfully"

        if not self._config.is_configured:
            logger.debug("Pushover is not configured, skipping batch summary")
            return False

        total_jobs = completed_jobs + failed_jobs + skipped_jobs
        lines = [
            f"Total: {total_jobs}",
            f"Completed: {completed_jobs}",
            f"Failed: {failed_jobs}",
            f"Skipped: {skipped_jobs}",
        ]
        if batch_id:
            lines.insert(0, f"Batch: {' '.join(batch_id.split())[:64]}")
        if languages:
            language_text = ", ".join(dict.fromkeys(languages))
            lines.append(f"Languages: {' '.join(language_text.split())[:100]}")
        if file_paths:
            unique_count = len(set(file_paths))
            lines.append(f"Media: {unique_count} unique file(s)")
            lines.extend(_media_summary(file_paths, get_settings().media_folders))
        if failed_files:
            names = [
                " ".join(Path(path).name.split())[:100]
                for path in dict.fromkeys(failed_files)
            ]
            lines.append(f"Failed files: {', '.join(names[:2])}")
            if len(names) > 2:
                lines.append(f"... and {len(names) - 2} more failed files")
        message = "\n".join(lines)
        if len(message) > 1024:
            message = f"{message[:1021]}..."
        return await self.send_pushover(title=title, message=message)
    
    async def test_notification(self) -> dict:
        """
        Send a test notification to verify configuration.
        
        Returns:
            Dict with status for each configured service.
        """
        results = {
            "pushover": {"configured": False, "success": False, "error": None}
        }
        
        if self._config.pushover_configured:
            results["pushover"]["configured"] = True
            try:
                success = await self.send_pushover(
                    title="🧪 SubGen-Azure-Batch Test",
                    message="This is a test notification from SubGen-Azure-Batch. If you see this, notifications are working!",
                )
                results["pushover"]["success"] = success
            except Exception as e:
                results["pushover"]["error"] = str(e)
        
        return results


# Convenience function for use throughout the app
async def notify_failure(
    file_path: str,
    error: str,
    job_id: Optional[str] = None,
    source: Optional[str] = None,
) -> bool:
    """
    Convenience function to send a failure notification.
    
    This is a fire-and-forget function that won't raise exceptions.
    """
    try:
        notifier = NotificationService.get_instance()
        return await notifier.notify_job_failed(
            file_path=file_path,
            error=error,
            job_id=job_id,
            source=source,
        )
    except Exception as e:
        logger.error(f"Failed to send failure notification: {e}")
        return False


async def notify_batch_completion(
    completed_jobs: int,
    failed_jobs: int,
    skipped_jobs: int,
    batch_id: str | None = None,
    file_paths: list[str] | None = None,
    languages: list[str] | None = None,
    failed_files: list[str] | None = None,
) -> bool:
    """Send a batch completion summary without propagating notification errors."""
    try:
        notifier = NotificationService.get_instance()
        return await notifier.notify_batch_completion(
            completed_jobs=completed_jobs,
            failed_jobs=failed_jobs,
            skipped_jobs=skipped_jobs,
            batch_id=batch_id,
            file_paths=file_paths,
            languages=languages,
            failed_files=failed_files,
        )
    except Exception as e:
        logger.error(f"Failed to send batch completion notification: {e}")
        return False
