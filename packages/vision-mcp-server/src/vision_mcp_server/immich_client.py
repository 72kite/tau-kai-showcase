import os


class ImmichClient:
    """Immich photo library integration (dormant). Enables learning from personal photos.

    Immich (https://immich.app) is a self-hosted photo management system. This client allows
    vision-mcp-server to access personal photos for face enrollment, re-identification, and
    visual learning without exposing photos externally.

    Not yet exposed as MCP tools — awaiting user decision on privacy model.
    """

    def __init__(self):
        self.enabled = os.getenv("IMMICH_ENABLED", "false").lower() == "true"
        self.api_url = os.getenv("IMMICH_API_URL", "http://localhost:2283")
        self.api_key = os.getenv("IMMICH_API_KEY", "")

    def _check_enabled(self):
        """Raise if Immich is not enabled."""
        if not self.enabled:
            raise RuntimeError("Immich integration disabled. Set IMMICH_ENABLED=true")
        if not self.api_key:
            raise RuntimeError("IMMICH_API_KEY not set")

    def search_by_person(self, person_id: str, limit: int = 10) -> list[dict]:
        """Search for photos of a specific person by Immich person ID.

        Returns list of photo metadata: {id, filename, date_taken, has_exif}.
        """
        self._check_enabled()
        try:
            import requests

            headers = {"x-api-key": self.api_key}
            resp = requests.get(
                f"{self.api_url}/api/search/metadata",
                params={"query": f"person:{person_id}", "limit": limit},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("assets", [])
        except ImportError:
            raise RuntimeError("requests library required for Immich integration")
        except Exception as e:
            raise RuntimeError(f"Immich search failed: {e}")

    def get_photo(self, photo_id: str) -> bytes:
        """Fetch a photo from Immich by ID. Returns JPEG bytes."""
        self._check_enabled()
        try:
            import requests

            headers = {"x-api-key": self.api_key}
            resp = requests.get(
                f"{self.api_url}/api/assets/{photo_id}/thumbnail",
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.content
        except ImportError:
            raise RuntimeError("requests library required for Immich integration")
        except Exception as e:
            raise RuntimeError(f"Immich photo fetch failed: {e}")

    def search_similar_faces(self, embedding: list[float], limit: int = 5) -> list[dict]:
        """Search Immich for photos with similar faces (requires Immich ML enabled).

        Passes a face embedding to Immich's face search and returns matching photo IDs.
        """
        self._check_enabled()
        try:
            import requests

            headers = {"x-api-key": self.api_key}
            resp = requests.post(
                f"{self.api_url}/api/search/smart",
                json={"embedding": embedding, "limit": limit},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
        except ImportError:
            raise RuntimeError("requests library required for Immich integration")
        except Exception as e:
            raise RuntimeError(f"Immich face search failed: {e}")

    def tag_photo(self, photo_id: str, person_id: str, person_name: str) -> dict:
        """Tag a photo in Immich with a recognized person's ID and name.

        Updates Immich metadata so the person is associated with the photo going forward.
        """
        self._check_enabled()
        try:
            import requests

            headers = {"x-api-key": self.api_key}
            resp = requests.patch(
                f"{self.api_url}/api/assets/{photo_id}",
                json={
                    "tagIds": [person_id],
                    "personIds": [person_id],
                },
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return {"photo_id": photo_id, "person_id": person_id, "tagged": True}
        except ImportError:
            raise RuntimeError("requests library required for Immich integration")
        except Exception as e:
            raise RuntimeError(f"Immich tagging failed: {e}")

    def list_people(self) -> list[dict]:
        """List all people/identities known to Immich.

        Returns list of {id, name, photo_count}.
        """
        self._check_enabled()
        try:
            import requests

            headers = {"x-api-key": self.api_key}
            resp = requests.get(
                f"{self.api_url}/api/people",
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("people", [])
        except ImportError:
            raise RuntimeError("requests library required for Immich integration")
        except Exception as e:
            raise RuntimeError(f"Immich list_people failed: {e}")

    def create_person(self, person_name: str) -> dict:
        """Create a new person identity in Immich.

        Returns {id, name, thumbnail}.
        """
        self._check_enabled()
        try:
            import requests

            headers = {"x-api-key": self.api_key}
            resp = requests.post(
                f"{self.api_url}/api/people",
                json={"name": person_name},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except ImportError:
            raise RuntimeError("requests library required for Immich integration")
        except Exception as e:
            raise RuntimeError(f"Immich create_person failed: {e}")
