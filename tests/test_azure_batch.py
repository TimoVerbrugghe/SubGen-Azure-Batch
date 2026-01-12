"""
Tests for Azure Batch Transcription API integration.

These tests verify the core functionality of the Azure Batch Transcription API client.
"""

import os
import sys
import time
from typing import Optional

import pytest
import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.azure_batch_transcriber import (AzureBatchTranscriber,
                                         TranscriptionJob, TranscriptionResult,
                                         TranscriptionSegment,
                                         TranscriptionStatus)
from app.utils.subtitle_utils import parse_srt, validate_srt


class TestTranscriptionResultClass:
    """Test the TranscriptionResult class."""
    
    def test_to_srt_basic(self):
        """Test basic SRT generation."""
        result = TranscriptionResult(
            job_id='test-123',
            language='en-US',
            segments=[
                TranscriptionSegment(start=0.0, end=2.5, text='Hello world', confidence=0.95),
                TranscriptionSegment(start=3.0, end=5.5, text='This is a test', confidence=0.92)
            ],
            duration=5.5
        )
        
        srt = result.to_srt()
        
        assert '1\n00:00:00,000 --> 00:00:02,500\nHello world' in srt
        assert '2\n00:00:03,000 --> 00:00:05,500\nThis is a test' in srt
    
    def test_to_srt_long_duration(self):
        """Test SRT with hours-long content."""
        result = TranscriptionResult(
            job_id='test-456',
            language='en-US',
            segments=[
                TranscriptionSegment(start=3661.5, end=3665.0, text='Over an hour in', confidence=0.9),
            ],
            duration=3665.0
        )
        
        srt = result.to_srt()
        
        # 3661.5 seconds = 1 hour, 1 minute, 1.5 seconds
        assert '01:01:01,500' in srt
    
    def test_text_property(self):
        """Test the text property concatenation."""
        result = TranscriptionResult(
            job_id='test-789',
            language='en-US',
            segments=[
                TranscriptionSegment(start=0.0, end=1.0, text='Hello', confidence=0.9),
                TranscriptionSegment(start=1.0, end=2.0, text='World', confidence=0.9),
            ],
            duration=2.0
        )
        
        assert result.text == 'Hello World'


class TestAzureBatchTranscriptionAPI:
    """Test Azure Batch Transcription API directly."""
    
    def get_api_base_url(self, region: str) -> str:
        """Get the base URL for Azure Speech API."""
        return f"https://{region}.api.cognitive.microsoft.com/speechtotext/v3.2"
    
    def test_api_connectivity(self, azure_speech_key, azure_speech_region):
        """Test that we can connect to the Azure Speech API."""
        url = f"{self.get_api_base_url(azure_speech_region)}/transcriptions"
        
        headers = {
            "Ocp-Apim-Subscription-Key": azure_speech_key,
            "Content-Type": "application/json"
        }
        
        # GET request to list transcriptions (should return empty list or existing jobs)
        response = requests.get(url, headers=headers)
        
        assert response.status_code == 200, f"API connectivity failed: {response.text}"
        data = response.json()
        assert "values" in data, "Response should contain 'values' key"
        print(f"✓ Connected to Azure Speech API. Found {len(data['values'])} existing transcriptions.")
    
    def test_list_supported_locales(self, azure_speech_key, azure_speech_region):
        """Test listing supported locales for transcription."""
        url = f"{self.get_api_base_url(azure_speech_region)}/transcriptions/locales"
        
        headers = {
            "Ocp-Apim-Subscription-Key": azure_speech_key,
        }
        
        response = requests.get(url, headers=headers)
        
        assert response.status_code == 200, f"Failed to get locales: {response.text}"
        locales = response.json()
        assert isinstance(locales, list), "Locales should be a list"
        assert len(locales) > 0, "Should have at least one supported locale"
        
        # Check for common languages
        assert "en-US" in locales, "en-US should be supported"
        print(f"✓ Found {len(locales)} supported locales. Sample: {locales[:5]}")
    
    def test_api_authentication_failure(self, azure_speech_region):
        """Test that invalid API key is rejected."""
        url = f"{self.get_api_base_url(azure_speech_region)}/transcriptions"
        
        headers = {
            "Ocp-Apim-Subscription-Key": "invalid_key_12345",
            "Content-Type": "application/json"
        }
        
        response = requests.get(url, headers=headers)
        
        # Should get 401 Unauthorized
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Invalid API key correctly rejected with 401")


