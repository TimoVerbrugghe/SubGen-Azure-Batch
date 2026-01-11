"""
Notification service for SubGen-Azure-Batch.

This module provides notification functionality for transcription events,
primarily for alerting when jobs fail. Currently supports Pushover.

Design:
- Single responsibility: Only handles outbound notifications
- Called by TranscriptionService when job status changes to FAILED
- Async-first design for non-blocking notifications
- Graceful degradation: Notification failures don't break transcription flow
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class NotificationConfig:
    """Configuration for notification services.
    
    Loaded from environment variables:
    - PUSHOVER_USER_KEY: Pushover user/group key
    - PUSHOVER_API_TOKEN: Pushover application API token
    - NOTIFY_ON_FAILURE: Enable failure notifications (default: true)
    
    Note: Even if NOTIFY_ON_FAILURE is true, notifications will only be sent
    if Pushover is properly configured (both user key and API token set).
    """
    # Pushover settings
    pushover_user_key: str = ""
    pushover_api_token: str = ""
    
    # Notification triggers
    notify_on_failure: bool = True
    
    @property
    def pushover_configured(self) -> bool:
        """Check if Pushover is properly configured."""
        return bool(self.pushover_user_key and self.pushover_api_token)
    
    @property
    def is_configured(self) -> bool:
        """Check if any notification service is configured."""
        return self.pushover_configured


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
    _config: Optional[NotificationConfig] = None
    
    PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
    
    def __init__(self, config: NotificationConfig):
        """Initialize with configuration."""
        self._config = config
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
        """Load notification configuration from environment."""
        import os
        
        def get_bool(value: str, default: bool = False) -> bool:
            if not value:
                return default
            return value.lower() in ('true', '1', 'yes', 'on')
        
        pushover_user = os.getenv('PUSHOVER_USER_KEY', '')
        pushover_token = os.getenv('PUSHOVER_API_TOKEN', '')
        
        # Default notify_on_failure to True
        # Even if True, won't send if Pushover isn't configured
        notify_on_failure = get_bool(os.getenv('NOTIFY_ON_FAILURE', 'true'), default=True)
        
        return NotificationConfig(
            pushover_user_key=pushover_user,
            pushover_api_token=pushover_token,
            notify_on_failure=notify_on_failure,
        )
    
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
                    
        except Exception as e:
            logger.error(f"Pushover notification error: {e}")
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
