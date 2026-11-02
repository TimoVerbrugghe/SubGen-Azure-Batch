"""Regression tests for Bazarr ASR response timing and compatibility."""

import asyncio
import socket
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
import requests
import uvicorn

from app.main import create_app
from app.utils.azure_batch_transcriber import TranscriptionResult
from app.utils.subtitle_utils import parse_srt, validate_srt


class ASRTestServer(uvicorn.Server):
    """Signal readiness without polling or arbitrary startup sleeps."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = asyncio.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        """Start the listener and notify the test fixture."""
        await super().startup(sockets=sockets)
        self.ready.set()


@pytest.fixture
def asr_settings(patched_settings: MagicMock) -> Iterator[MagicMock]:
    """Provide isolated ASR settings, including short test heartbeat intervals."""
    patched_settings.asr_keepalive_interval = 0.05
    patched_settings.transcription_timeout = 3600
    patched_settings.transcription.language_detection_candidates = "en-US,nl-NL"
    with (
        patch("app.routers.asr.get_settings", return_value=patched_settings),
        patch("app.utils.audio_extractor.get_settings", return_value=patched_settings),
    ):
        yield patched_settings


@pytest.fixture
def transcribe(mock_transcription_result: TranscriptionResult) -> Iterator[AsyncMock]:
    """Mock transcription at the service boundary; never contact Azure."""
    with patch(
        "app.routers.asr.TranscriptionService.transcribe_audio_data",
        new_callable=AsyncMock,
        return_value=(mock_transcription_result, MagicMock()),
    ) as mock:
        yield mock


@pytest_asyncio.fixture
async def asr_server(asr_settings: MagicMock) -> AsyncIterator[str]:
    """Serve the registered application on an isolated ephemeral TCP port."""
    with patch("app.main.get_settings", return_value=asr_settings):
        app = create_app()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = ASRTestServer(
        uvicorn.Config(
            app,
            lifespan="off",
            http="h11",
            log_level="error",
            access_log=False,
            timeout_graceful_shutdown=1,
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        await asyncio.wait_for(server.ready.wait(), timeout=5)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)
        listener.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/asr", "/detect-language"])
async def test_should_keep_bazarr_request_alive_beyond_read_timeout(
    endpoint: str,
    asr_server: str,
    transcribe: AsyncMock,
    mock_transcription_result: TranscriptionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bazarr's non-streaming requests.post survives multiple idle-timeout periods."""
    release = asyncio.Event()
    heartbeats_received = asyncio.Event()
    loop = asyncio.get_running_loop()
    read_timeout = 0.25
    started_at = 0.0

    async def pending_transcription(
        **kwargs: object,
    ) -> tuple[TranscriptionResult, None]:
        nonlocal started_at
        started_at = time.monotonic()
        await release.wait()
        return mock_transcription_result, None

    transcribe.side_effect = pending_transcription
    original_iter_content = requests.Response.iter_content

    def observe_content(
        response: requests.Response,
        chunk_size: int = 1,
        decode_unicode: bool = False,
    ) -> Iterator[bytes | str]:
        for chunk in original_iter_content(response, chunk_size, decode_unicode):
            if (
                isinstance(chunk, bytes)
                and chunk
                and not chunk.strip()
                and started_at
                and time.monotonic() - started_at >= read_timeout * 2
            ):
                loop.call_soon_threadsafe(heartbeats_received.set)
            yield chunk

    monkeypatch.setattr(requests.Response, "iter_content", observe_content)
    request_task = asyncio.create_task(
        asyncio.to_thread(
            requests.post,
            f"{asr_server}{endpoint}",
            params={"encode": "false", "language": "en", "output": "srt"},
            files={"audio_file": b"\x00\x01" * 16},
            timeout=(2, read_timeout),
        )
    )
    heartbeat_task = asyncio.create_task(heartbeats_received.wait())
    try:
        done, _ = await asyncio.wait(
            (request_task, heartbeat_task),
            timeout=5,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if request_task in done:
            await request_task
            pytest.fail("The request finished before pending Azure work was released")
        assert heartbeat_task in done, "No heartbeats arrived during pending Azure work"
        release.set()
        response = await asyncio.wait_for(request_task, timeout=5)
        assert response.status_code == 200
        assert response.headers["X-Accel-Buffering"] == "no"
        if endpoint == "/asr":
            assert (
                response.content.lstrip(b"\n")
                == mock_transcription_result.to_srt().encode()
            )
            assert validate_srt(response.text)
            assert len(parse_srt(response.text)) == len(
                mock_transcription_result.segments
            )
        else:
            assert response.json() == {
                "detected_language": "English",
                "language_code": "en",
            }
    finally:
        release.set()
        heartbeat_task.cancel()
        await asyncio.gather(request_task, heartbeat_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["srt", "vtt", "txt"])
async def test_should_preserve_buffered_output_when_keepalive_disabled(
    output: str,
    asr_server: str,
    asr_settings: MagicMock,
    transcribe: AsyncMock,
    mock_transcription_result: TranscriptionResult,
) -> None:
    """Opting out preserves exact response contents and Content-Length."""
    asr_settings.asr_keepalive_interval = 0
    srt = mock_transcription_result.to_srt()
    expected = {
        "srt": srt,
        "vtt": f"WEBVTT\n\n{srt.strip().replace(',', '.')}",
        "txt": mock_transcription_result.text,
    }[output]
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{asr_server}/asr",
            params={"output": output, "encode": "false"},
            files={"audio_file": b"\x00\x01"},
        )
    assert response.text == expected
    assert int(response.headers["Content-Length"]) == len(expected.encode())
    assert "X-Accel-Buffering" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["vtt", "txt"])
