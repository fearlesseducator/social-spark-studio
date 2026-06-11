"""
tools/firestore_tool.py

The single place all Firestore reads and writes happen.

Collection structure (one document per founder):

    message_dna     / {founder_id}   — MessageDNA as a nested dict
    campaign_brief  / {founder_id}   — CampaignBrief as a nested dict
    transcript      / {founder_id}   — TranscriptResult as a nested dict
    moments         / {founder_id}   — MomentSelectionResult as a nested dict
    post_drafts     / {founder_id}   — PostDraftSet as a nested dict
    voice_session   / {founder_id}   — VoiceConversationSession as a nested dict

Session key: a single founder_id ("default_founder") for now.
In production this becomes the Firebase Auth user ID.

Client setup:
    - Uses GOOGLE_CLOUD_PROJECT env var for the project.
    - On Cloud Run, credentials come automatically from the attached
      service account — GOOGLE_APPLICATION_CREDENTIALS is NOT required.
    - Locally, GOOGLE_APPLICATION_CREDENTIALS (if set in .env) is picked
      up automatically by the google-cloud-firestore client library.
"""

import os

from dotenv import load_dotenv
load_dotenv()

DEFAULT_FOUNDER_ID = "default_founder"

# Lazy singleton — created on first use so importing this module never
# fails when Firestore isn't installed/configured (local file mode).
_client = None


def _get_client():
    """Return the shared Firestore client, creating it on first use."""
    global _client
    if _client is not None:
        return _client

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    if not project:
        raise RuntimeError(
            "Firestore unavailable: GOOGLE_CLOUD_PROJECT is not set.\n"
            "  Add it to your .env file (local) or Cloud Run env vars:\n"
            "    GOOGLE_CLOUD_PROJECT=your-gcp-project-id"
        )

    try:
        from google.cloud import firestore
    except ImportError as exc:
        raise RuntimeError(
            "Firestore unavailable: google-cloud-firestore is not installed.\n"
            "  pip install google-cloud-firestore"
        ) from exc

    try:
        _client = firestore.Client(project=project)
    except Exception as exc:
        raise RuntimeError(
            f"Firestore client failed to initialise for project '{project}'.\n"
            "  - On Cloud Run: attach a service account with the "
            "'Cloud Datastore User' role.\n"
            "  - Locally: set GOOGLE_APPLICATION_CREDENTIALS to a service "
            "account key with Firestore access.\n"
            "  - Make sure the Firestore database exists:\n"
            f"      gcloud firestore databases create --project={project} "
            "--location=us-central1 --type=firestore-native\n"
            f"  Underlying error: {type(exc).__name__}: {exc}"
        ) from exc

    return _client


def save_document(collection: str, doc_id: str, data: dict) -> None:
    """Write (overwrite) one document."""
    _get_client().collection(collection).document(doc_id).set(data)


def load_document(collection: str, doc_id: str) -> dict | None:
    """Read one document. Returns None if it does not exist."""
    snap = _get_client().collection(collection).document(doc_id).get()
    return snap.to_dict() if snap.exists else None


def document_exists(collection: str, doc_id: str) -> bool:
    """Return True if the document exists."""
    return _get_client().collection(collection).document(doc_id).get().exists


def delete_document(collection: str, doc_id: str) -> None:
    """Delete one document. No error if it does not exist."""
    _get_client().collection(collection).document(doc_id).delete()
