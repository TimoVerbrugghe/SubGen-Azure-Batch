"""
Media Server Client - API clients for Plex, Jellyfin, and Emby.

Provides async methods to:
- Refresh metadata after subtitle creation
- Resolve file paths from item IDs (future use)
"""

import logging
from typing import Optional

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)


class PlexClient:
    """Async client for Plex Media Server API."""
    
    def __init__(self, server: Optional[str] = None, token: Optional[str] = None):
        """
        Initialize Plex client.
        
        Args:
            server: Plex server URL (e.g., 'http://192.168.1.100:32400')
            token: Plex authentication token
        """
        settings = get_settings()
        self.server = (server or settings.plex.server).rstrip('/')
        self.token = token or settings.plex.token
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def is_configured(self) -> bool:
        """Check if Plex is configured."""
        return bool(self.server and self.token)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-Plex-Token": self.token},
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session
    
    async def close(self):
        """Close the client session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def refresh_metadata(self, item_id: str) -> bool:
        """
        Refresh metadata for a Plex library item.
        
        This tells Plex to re-scan the item, picking up new subtitles.
        
        Args:
            item_id: The rating key (ID) of the item in Plex.
            
        Returns:
            True if refresh was initiated successfully.
        """
        if not self.is_configured:
            logger.warning("Plex not configured, skipping metadata refresh")
            return False
        
        url = f"{self.server}/library/metadata/{item_id}/refresh"
        
        try:
            session = await self._get_session()
            async with session.put(url) as response:
                if response.status == 200:
                    logger.info(f"Plex: Metadata refresh sent for item {item_id}")
                    return True
                else:
                    logger.warning(f"Plex: Metadata refresh failed (HTTP {response.status}) for item {item_id}")
                    return False
        except aiohttp.ClientError as e:
            logger.error(f"Plex refresh error: {e}")
            return False
    
    async def get_file_path(self, item_id: str) -> Optional[str]:
        """
        Get file path for a Plex item.
        
        Args:
            item_id: The rating key (ID) of the item.
            
        Returns:
            File path or None if not found.
        """
        if not self.is_configured:
            return None
        
        url = f"{self.server}/library/metadata/{item_id}"
        
        try:
            session = await self._get_session()
            async with session.get(url, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    return None
                
                data = await response.json()
                # Navigate Plex's XML-to-JSON structure
                metadata = data.get("MediaContainer", {}).get("Metadata", [])
                if not metadata:
                    return None
                
                media = metadata[0].get("Media", [])
                if not media:
                    return None
                
                parts = media[0].get("Part", [])
                if not parts:
                    return None
                
                return parts[0].get("file")
        except Exception as e:
            logger.error(f"Failed to get Plex file path: {e}")
            return None
    
    async def get_library_sections(self) -> list:
        """
        Get all library sections from Plex.
        
        Returns:
            List of library section dicts with 'key', 'title', 'type', and 'locations'.
        """
        if not self.is_configured:
            return []
        
        url = f"{self.server}/library/sections"
        
        try:
            session = await self._get_session()
            async with session.get(url, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    logger.warning(f"Plex: Failed to get library sections (HTTP {response.status})")
                    return []
                
                data = await response.json()
                sections = []
                for directory in data.get("MediaContainer", {}).get("Directory", []):
                    locations = []
                    for loc in directory.get("Location", []):
                        if "path" in loc:
                            locations.append(loc["path"])
                    
                    sections.append({
                        "key": directory.get("key"),
                        "title": directory.get("title"),
                        "type": directory.get("type"),
                        "locations": locations,
                    })
                return sections
                
        except Exception as e:
            logger.error(f"Plex: Error getting library sections: {e}")
            return []
    
    async def refresh_section_path(self, section_key: str, path: str) -> bool:
        """
        Trigger a partial scan of a specific path within a library section.
        
        This tells Plex to rescan just that path, picking up new/changed files.
        
        Args:
            section_key: The library section key.
            path: The file or folder path to refresh.
            
        Returns:
            True if refresh was initiated successfully.
        """
        if not self.is_configured:
            return False
        
        # URL encode the path for the query parameter
        from urllib.parse import quote
        url = f"{self.server}/library/sections/{section_key}/refresh"
        params = {"path": path}
        
        try:
            session = await self._get_session()
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    logger.info(f"Plex: Partial scan triggered for section {section_key}, path: {path}")
                    return True
                else:
                    logger.warning(f"Plex: Partial scan failed (HTTP {response.status})")
                    return False
        except Exception as e:
            logger.error(f"Plex: Error triggering partial scan: {e}")
            return False

    async def refresh_by_file_path(self, file_path: str) -> bool:
        """
        Refresh a specific file path in Plex by triggering a partial library scan.
        
        This finds which library section contains the file and triggers a 
        partial scan of that path, which is more reliable than search-based lookup.
        
        Args:
            file_path: Path to the media file.
            
        Returns:
            True if refresh was initiated successfully.
        """
        if not self.is_configured:
            return False
        
        from pathlib import Path
        
        logger.info(f"Plex: Looking for library containing: {file_path}")
        
        # Get all library sections and find which one contains this path
        sections = await self.get_library_sections()
        
        for section in sections:
            for location in section.get("locations", []):
                # Check if the file path starts with this library location
                if file_path.startswith(location):
                    logger.info(f"Plex: File is in library '{section['title']}' (section {section['key']})")
                    
                    # Trigger partial scan for the parent directory of the file
                    # This ensures Plex picks up the new subtitle file
                    parent_dir = str(Path(file_path).parent)
                    return await self.refresh_section_path(section["key"], parent_dir)
        
        logger.info(f"Plex: No library found containing path: {file_path}")
        return False


class JellyfinClient:
    """Async client for Jellyfin/Emby Server API."""
    
    def __init__(
        self,
        server: Optional[str] = None,
        token: Optional[str] = None,
        is_emby: bool = False
    ):
        """
        Initialize Jellyfin/Emby client.
        
        Args:
            server: Server URL (e.g., 'http://192.168.1.100:8096')
            token: Authentication token
            is_emby: True if this is an Emby server (minor API differences)
        """
        settings = get_settings()
        if is_emby:
            self.server = (server or settings.emby.server).rstrip('/')
            self.token = token or settings.emby.token
        else:
            self.server = (server or settings.jellyfin.server).rstrip('/')
            self.token = token or settings.jellyfin.token
        
        self.is_emby = is_emby
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def is_configured(self) -> bool:
        """Check if server is configured."""
        return bool(self.server and self.token)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"MediaBrowser Token={self.token}"},
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session
    
    async def close(self):
        """Close the client session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def refresh_metadata(self, item_id: str) -> bool:
        """
        Refresh metadata for a Jellyfin/Emby library item.
        
        Args:
            item_id: The ID of the item.
            
        Returns:
            True if refresh was initiated successfully.
        """
        if not self.is_configured:
            server_name = "Emby" if self.is_emby else "Jellyfin"
            logger.warning(f"{server_name} not configured, skipping metadata refresh")
            return False
        
        url = f"{self.server}/Items/{item_id}/Refresh"
        
        try:
            session = await self._get_session()
            async with session.post(url) as response:
                # Jellyfin returns 204 No Content on success
                if response.status in (200, 204):
                    server_name = "Emby" if self.is_emby else "Jellyfin"
                    logger.info(f"{server_name}: Metadata refresh sent for item {item_id}")
                    return True
                else:
                    server_name = "Emby" if self.is_emby else "Jellyfin"
                    logger.warning(f"{server_name}: Metadata refresh failed (HTTP {response.status}) for item {item_id}")
                    return False
        except aiohttp.ClientError as e:
            server_name = "Emby" if self.is_emby else "Jellyfin"
            logger.error(f"{server_name}: Metadata refresh error: {e}")
            return False
    
    async def get_file_path(self, item_id: str) -> Optional[str]:
        """
        Get file path for a Jellyfin/Emby item.
        
        Args:
            item_id: The ID of the item.
            
        Returns:
            File path or None if not found.
        """
        if not self.is_configured:
            return None
        
        url = f"{self.server}/Items/{item_id}"
        
        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                
                data = await response.json()
                return data.get("Path")
        except Exception as e:
            logger.error(f"Failed to get Jellyfin/Emby file path: {e}")
            return None
    
    async def refresh_by_file_path(self, file_path: str) -> bool:
        """
        Find an item by file path and refresh its metadata.
        
        Uses Jellyfin/Emby's search API to find the item by filename,
        then verifies the path and refreshes.
        
        Args:
            file_path: Path to the media file.
            
        Returns:
            True if item was found and refresh initiated.
        """
        if not self.is_configured:
            return False
        
        from pathlib import Path
        filename = Path(file_path).stem
        server_name = "Emby" if self.is_emby else "Jellyfin"
        
        # Search for the file in Jellyfin/Emby
        search_url = f"{self.server}/Items"
        params = {
            "searchTerm": filename,
            "IncludeItemTypes": "Episode,Movie",
            "Recursive": "true",
            "Fields": "Path",
            "Limit": "20",
        }
        
        try:
            session = await self._get_session()
            async with session.get(search_url, params=params) as response:
                if response.status != 200:
                    logger.warning(f"{server_name} search failed: HTTP {response.status}")
                    return False
                
                data = await response.json()
                items = data.get("Items", [])
                
                for item in items:
                    item_path = item.get("Path", "")
                    if item_path == file_path:
                        item_id = item.get("Id")
                        logger.info(f"Found {server_name} item {item_id} for {filename}")
                        return await self.refresh_metadata(item_id)
                
                logger.debug(f"No {server_name} item found for: {file_path}")
                return False
                
        except Exception as e:
            logger.error(f"{server_name} search error: {e}")
            return False


