"""
Tests for notification service module.

Tests cover:
- NotificationConfig dataclass
- NotificationService singleton
- Pushover notification sending
- Failure notification formatting
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestNotificationConfig:
    """Test NotificationConfig dataclass."""
    
    def test_pushover_configured_when_both_set(self):
        """Test pushover_configured returns True when both keys are set."""
        from app.notification_service import NotificationConfig
        
        config = NotificationConfig(
            pushover_user_key="user123",
            pushover_api_token="token456"
        )
        assert config.pushover_configured is True
    
    def test_pushover_not_configured_when_missing_key(self):
        """Test pushover_configured returns False when key is missing."""
        from app.notification_service import NotificationConfig
        
        config = NotificationConfig(
            pushover_user_key="",
            pushover_api_token="token456"
        )
        assert config.pushover_configured is False
    
    def test_pushover_not_configured_when_missing_token(self):
        """Test pushover_configured returns False when token is missing."""
        from app.notification_service import NotificationConfig
        
        config = NotificationConfig(
            pushover_user_key="user123",
            pushover_api_token=""
        )
        assert config.pushover_configured is False
    
    def test_is_configured_when_pushover_configured(self):
        """Test is_configured returns True when Pushover is configured."""
        from app.notification_service import NotificationConfig
        
        config = NotificationConfig(
            pushover_user_key="user123",
            pushover_api_token="token456"
        )
        assert config.is_configured is True
    
    def test_is_configured_when_nothing_configured(self):
        """Test is_configured returns False when nothing is configured."""
        from app.notification_service import NotificationConfig
        
        config = NotificationConfig()
        assert config.is_configured is False


class TestNotificationServiceSingleton:
    """Test NotificationService singleton behavior."""
    
    def test_get_instance_returns_same_object(self):
        """Test that get_instance returns the same singleton."""
        from app.notification_service import NotificationService

        # Reset singleton
        NotificationService.reset_instance()
        
        with patch.dict(os.environ, {}, clear=True):
            instance1 = NotificationService.get_instance()
            instance2 = NotificationService.get_instance()
            
            assert instance1 is instance2
        
        # Cleanup
        NotificationService.reset_instance()
    
    def test_reset_instance_clears_singleton(self):
        """Test that reset_instance clears the singleton."""
        from app.notification_service import NotificationService
        
        with patch.dict(os.environ, {}, clear=True):
            instance1 = NotificationService.get_instance()
            NotificationService.reset_instance()
            instance2 = NotificationService.get_instance()
            
            # Should be different objects after reset
            assert instance1 is not instance2
        
        # Cleanup
        NotificationService.reset_instance()


class TestNotificationServiceConfig:
    """Test NotificationService configuration loading."""
    
    def test_loads_pushover_config_from_env(self):
        """Test that Pushover config is loaded from environment."""
        from app.notification_service import NotificationService
        
        NotificationService.reset_instance()
        
        env = {
            'PUSHOVER_USER_KEY': 'test-user-key',
            'PUSHOVER_API_TOKEN': 'test-api-token',
            'NOTIFY_ON_FAILURE': 'true'
        }
        
        with patch.dict(os.environ, env, clear=True):
            instance = NotificationService.get_instance()
            
            assert instance.config.pushover_user_key == 'test-user-key'
            assert instance.config.pushover_api_token == 'test-api-token'
            assert instance.config.notify_on_failure is True
        
        NotificationService.reset_instance()
    
    def test_notify_on_failure_defaults_to_true(self):
        """Test that notify_on_failure defaults to True."""
        from app.notification_service import NotificationService
        
        NotificationService.reset_instance()
        
        with patch.dict(os.environ, {}, clear=True):
            instance = NotificationService.get_instance()
            assert instance.config.notify_on_failure is True
        
        NotificationService.reset_instance()


class TestSendPushover:
    """Test Pushover notification sending."""
    
    @pytest.fixture
    def configured_service(self):
        """Create a NotificationService with Pushover configured."""
        from app.notification_service import (NotificationConfig,
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
        from app.notification_service import (NotificationConfig,
                                              NotificationService)
        
        config = NotificationConfig()  # Not configured
        service = NotificationService(config)
        
        result = await service.send_pushover(
            title="Test Title",
            message="Test Message"
        )
        
        assert result is False


class TestNotifyJobFailed:
    """Test job failure notification."""
    
    @pytest.fixture
    def configured_service(self):
        """Create a NotificationService with Pushover configured."""
        from app.notification_service import (NotificationConfig,
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
        from app.notification_service import (NotificationConfig,
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
        """Test that notifications are skipped when not configured."""
        from app.notification_service import (NotificationConfig,
                                              NotificationService)
        
        config = NotificationConfig(notify_on_failure=True)  # Enabled but not configured
        service = NotificationService(config)
        
        result = await service.notify_job_failed(
            file_path="/media/file.mkv",
            error="Test error"
        )
        
        assert result is False


class TestTestNotification:
    """Test the test_notification method."""
    
    @pytest.mark.asyncio
    async def test_test_notification_with_pushover(self):
        """Test test_notification returns correct status."""
        from app.notification_service import (NotificationConfig,
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
        from app.notification_service import (NotificationConfig,
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
        from app.notification_service import (NotificationService,
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
        from app.notification_service import (NotificationService,
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
