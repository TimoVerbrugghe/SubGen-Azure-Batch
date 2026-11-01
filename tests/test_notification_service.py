"""
Tests for notification service module.

Tests cover:
- NotificationConfig dataclass
- NotificationService singleton
- Pushover notification sending
- Failure notification formatting
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestNotificationConfig:
    """Test NotificationConfig dataclass."""
    
    def test_pushover_configured_when_both_set(self):
        """Test pushover_configured returns True when both keys are set."""
        from app.utils.notification_service import NotificationConfig
        
        config = NotificationConfig(
            pushover_user_key="user123",
            pushover_api_token="token456"
        )
        assert config.pushover_configured is True
    
    def test_pushover_not_configured_when_missing_key(self):
        """Test pushover_configured returns False when key is missing."""
        from app.utils.notification_service import NotificationConfig
        
        config = NotificationConfig(
            pushover_user_key="",
            pushover_api_token="token456"
        )
        assert config.pushover_configured is False
    
    def test_pushover_not_configured_when_missing_token(self):
        """Test pushover_configured returns False when token is missing."""
        from app.utils.notification_service import NotificationConfig
        
        config = NotificationConfig(
            pushover_user_key="user123",
            pushover_api_token=""
        )
        assert config.pushover_configured is False
    
    def test_is_configured_when_pushover_configured(self):
        """Test is_configured returns True when Pushover is configured."""
        from app.utils.notification_service import NotificationConfig
        
        config = NotificationConfig(
            pushover_user_key="user123",
            pushover_api_token="token456"
        )
        assert config.is_configured is True
    
    def test_is_configured_when_nothing_configured(self):
        """Test is_configured returns False when nothing is configured."""
        from app.utils.notification_service import NotificationConfig
        
        config = NotificationConfig()
        assert config.is_configured is False

    def test_batch_success_notifications_are_disabled_by_default(self):
        """Test successful batch summaries require explicit opt-in."""
        from app.utils.notification_service import NotificationConfig

        assert NotificationConfig().notify_on_success is False


class TestNotificationServiceSingleton:
    """Test NotificationService singleton behavior."""
    
    def test_get_instance_returns_same_object(self):
        """Test that get_instance returns the same singleton."""
        from app.utils.notification_service import (
            NotificationConfig,
            NotificationService,
        )

        # Reset singleton
        NotificationService.reset_instance()
        
        with patch(
            "app.utils.notification_service.get_settings",
            return_value=MagicMock(notification=NotificationConfig()),
        ):
            instance1 = NotificationService.get_instance()
            instance2 = NotificationService.get_instance()
            
            assert instance1 is instance2
        
        # Cleanup
        NotificationService.reset_instance()
    
    def test_reset_instance_clears_singleton(self):
        """Test that reset_instance clears the singleton."""
        from app.utils.notification_service import NotificationService
        
        from app.utils.notification_service import NotificationConfig

        with patch(
            "app.utils.notification_service.get_settings",
            return_value=MagicMock(notification=NotificationConfig()),
        ):
            instance1 = NotificationService.get_instance()
            NotificationService.reset_instance()
            instance2 = NotificationService.get_instance()
            
            # Should be different objects after reset
            assert instance1 is not instance2
        
        # Cleanup
        NotificationService.reset_instance()


class TestNotificationServiceConfig:
    """Test NotificationService configuration loading."""
    
    def test_loads_pushover_config_from_settings(self, mock_settings):
        """Test that Pushover config is loaded from application settings."""
        from app.utils.notification_service import NotificationService

        NotificationService.reset_instance()
        mock_settings.notification.pushover_user_key = "test-user-key"
        mock_settings.notification.pushover_api_token = "test-api-token"
        mock_settings.notification.notify_on_failure = True
        mock_settings.notification.notify_on_success = True

        with patch(
            "app.utils.notification_service.get_settings",
            return_value=mock_settings,
        ):
            instance = NotificationService.get_instance()

            assert instance.config.pushover_user_key == 'test-user-key'
            assert instance.config.pushover_api_token == 'test-api-token'
            assert instance.config.notify_on_failure is True
            assert instance.config.notify_on_success is True
        
        NotificationService.reset_instance()
    
    def test_uses_notification_defaults_from_settings(self, mock_settings):
        """Test notification trigger defaults are read from Settings."""
        from app.utils.notification_service import NotificationService

        NotificationService.reset_instance()

        with patch(
            "app.utils.notification_service.get_settings",
            return_value=mock_settings,
        ):
            instance = NotificationService.get_instance()
            assert instance.config.notify_on_failure is True
            assert instance.config.notify_on_success is False
        
        NotificationService.reset_instance()


class TestSendPushover:
    """Test Pushover notification sending."""
    
    @pytest.fixture
    def configured_service(self):
        """Create a NotificationService with Pushover configured."""
        from app.utils.notification_service import (NotificationConfig,
                                              NotificationService)
        
        config = NotificationConfig(
            pushover_user_key="test-user",
            pushover_api_token="test-token",
            notify_on_failure=True
        )
        return NotificationService(config)
    
    @pytest.mark.asyncio
    async def test_send_pushover_success(self, configured_service):
        """Test successful Pushover notification."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        
        with patch.object(configured_service, '_get_session', return_value=mock_session):
            result = await configured_service.send_pushover(
                title="Test Title",
                message="Test Message"
            )
            
            assert result is True
            mock_session.post.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_send_pushover_failure(self, configured_service):
        """Test Pushover notification failure handling."""
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad request")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        
        with patch.object(configured_service, '_get_session', return_value=mock_session):
            result = await configured_service.send_pushover(
                title="Test Title",
                message="Test Message"
            )
            
            assert result is False
    
    @pytest.mark.asyncio
    async def test_send_pushover_not_configured(self):
        """Test that send_pushover returns False when not configured."""
        from app.utils.notification_service import (NotificationConfig,
                                              NotificationService)
        
        config = NotificationConfig()  # Not configured
        service = NotificationService(config)
        
        result = await service.send_pushover(
            title="Test Title",
            message="Test Message"
        )
        
        assert result is False

    @pytest.mark.asyncio
    async def test_send_pushover_logs_network_failure(self, configured_service, caplog):
        """Test network errors are logged and reported without raising."""
        import aiohttp

        with patch.object(
            configured_service,
            "_get_session",
            side_effect=aiohttp.ClientError("network unavailable"),
        ):
            result = await configured_service.send_pushover(
                title="Test Title",
                message="Test Message",
            )

        assert result is False
        assert "Pushover notification request failed" in caplog.text