class TestTranscriptionJobLifecycle:
    """Test the full transcription job lifecycle with a real audio file."""
    
    def get_api_base_url(self, region: str) -> str:
        """Get the base URL for Azure Speech API."""
        return f"https://{region}.api.cognitive.microsoft.com/speechtotext/v3.2"
    
    @pytest.mark.skip(reason="Requires Azure Blob Storage setup and audio file URL")
    def test_create_transcription_job(
        self, 
        azure_speech_key, 
        azure_speech_region,
        azure_storage_connection_string
    ):
        """
        Test creating a transcription job.
        
        Note: This test requires:
        1. Azure Blob Storage configured
        2. An audio file uploaded to blob storage
        3. A SAS URL for the audio file
        """
        url = f"{self.get_api_base_url(azure_speech_region)}/transcriptions"
        
        headers = {
            "Ocp-Apim-Subscription-Key": azure_speech_key,
            "Content-Type": "application/json"
        }
        
        # This URL would need to be a real SAS URL to an audio file
        audio_url = "https://your-storage.blob.core.windows.net/audio/test.wav?sastoken"
        
        payload = {
            "contentUrls": [audio_url],
            "locale": "en-US",
            "displayName": "SubGen-Azure-Batch Test Transcription",
            "properties": {
                "wordLevelTimestampsEnabled": True,
                "displayFormWordLevelTimestampsEnabled": True,
                "diarizationEnabled": False,
                "punctuationMode": "DictatedAndAutomatic",
                "profanityFilterMode": "None"
            }
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        assert response.status_code == 201, f"Failed to create transcription: {response.text}"
        data = response.json()
        
        assert "self" in data, "Response should contain 'self' URL"
        assert data["status"] in ["NotStarted", "Running"], f"Unexpected status: {data['status']}"
        
        job_id = data["self"].split("/")[-1]
        print(f"✓ Created transcription job: {job_id}")
        
        return job_id


class TestTranscriptionResultParsing:
    """Test parsing of transcription results into SRT format."""
    
    def test_parse_azure_result_to_srt(self):
        """Test converting Azure transcription result to SRT format."""
        # Sample Azure transcription result format
        azure_result = {
            "recognizedPhrases": [
                {
                    "offsetInTicks": 0,
                    "durationInTicks": 20000000,  # 2 seconds in ticks (100ns units)
                    "nBest": [
                        {
                            "display": "Hello, this is a test.",
                            "confidence": 0.95
                        }
                    ]
                },
                {
                    "offsetInTicks": 25000000,  # 2.5 seconds
                    "durationInTicks": 30000000,  # 3 seconds
                    "nBest": [
                        {
                            "display": "This is the second sentence.",
                            "confidence": 0.92
                        }
                    ]
                }
            ]
        }
        
        srt_content = self._convert_to_srt(azure_result)
        
        # Verify SRT format
        lines = srt_content.strip().split("\n")
        
        # First subtitle block
        assert lines[0] == "1", "First line should be subtitle number 1"
        assert "-->" in lines[1], "Second line should contain timestamp"
        assert "Hello, this is a test." in lines[2], "Third line should be text"
        
        print("✓ Successfully parsed Azure result to SRT format")
        print(f"Generated SRT:\n{srt_content}")
    
    def _convert_to_srt(self, azure_result: dict) -> str:
        """Convert Azure transcription result to SRT format."""
        srt_lines = []
        
        for i, phrase in enumerate(azure_result.get("recognizedPhrases", []), 1):
            # Convert ticks to seconds (1 tick = 100 nanoseconds)
            start_seconds = phrase["offsetInTicks"] / 10_000_000
            duration_seconds = phrase["durationInTicks"] / 10_000_000
            end_seconds = start_seconds + duration_seconds
            
            # Get the best transcription
            text = phrase["nBest"][0]["display"] if phrase.get("nBest") else ""
            
            # Format timestamps
            start_time = self._seconds_to_srt_time(start_seconds)
            end_time = self._seconds_to_srt_time(end_seconds)
            
            srt_lines.append(str(i))
            srt_lines.append(f"{start_time} --> {end_time}")
            srt_lines.append(text)
            srt_lines.append("")
        
        return "\n".join(srt_lines)
    
    def _seconds_to_srt_time(self, seconds: float) -> str:
        """Convert seconds to SRT time format (HH:MM:SS,mmm)."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


class TestAPIRateLimits:
    """Test Azure API rate limits and quotas."""
    
    def get_api_base_url(self, region: str) -> str:
        """Get the base URL for Azure Speech API."""
        return f"https://{region}.api.cognitive.microsoft.com/speechtotext/v3.2"
    
    def test_concurrent_job_limit(self, azure_speech_key, azure_speech_region):
        """
        Test to understand concurrent job limits.
        
        Azure Batch Transcription limits:
        - Free tier: 1 concurrent transcription
        - S0 tier: 20 concurrent transcriptions (default)
        - Can be increased via support request
        """
        url = f"{self.get_api_base_url(azure_speech_region)}/transcriptions"
        
        headers = {
            "Ocp-Apim-Subscription-Key": azure_speech_key,
        }
        
        response = requests.get(url, headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        
        # Count running jobs
        running_jobs = [
            t for t in data.get("values", []) 
            if t.get("status") in ["NotStarted", "Running"]
        ]
        
        print(f"✓ Current running transcription jobs: {len(running_jobs)}")
        print(f"  Note: Default limit is 20 concurrent jobs (S0 tier)")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