async def test_should_not_prefix_other_formats_with_heartbeats(
    output: str,
    asr_server: str,
    transcribe: AsyncMock,
) -> None:
    """Only SRT accepts the leading blank lines used for ASR heartbeats."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{asr_server}/asr",
            params={"output": output, "encode": "false"},
            files={"audio_file": b"\x00\x01"},
        )
    assert "Content-Length" in response.headers
    assert not response.content.startswith(b"\n")


@pytest.mark.asyncio
async def test_should_use_configured_forced_language_without_azure(
    asr_server: str,
    asr_settings: MagicMock,
    transcribe: AsyncMock,
) -> None:
    """A forced language is a settings-based fast path, even without Azure credentials."""
    asr_settings.azure.is_configured = False
    asr_settings.transcription.force_language = "de"
    asr_settings.transcription.forced_language = "de"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{asr_server}/detect-language",
            params={"encode": "false"},
            files={"audio_file": b"\x00\x01"},
        )
    assert response.json() == {"detected_language": "German", "language_code": "de"}
    transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_should_pass_pcm_segment_and_candidates_to_shared_service(
    asr_server: str,
    transcribe: AsyncMock,
) -> None:
    """Language detection uses the requested PCM segment and shared Bazarr queue."""
    from app.transcription_service import JobSource

    audio = bytes(range(256)) * 500
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{asr_server}/detect-language",
            params={
                "encode": "false",
                "detect_lang_offset": 1,
                "detect_lang_length": 2,
                "video_file": "episode.mkv",
            },
            files={"audio_file": audio},
        )
    assert response.status_code == 200
    transcribe.assert_awaited_once_with(
        audio_data=audio[32000:96000],
        language="en-US",
        source=JobSource.BAZARR,
        file_name="episode.mkv",
        is_raw_pcm=True,
        candidate_locales=["en-US", "nl-NL"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/asr", "/detect-language"])
async def test_should_abort_stream_instead_of_returning_success_on_failure(
    endpoint: str,
    asr_server: str,
    transcribe: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Real processing errors remain visible, never valid subtitles or 'und' JSON."""
    transcribe.side_effect = RuntimeError("Azure transcription failed")
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.RemoteProtocolError, match="incomplete chunked read"):
            await client.post(
                f"{asr_server}{endpoint}",
                params={"encode": "false"},
                files={"audio_file": b"\x00\x01"},
            )
    assert "Bazarr request processing failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/asr", "/detect-language"])
async def test_should_cancel_pending_work_on_client_disconnect(
    endpoint: str,
    asr_server: str,
    transcribe: AsyncMock,
) -> None:
    """Disconnecting consumers do not leave orphan processing tasks."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_transcription(**kwargs: object) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    transcribe.side_effect = pending_transcription
    async with (
        httpx.AsyncClient() as client,
        client.stream(
            "POST",
            f"{asr_server}{endpoint}",
            params={"encode": "false"},
            files={"audio_file": b"\x00\x01"},
        ) as response,
    ):
        await asyncio.wait_for(started.wait(), timeout=2)
        assert await anext(response.aiter_bytes()) == b"\n"
    await asyncio.wait_for(cancelled.wait(), timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/asr", "/detect-language"])
@pytest.mark.parametrize("keepalive_interval", [0, 0.05])
async def test_should_bound_processing_time_even_with_heartbeats(
    endpoint: str,
    keepalive_interval: float,
    asr_server: str,
    asr_settings: MagicMock,
    transcribe: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Heartbeats do not turn stalled work into an unlimited request."""
    asr_settings.asr_keepalive_interval = keepalive_interval
    asr_settings.transcription_timeout = 0.05
    cancelled = asyncio.Event()

    async def pending_transcription(**kwargs: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    transcribe.side_effect = pending_transcription
    async with httpx.AsyncClient() as client:
        if keepalive_interval:
            with pytest.raises(
                httpx.RemoteProtocolError, match="incomplete chunked read"
            ):
                await client.post(
                    f"{asr_server}{endpoint}",
                    params={"encode": "false"},
                    files={"audio_file": b"\x00\x01"},
                )
        else:
            response = await client.post(
                f"{asr_server}{endpoint}",
                params={"encode": "false"},
                files={"audio_file": b"\x00\x01"},
            )
            assert response.status_code == 504
            assert response.json() == {"detail": "ASR processing timed out"}
    await asyncio.wait_for(cancelled.wait(), timeout=2)
    assert "Bazarr request exceeded processing timeout" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/asr", "/detect-language"])
async def test_should_return_json_error_when_buffered_processing_fails(
    endpoint: str,
    asr_server: str,
    asr_settings: MagicMock,
    transcribe: AsyncMock,
) -> None:
    """The opt-out path retains normal HTTP error status and JSON details."""
    asr_settings.asr_keepalive_interval = 0
    transcribe.side_effect = RuntimeError("Internal Azure diagnostics")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{asr_server}{endpoint}",
            params={"encode": "false"},
            files={"audio_file": b"\x00\x01"},
        )
    assert response.status_code == 500
    assert response.json() == {"detail": "ASR processing failed; see server logs"}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/asr", "/detect-language"])
