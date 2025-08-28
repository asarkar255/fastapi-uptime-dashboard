# Uptime Dashboard with Wake Button

Per-row **Wake** button calls `POST /api/wake`, which pings a configurable `wake_url` (or the service URL by default) and then immediately re-checks status.

Configure per service in `config/services.yaml`:
- wake_url (defaults to url)
- wake_method (GET/POST...)
- wake_headers
- wake_body

Useful to warm up Render free instances that have spun down.