async def refresh_all_configured_servers(
    plex_item_id: Optional[str] = None,
    jellyfin_item_id: Optional[str] = None,
    emby_item_id: Optional[str] = None,
) -> dict:
    """
    Refresh metadata on all configured media servers.
    
    This is a convenience function that refreshes metadata on whichever
    servers are configured and have an item ID provided.
    
    Args:
        plex_item_id: Plex rating key (optional)
        jellyfin_item_id: Jellyfin item ID (optional)
        emby_item_id: Emby item ID (optional)
        
    Returns:
        Dict with refresh status for each server.
    """
    results = {}
    
    if plex_item_id:
        plex = PlexClient()
        try:
            results["plex"] = await plex.refresh_metadata(plex_item_id)
        finally:
            await plex.close()
    
    if jellyfin_item_id:
        jellyfin = JellyfinClient(is_emby=False)
        try:
            results["jellyfin"] = await jellyfin.refresh_metadata(jellyfin_item_id)
        finally:
            await jellyfin.close()
    
    if emby_item_id:
        emby = JellyfinClient(is_emby=True)
        try:
            results["emby"] = await emby.refresh_metadata(emby_item_id)
        finally:
            await emby.close()
    
    return results


async def refresh_by_file_path(file_path: str) -> dict:
    """
    Search for a file in all configured media servers and refresh metadata.
    
    This is useful for UI batch jobs where we don't have item IDs.
    Will search Plex, Jellyfin, and Emby if configured.
    
    Args:
        file_path: Path to the media file.
        
    Returns:
        Dict with refresh status for each server that was tried.
    """
    settings = get_settings()
    results = {}
    
    logger.info(f"Attempting media server refresh for: {file_path}")
    
    # Try Plex
    if settings.plex.is_configured:
        plex = PlexClient()
        try:
            results["plex"] = await plex.refresh_by_file_path(file_path)
        except Exception as e:
            logger.warning(f"Plex: Refresh by path failed: {e}")
            results["plex"] = False
        finally:
            await plex.close()
    
    # Try Jellyfin
    if settings.jellyfin.is_configured:
        jellyfin = JellyfinClient(is_emby=False)
        try:
            results["jellyfin"] = await jellyfin.refresh_by_file_path(file_path)
        except Exception as e:
            logger.warning(f"Jellyfin: Refresh by path failed: {e}")
            results["jellyfin"] = False
        finally:
            await jellyfin.close()
    
    # Try Emby
    if settings.emby.is_configured:
        emby = JellyfinClient(is_emby=True)
        try:
            results["emby"] = await emby.refresh_by_file_path(file_path)
        except Exception as e:
            logger.warning(f"Emby: Refresh by path failed: {e}")
            results["emby"] = False
        finally:
            await emby.close()
    
    return results