async def test_should_reject_empty_upload_before_starting_stream(
    endpoint: str,
    asr_server: str,
    transcribe: AsyncMock,
) -> None:
    """Invalid uploads still produce an ordinary 400 rather than a partial stream."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{asr_server}{endpoint}", files={"audio_file": b""}
        )
    assert response.status_code == 400
    assert response.json() == {"detail": "Audio upload is empty"}
    transcribe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("candidates", ["", "en-US,nl-NL,es-ES,fr-FR,de-DE"])
async def test_should_reject_invalid_detection_candidates_before_streaming(
    candidates: str,
    asr_server: str,
    asr_settings: MagicMock,
    transcribe: AsyncMock,
) -> None:
    """Misconfiguration is reported explicitly rather than identifying a fallback locale."""
    asr_settings.transcription.language_detection_candidates = candidates
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{asr_server}/detect-language", files={"audio_file": b"\x00\x01"}
        )
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Language detection requires one to four candidate locales"
    }
    transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_should_kill_segment_extraction_on_disconnect(
    asr_server: str,
    transcribe: AsyncMock,
) -> None:
    """Cancelling an encoded language request also stops its FFmpeg subprocess."""
    started = asyncio.Event()
    stopped = asyncio.Event()
    process = MagicMock(returncode=None)

    async def communicate() -> None:
        started.set()
        await asyncio.Event().wait()

    async def wait() -> int:
        stopped.set()
        return -9

    process.communicate = AsyncMock(side_effect=communicate)
    process.wait = AsyncMock(side_effect=wait)
    with patch(
        "app.utils.audio_extractor.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=process,
    ):
        async with (
            httpx.AsyncClient() as client,
            client.stream(
                "POST",
                f"{asr_server}/detect-language",
                files={"audio_file": b"RIFF"},
            ) as response,
        ):
            await asyncio.wait_for(started.wait(), timeout=2)
            assert await anext(response.aiter_bytes()) == b"\n"
        await asyncio.wait_for(stopped.wait(), timeout=2)
    process.kill.assert_called_once_with()
    transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_should_extract_encoded_segment_and_return_detected_locale(
    asr_server: str,
    transcribe: AsyncMock,
    mock_transcription_result: TranscriptionResult,
) -> None:
    """Encoded detection preserves segment data, locale conversion and temp cleanup."""
    from app.transcription_service import JobSource

    mock_transcription_result.language = "nl-NL"
    input_files: list[Path] = []
    output_files: list[Path] = []

    async def spawn(*command: str, **kwargs: object) -> MagicMock:
        input_path = Path(command[command.index("-i") + 1])
        output_path = Path(command[-1])
        input_files.append(input_path)
        output_files.append(output_path)
        assert await asyncio.to_thread(input_path.read_bytes) == b"encoded upload"

        async def communicate() -> tuple[bytes, bytes]:
            await asyncio.to_thread(output_path.write_bytes, b"encoded segment")
            return b"", b""

        process = MagicMock(returncode=0)
        process.communicate = AsyncMock(side_effect=communicate)
        return process

    with patch(
        "app.utils.audio_extractor.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        side_effect=spawn,
    ):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{asr_server}/detect-language",
                params={"video_file": "episode.mkv"},
                files={"audio_file": ("encoded.wav", b"encoded upload", "audio/wav")},
            )
    assert response.json() == {"detected_language": "Dutch", "language_code": "nl"}
    transcribe.assert_awaited_once_with(
        audio_data=b"encoded segment",
        language="en-US",
        source=JobSource.BAZARR,
        file_name="episode.mkv",
        is_raw_pcm=False,
        candidate_locales=["en-US", "nl-NL"],
    )
    assert not await asyncio.to_thread(input_files[0].parent.exists)
    assert not await asyncio.to_thread(output_files[0].exists)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parameter", "value"),
    [("detect_lang_length", 0), ("detect_lang_offset", -1)],
)
async def test_should_reject_invalid_segment_queries_before_processing(
    parameter: str,
    value: int,
    asr_server: str,
    transcribe: AsyncMock,
) -> None:
    """The detection query constraints produce the standard validation response."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{asr_server}/detect-language",
            params={parameter: value},
            files={"audio_file": b"\x00\x01"},
        )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", parameter]
    transcribe.assert_not_awaited()
