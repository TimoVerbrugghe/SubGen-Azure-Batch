"""Authenticated, targeted subtitle refreshes for Plex, Jellyfin, and Emby."""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Literal
from urllib.parse import quote

import aiohttp

from app.config import PathMappingConfig, get_media_path, get_settings

logger = logging.getLogger(__name__)


class MediaServerError(Exception):
    """A media-server request failed, with a credential-safe reason."""


@dataclass
class MediaRefreshContext:
    """Server-specific IDs and original paths carried by a webhook job."""

    item_ids: dict[str, str] = field(default_factory=dict)
    server_paths: dict[str, str] = field(default_factory=dict)


@dataclass
class MediaRefreshResult:
    """Refresh acceptance, not confirmation that subtitle indexing has finished."""

    status: Literal["queued", "not_found", "failed", "not_configured"]
    error: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Serialize the outcome without exposing server credentials."""
        return {"status": self.status, "error": self.error}


@dataclass
class MediaConnectionStatus:
    """Read-only connectivity and authentication status without exposing secrets."""

    configured: bool
    connected: bool = False
    error: str | None = None


class _MediaServerClient:
    """Shared bounded HTTP transport; credentials stay in request headers."""

    server: str
    token: str
    name: str

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    @property
    def is_configured(self) -> bool:
        """Check whether the server URL and token are present."""
        return bool(self.server and self.token)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = (
                {"X-Plex-Token": self.token}
                if self.name == "Plex"
                else {"Authorization": f'MediaBrowser Token="{self.token}"'}
            )
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30, connect=10),
            )
        return self._session

    async def close(self) -> None:
        """Close HTTP resources, retaining only the session-scoped path cache."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(
        self,
        method: str,
        route: str,
        params: dict[str, str] | None = None,
        read_json: bool = False,
        max_attempts: int = 3,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> dict[str, Any]:
        for attempt in range(max_attempts):
            try:
                session = await self._get_session()
                request = getattr(session, method)
                async with request(
                    f"{self.server}{route}",
                    params=params,
                    headers={"Accept": "application/json"},
                    allow_redirects=False,
                    timeout=timeout or aiohttp.ClientTimeout(total=30, connect=10),
                ) as response:
                    if response.status not in (200, 204):
                        if (
                            response.status in (429, 500, 502, 503, 504)
                            and attempt < max_attempts - 1
                        ):
                            logger.warning(
                                f"{self.name}: HTTP {response.status}; retrying request"
                            )
                        else:
                            raise MediaServerError(f"HTTP {response.status}")
                    elif not read_json:
                        return {}
                    else:
                        data = await response.json()
                        if not isinstance(data, dict):
                            raise MediaServerError("Invalid API response")
                        return data
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                if attempt == max_attempts - 1:
                    raise MediaServerError("Connection failed or timed out") from None
                logger.warning(f"{self.name}: Connection failed; retrying request")
            except (aiohttp.ClientError, ValueError):
                raise MediaServerError("Invalid or unreadable API response") from None
            await asyncio.sleep(0.5 * (2**attempt))
        raise MediaServerError("Request retry limit reached")

    async def check_connection(self) -> MediaConnectionStatus:
        """Verify configured credentials with a single bounded, read-only request."""
        status = MediaConnectionStatus(configured=bool(self.token.strip()))
        if not status.configured:
            return status
        if not self.server.strip():
            status.error = "Server URL not configured"
            logger.error(f"{self.name}: Connection check failed: {status.error}")
            return status
        try:
            route = "/library/sections" if self.name == "Plex" else "/System/Info"
            data = await self._request(
                "get",
                route,
                read_json=True,
                max_attempts=1,
                timeout=aiohttp.ClientTimeout(total=10, connect=5),
            )
            valid = (
                isinstance(data.get("MediaContainer"), dict)
                if self.name == "Plex"
                else isinstance(data.get("Id"), str) and bool(data["Id"])
            )
            if not valid:
                raise MediaServerError("Unexpected server response")
            status.connected = True
            logger.info(f"{self.name}: Authenticated connection check succeeded")
        except MediaServerError as exc:
            status.error = (
                "Invalid token or insufficient permissions"
                if str(exc) in ("HTTP 401", "HTTP 403")
                else str(exc)
            )
            logger.error(f"{self.name}: Connection check failed: {status.error}")
        finally:
            await self.close()
        return status

    async def refresh(
        self, file_path: str | None = None, item_id: str | None = None
    ) -> MediaRefreshResult:
        """Queue an item refresh, resolving its server-visible path if needed."""
        if not self.is_configured:
            logger.warning(f"{self.name}: Not configured; refresh skipped")
            return MediaRefreshResult("not_configured")
        try:
            queued = (
                await self._refresh_metadata(item_id)
                if item_id
                else await self._refresh_path(file_path)
                if file_path
                else False
            )
            if not queued:
                logger.warning(f"{self.name}: No unique library item for {file_path}")
                return MediaRefreshResult("not_found")
            logger.info(f"{self.name}: Subtitle refresh queued")
            return MediaRefreshResult("queued")
        except MediaServerError as exc:
            logger.error(f"{self.name}: Subtitle refresh failed: {exc}")
            return MediaRefreshResult("failed", str(exc))

    async def refresh_metadata(self, item_id: str) -> bool:
        """Return whether a refresh request for the item was accepted."""
        return (await self.refresh(item_id=item_id)).status == "queued"

    async def refresh_by_file_path(self, file_path: str) -> bool:
        """Return whether a refresh for the exact server-visible path was accepted."""
        return (await self.refresh(file_path=file_path)).status == "queued"

    async def _refresh_metadata(self, item_id: str) -> bool:
        raise NotImplementedError

    async def _refresh_path(self, file_path: str) -> bool:
        raise NotImplementedError


