# petconnect-18446-498ae195

## Pet Image Uploads: Storage and Usage Policy

- Any pet photo uploaded for a listing is:
    - Saved on the backend under the `/uploads/` directory.
    - Only made accessible (read-only) to the frontend for display via the endpoint `/static/uploads/{filename}`.
    - Not used for any backend processing (including AI/ML, moderation), nor for any external integrations.
    - The image path (photo_url) is only to be referenced by pet listing components in the frontend dashboard or grid.
    - It is NOT to be sent to any external service or used for any computation.

> For security, never process or forward /static/uploads URLs outside display in the pet dashboard.
