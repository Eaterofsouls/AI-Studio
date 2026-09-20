"""
Provider Registry for AI Cinema Studio Engine.
Routes generation tasks to appropriate provider adapters with automatic fallback.
"""

from typing import Dict, List, Optional
from cinema_engine.providers.base import (
    AudioProvider,
    BaseProvider,
    ImageProvider,
    LipSyncProvider,
    VideoProvider,
)
from cinema_engine.providers.elevenlabs_provider import ElevenLabsProvider
from cinema_engine.providers.fal_provider import FalProvider
from cinema_engine.providers.replicate_provider import ReplicateProvider
from cinema_engine.providers.synclabs_provider import SyncLabsProvider


class ProviderRegistry:
    """Central registry of configured media generation providers with fallback routing."""

    def __init__(self):
        self._video_providers: Dict[str, VideoProvider] = {}
        self._image_providers: Dict[str, ImageProvider] = {}
        self._audio_providers: Dict[str, AudioProvider] = {}
        self._lipsync_providers: Dict[str, LipSyncProvider] = {}

        # Instantiate adapters
        fal = FalProvider()
        replicate = ReplicateProvider()
        elevenlabs = ElevenLabsProvider()
        synclabs = SyncLabsProvider()

        # Register Video
        self.register_video_provider(fal)
        self.register_video_provider(replicate)

        # Register Image
        self.register_image_provider(fal)
        self.register_image_provider(replicate)

        # Register Audio
        self.register_audio_provider(elevenlabs)

        # Register LipSync
        self.register_lipsync_provider(synclabs)

    def register_video_provider(self, provider: VideoProvider) -> None:
        self._video_providers[provider.name.lower()] = provider

    def register_image_provider(self, provider: ImageProvider) -> None:
        self._image_providers[provider.name.lower()] = provider

    def register_audio_provider(self, provider: AudioProvider) -> None:
        self._audio_providers[provider.name.lower()] = provider

    def register_lipsync_provider(self, provider: LipSyncProvider) -> None:
        self._lipsync_providers[provider.name.lower()] = provider

    def get_video_provider(self, name: Optional[str] = None) -> VideoProvider:
        """
        Get requested video provider, or route to configured provider with fallback.
        Priority: explicitly requested -> fal (if configured) -> replicate (if configured) -> first available.
        """
        if name and name.lower() in self._video_providers:
            return self._video_providers[name.lower()]

        # Try configured providers in order
        for priority_name in ["fal", "replicate"]:
            prov = self._video_providers.get(priority_name)
            if prov and prov.is_configured():
                return prov

        # Default fallback
        if "fal" in self._video_providers:
            return self._video_providers["fal"]

        return next(iter(self._video_providers.values()))

    def get_image_provider(self, name: Optional[str] = None) -> ImageProvider:
        if name and name.lower() in self._image_providers:
            return self._image_providers[name.lower()]
        for priority_name in ["fal", "replicate"]:
            prov = self._image_providers.get(priority_name)
            if prov and prov.is_configured():
                return prov
        if "fal" in self._image_providers:
            return self._image_providers["fal"]
        return next(iter(self._image_providers.values()))

    def get_audio_provider(self, name: Optional[str] = None) -> AudioProvider:
        if name and name.lower() in self._audio_providers:
            return self._audio_providers[name.lower()]
        if "elevenlabs" in self._audio_providers:
            return self._audio_providers["elevenlabs"]
        return next(iter(self._audio_providers.values()))

    def get_lipsync_provider(self, name: Optional[str] = None) -> LipSyncProvider:
        if name and name.lower() in self._lipsync_providers:
            return self._lipsync_providers[name.lower()]
        if "synclabs" in self._lipsync_providers:
            return self._lipsync_providers["synclabs"]
        return next(iter(self._lipsync_providers.values()))

    def list_providers(self) -> Dict[str, Dict[str, bool]]:
        """Return all registered providers and whether they have valid credentials."""
        return {
            "video": {k: v.is_configured() for k, v in self._video_providers.items()},
            "image": {k: v.is_configured() for k, v in self._image_providers.items()},
            "audio": {k: v.is_configured() for k, v in self._audio_providers.items()},
            "lipsync": {k: v.is_configured() for k, v in self._lipsync_providers.items()},
        }


_registry: Optional[ProviderRegistry] = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