class PlexClient(_MediaServerClient):
    """Plex item refreshes and targeted directory scans without title searches."""

    def __init__(self, server: str | None = None, token: str | None = None) -> None:
        super().__init__()
        settings = get_settings()
        self.server = (server or settings.plex.server).rstrip("/")
        self.token = token or settings.plex.token
        self.name = "Plex"
        self._sections: list[dict[str, Any]] | None = None

    async def _refresh_metadata(self, item_id: str) -> bool:
        await self._request(
            "put", f"/library/metadata/{quote(item_id, safe='')}/refresh"
        )
        return True

    async def get_file_path(self, item_id: str) -> str | None:
        """Get the first media-part path for an item."""
        if not self.is_configured:
            return None
        try:
            data = await self._request(
                "get", f"/library/metadata/{quote(item_id, safe='')}", read_json=True
            )
            return data["MediaContainer"]["Metadata"][0]["Media"][0]["Part"][0]["file"]
        except (MediaServerError, KeyError, IndexError, TypeError) as exc:
            logger.error(f"Plex: File lookup failed ({type(exc).__name__})")
            return None

    async def get_library_sections(self) -> list[dict[str, Any]]:
        """Fetch library roots, caching them for this batch/session."""
        if not self.is_configured:
            return []
        if self._sections is None:
            data = await self._request("get", "/library/sections", read_json=True)
            try:
                self._sections = [
                    {
                        "key": str(section["key"]),
                        "title": section.get("title", ""),
                        "locations": [
                            loc["path"] for loc in section.get("Location", [])
                        ],
                    }
                    for section in data["MediaContainer"]["Directory"]
                    if section.get("type") in ("movie", "show")
                ]
                if any(
                    not isinstance(root, str) or not root
                    for section in self._sections
                    for root in section["locations"]
                ):
                    raise TypeError
            except (KeyError, TypeError, AttributeError) as exc:
                self._sections = None
                raise MediaServerError("Invalid library sections response") from exc
        return self._sections

    async def refresh_section_path(self, section_key: str, path: str) -> bool:
        """Request a partial scan, never a full-library metadata replacement."""
        if not self.is_configured:
            logger.warning("Plex: Not configured; scan skipped")
            return False
        try:
            await self._request(
                "get",
                f"/library/sections/{quote(section_key, safe='')}/refresh",
                params={"path": path},
            )
            return True
        except MediaServerError as exc:
            logger.error(f"Plex: Partial scan failed: {exc}")
            return False

    async def _refresh_path(self, file_path: str) -> bool:
        candidate = get_media_path(file_path)
        sections = await self.get_library_sections()
        matches = [
            (len(get_media_path(root).parts), section["key"])
            for section in sections
            for root in section["locations"]
            if candidate.is_relative_to(get_media_path(root))
        ]
        if not matches:
            return False
        depth = max(length for length, _ in matches)
        section_keys = {key for length, key in matches if length == depth}
        if len(section_keys) != 1:
            raise MediaServerError("Multiple libraries match the file path")
        section_key = next(iter(section_keys))
        await self._request(
            "get",
            f"/library/sections/{quote(section_key, safe='')}/refresh",
            params={"path": str(candidate.parent)},
        )
        return True