class TestNotifyJobFailed:
    """Test job failure notification."""
    
    @pytest.fixture
    def configured_service(self):
        """Create a NotificationService with Pushover configured."""
        from app.utils.notification_service import (NotificationConfig,
                                              NotificationService)
        
        config = NotificationConfig(
            pushover_user_key="test-user",
            pushover_api_token="test-token",
            notify_on_failure=True
        )
        return NotificationService(config)
    
    @pytest.mark.asyncio
    async def test_notify_job_failed_sends_notification(self, configured_service):
        """Test that notify_job_failed sends a formatted notification."""
        with patch.object(configured_service, 'send_pushover', new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            
            result = await configured_service.notify_job_failed(
                file_path="/media/tv/show/episode.mkv",
                error="Transcription timeout",
                job_id="abc123",
                source="Plex"
            )
            
            assert result is True
            mock_send.assert_called_once()
            
            # Verify notification content
            call_args = mock_send.call_args
            assert "SubGen-Azure-Batch" in call_args.kwargs['title']
            assert "Failed" in call_args.kwargs['title']
            assert "episode.mkv" in call_args.kwargs['message']
            assert "Transcription timeout" in call_args.kwargs['message']
    
    @pytest.mark.asyncio
    async def test_notify_job_failed_disabled(self):
        """Test that notifications are skipped when disabled."""
        from app.utils.notification_service import (NotificationConfig,
                                              NotificationService)
        
        config = NotificationConfig(
            pushover_user_key="test-user",
            pushover_api_token="test-token",
            notify_on_failure=False  # Disabled
        )
        service = NotificationService(config)
        
        result = await service.notify_job_failed(
            file_path="/media/file.mkv",
            error="Test error"
        )
        
        assert result is False

    @pytest.mark.asyncio
    async def test_notify_job_failed_not_configured(self):
        """Test that notifications are skipped without Pushover credentials."""
        from app.utils.notification_service import (
            NotificationConfig,
            NotificationService,
        )

        service = NotificationService(NotificationConfig(notify_on_failure=True))

        result = await service.notify_job_failed(
            file_path="/media/file.mkv",
            error="Test error",
        )

        assert result is False


class TestNotifyBatchCompletion:
    """Test batch completion summaries."""

    @pytest.fixture
    def configured_service(self):
        from app.utils.notification_service import (
            NotificationConfig,
            NotificationService,
        )

        return NotificationService(
            NotificationConfig(
                pushover_user_key="test-user",
                pushover_api_token="test-token",
                notify_on_failure=True,
                notify_on_success=True,
            )
        )

    @pytest.mark.asyncio
    async def test_sends_success_summary_with_counters(self, configured_service):
        with patch.object(
            configured_service, "send_pushover", new_callable=AsyncMock
        ) as send:
            send.return_value = True

            result = await configured_service.notify_batch_completion(3, 0, 2)

        assert result is True
        send.assert_awaited_once()
        title = send.await_args.kwargs["title"]
        message = send.await_args.kwargs["message"]
        assert "Successfully" in title
        assert "Total: 5" in message
        assert "Completed: 3" in message
        assert "Failed: 0" in message
        assert "Skipped: 2" in message

    @pytest.mark.asyncio
    async def test_sends_error_summary(self, configured_service):
        with patch.object(
            configured_service, "send_pushover", new_callable=AsyncMock
        ) as send:
            send.return_value = True

            result = await configured_service.notify_batch_completion(1, 2, 0)

        assert result is True
        assert "Errors" in send.await_args.kwargs["title"]
        assert "Failed: 2" in send.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_should_identify_shows_movies_and_languages_when_batch_finishes(
        self, configured_service, mock_settings
    ):
        mock_settings.media_folders = ["/tv", "/movies"]
        files = [
            "/tv/Maxima (2024)/Season 02/Maxima s02e05.mkv",
            "/tv/Maxima (2024)/Season 02/Maxima s02e06.mkv",
            "/tv/Maxima (2024)/Season 02/Maxima s02e06.mkv",
            "/movies/Example Movie (2025)/release-name.mkv",
            "/movies/Loose Movie.mkv",
        ]
        with (
            patch("app.utils.notification_service.get_settings", return_value=mock_settings),
            patch.object(configured_service, "send_pushover", new_callable=AsyncMock) as send,
        ):
            await configured_service.notify_batch_completion(
                5, 0, 0, batch_id="03624ed5", file_paths=files,
                languages=["nl-NL", "en-US", "nl-NL"],
            )
        assert send.await_args.kwargs["message"] == (
            "Batch: 03624ed5\nTotal: 5\nCompleted: 5\nFailed: 0\nSkipped: 0\n"
            "Languages: nl-NL, en-US\nMedia: 4 unique file(s)\n"
            "- Maxima (2024) / Season 02 (2 files)\n"
            "- Example Movie (2025) (1 file)\n- Loose Movie (1 file)"
        )

    @pytest.mark.asyncio
    async def test_should_include_failed_filenames_when_batch_has_errors(
        self, configured_service, mock_settings
    ):
        with (
            patch("app.utils.notification_service.get_settings", return_value=mock_settings),
            patch.object(configured_service, "send_pushover", new_callable=AsyncMock) as send,
        ):
            await configured_service.notify_batch_completion(
                1, 3, 0, batch_id="error-batch",
                failed_files=["/tv/a.mkv", "/tv/b.mkv", "/tv/c.mkv"],
            )
        assert send.await_args.kwargs["message"] == (
            "Batch: error-batch\nTotal: 4\nCompleted: 1\nFailed: 3\nSkipped: 0\n"
            "Failed files: a.mkv, b.mkv\n... and 1 more failed files"
        )

    @pytest.mark.asyncio
    async def test_should_bound_large_summary_and_report_omitted_titles(
        self, configured_service, mock_settings
    ):
        mock_settings.media_folders = ["/movies"]
        with (
            patch("app.utils.notification_service.get_settings", return_value=mock_settings),
            patch.object(configured_service, "send_pushover", new_callable=AsyncMock) as send,
        ):
            await configured_service.notify_batch_completion(
                50, 2, 0, batch_id="large-batch",
                file_paths=[f"/movies/{index}-{'x' * 200}.mkv" for index in range(50)],
                languages=["nl-NL"],
                failed_files=[f"/movies/{'y' * 200}.mkv", f"/movies/{'z' * 200}.mkv"],
            )
        message = send.await_args.kwargs["message"]
        assert len(message) <= 1024
        assert "... and 44 more titles" in message
        assert "Batch: large-batch" in message

    @pytest.mark.asyncio
    async def test_success_summary_is_disabled_by_default(self):
        from app.utils.notification_service import (
            NotificationConfig,
            NotificationService,
        )

        service = NotificationService(
            NotificationConfig(
                pushover_user_key="test-user",
                pushover_api_token="test-token",
            )
        )

        with patch.object(
            service, "send_pushover", new_callable=AsyncMock
        ) as send:
            result = await service.notify_batch_completion(1, 0, 0)

        assert result is False
        send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_credentials_skip_error_summary(self):
        from app.utils.notification_service import (
            NotificationConfig,
            NotificationService,
        )

        service = NotificationService(NotificationConfig(notify_on_failure=True))

        result = await service.notify_batch_completion(0, 1, 0)

        assert result is False

    @pytest.mark.asyncio
    async def test_error_summary_respects_failure_notification_setting(self):
        from app.utils.notification_service import (
            NotificationConfig,
            NotificationService,
        )

        service = NotificationService(
            NotificationConfig(
                pushover_user_key="test-user",
                pushover_api_token="test-token",
                notify_on_failure=False,
            )
        )

        with patch.object(
            service, "send_pushover", new_callable=AsyncMock
        ) as send:
            result = await service.notify_batch_completion(0, 1, 0)

        assert result is False
        send.assert_not_awaited()


class TestTestNotification:
    """Test the test_notification method."""
    
    @pytest.mark.asyncio
    async def test_test_notification_with_pushover(self):
        """Test test_notification returns correct status."""
        from app.utils.notification_service import (NotificationConfig,
                                              NotificationService)
        
        config = NotificationConfig(
            pushover_user_key="test-user",
            pushover_api_token="test-token"
        )
        service = NotificationService(config)
        
        with patch.object(service, 'send_pushover', new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            
            results = await service.test_notification()
            
            assert results['pushover']['configured'] is True
            assert results['pushover']['success'] is True
    
    @pytest.mark.asyncio
    async def test_test_notification_not_configured(self):
        """Test test_notification reports unconfigured status."""
        from app.utils.notification_service import (NotificationConfig,
                                              NotificationService)
        
        config = NotificationConfig()  # Not configured
        service = NotificationService(config)
        
        results = await service.test_notification()
        
        assert results['pushover']['configured'] is False
        assert results['pushover']['success'] is False


class TestNotifyFailureConvenience:
    """Test notify_failure convenience function."""
    
    @pytest.mark.asyncio
    async def test_notify_failure_success(self):
        """Test notify_failure convenience function."""
        from app.utils.notification_service import (NotificationService,
                                              notify_failure)
        
        NotificationService.reset_instance()
        
        # Mock the singleton
        mock_instance = MagicMock()
        mock_instance.notify_job_failed = AsyncMock(return_value=True)
        
        with patch.object(NotificationService, 'get_instance', return_value=mock_instance):
            result = await notify_failure(
                file_path="/test/file.mkv",
                error="Test error",
                job_id="123",
                source="test"
            )
            
            assert result is True
            mock_instance.notify_job_failed.assert_called_once()
        
        NotificationService.reset_instance()
    
    @pytest.mark.asyncio
    async def test_notify_failure_handles_exception(self):
        """Test that notify_failure catches exceptions."""
        from app.utils.notification_service import (NotificationService,
                                              notify_failure)
        
        NotificationService.reset_instance()
        
        with patch.object(NotificationService, 'get_instance', side_effect=Exception("Test error")):
            # Should not raise, just return False
            result = await notify_failure(
                file_path="/test/file.mkv",
                error="Test error"
            )
            
            assert result is False
        
        NotificationService.reset_instance()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