class JellyfinClient(_MediaServerClient):
    """Jellyfin/Emby item refreshes using a paginated exact-path index."""

    def __init__(
        self,
        server: str | None = None,
        token: str | None = None,
        is_emby: bool = False,
    ) -> None:
        super().__init__()
        settings = get_settings()
        config = settings.emby if is_emby else settings.jellyfin
        self.server = (server or config.server).rstrip("/")
        self.token = token or config.token
        self.is_emby = is_emby
        self.name = "Emby" if is_emby else "Jellyfin"
        self._path_index: dict[PurePath, set[str]] | None = None

    async def _refresh_metadata(self, item_id: str) -> bool:
        await self._request(
            "post",
            f"/Items/{quote(item_id, safe='')}/Refresh",
            params={
                "MetadataRefreshMode": "Default",
                "ImageRefreshMode": "None",
                "ReplaceAllMetadata": "false",
                "ReplaceAllImages": "false",
            },
        )
        return True

    async def get_file_path(self, item_id: str) -> str | None:
        """Get the main server-visible path for an item."""
        if not self.is_configured:
            return None
        try:
            data = await self._request(
                "get", f"/Items/{quote(item_id, safe='')}", read_json=True
            )
            return data.get("Path")
        except MediaServerError as exc:
            logger.error(f"{self.name}: File lookup failed: {exc}")
            return None

    async def _load_path_index(self) -> dict[PurePath, set[str]]:
        index: dict[PurePath, set[str]] = {}
        start = 0
        while True:
            data = await self._request(
                "get",
                "/Items",
                params={
                    "IncludeItemTypes": "Episode,Movie",
                    "Recursive": "true",
                    "Fields": "Path,MediaSources",
                    "Limit": "200",
                    "StartIndex": str(start),
                    "SortBy": "SortName",
                    "SortOrder": "Ascending",
                },
                read_json=True,
            )
            try:
                items = data["Items"]
                total = data["TotalRecordCount"]
                if not isinstance(items, list) or not isinstance(total, int):
                    raise TypeError
                for item in items:
                    item_id = item["Id"]
                    paths = [item.get("Path")]
                    paths.extend(
                        source.get("Path") for source in item.get("MediaSources", [])
                    )
                    if not isinstance(item_id, str) or not item_id:
                        raise TypeError
                    for path in paths:
                        if isinstance(path, str) and path:
                            index.setdefault(get_media_path(path), set()).add(item_id)
                start += len(items)
                if start >= total:
                    break
                if not items:
                    raise MediaServerError("Incomplete item listing")
            except (KeyError, TypeError, AttributeError) as exc:
                raise MediaServerError("Invalid item listing response") from exc
        return index

    async def _refresh_path(self, file_path: str) -> bool:
        if self._path_index is None:
            self._path_index = await self._load_path_index()
        ids = self._path_index.get(get_media_path(file_path), set())
        if len(ids) > 1:
            raise MediaServerError("Multiple items match the file path")
        if not ids:
            return False
        return await self._refresh_metadata(next(iter(ids)))


class MediaServerRefresher:
    """Session-scoped caches; servers run independently without overlapping scans."""

    def __init__(self) -> None:
        self._clients: dict[str, _MediaServerClient] = {}
        self._lock = asyncio.Lock()

    async def refresh(
        self, file_path: str, context: MediaRefreshContext | None = None
    ) -> dict[str, MediaRefreshResult]:
        """Refresh every configured server after a subtitle has been saved."""
        settings = get_settings()
        context = context or MediaRefreshContext()
        async with self._lock:
            names = [
                name
                for name in ("plex", "jellyfin", "emby")
                if getattr(settings, name).is_configured
            ]
            outcomes = await asyncio.gather(
                *(self._refresh_server(name, file_path, context) for name in names)
            )
            return dict(zip(names, outcomes))

    async def _refresh_server(
        self, name: str, file_path: str, context: MediaRefreshContext
    ) -> MediaRefreshResult:
        settings = get_settings()
        config = getattr(settings, name)
        if name not in self._clients:
            self._clients[name] = (
                PlexClient()
                if name == "plex"
                else JellyfinClient(is_emby=name == "emby")
            )
        client = self._clients[name]
        try:
            server_path = context.server_paths.get(name)
            item_id = context.item_ids.get(name)
            if not server_path and not item_id:
                mapping = config.path_mapping
                if not mapping.enabled and settings.path_mapping.enabled:
                    mapping = PathMappingConfig(
                        enabled=True,
                        from_path=settings.path_mapping.to_path,
                        to_path=settings.path_mapping.from_path,
                    )
                server_path = mapping.apply(file_path)
            return await client.refresh(file_path=server_path, item_id=item_id)
        except ValueError as exc:
            logger.error(f"{name}: Invalid refresh path mapping: {exc}")
            return MediaRefreshResult("failed", str(exc))
        finally:
            try:
                await client.close()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                logger.error(f"{name}: Failed to close media-server connection")


async def refresh_all_configured_servers(
    plex_item_id: str | None = None,
    jellyfin_item_id: str | None = None,
    emby_item_id: str | None = None,
) -> dict[str, bool]:
    """Refresh supplied server IDs (compatibility helper)."""
    results = {}
    for name, item_id in (
        ("plex", plex_item_id),
        ("jellyfin", jellyfin_item_id),
        ("emby", emby_item_id),
    ):
        if item_id:
            client = (
                PlexClient()
                if name == "plex"
                else JellyfinClient(is_emby=name == "emby")
            )
            try:
                results[name] = await client.refresh_metadata(item_id)
            finally:
                await client.close()
    return results


async def refresh_by_file_path(file_path: str) -> dict[str, bool]:
    """Refresh all configured servers for a SubGen-visible file path."""
    results = await MediaServerRefresher().refresh(file_path)
    return {name: result.status == "queued" for name, result in results.items()}
