# Recovery export

Snapshot of app-owned governance state as YAML, for restoring the app after a failure.
Phase 1 (current) builds the files and shows a preview on the **Recovery** page (admin only).
Nothing is written to Git yet.

## Files

Written under `path_prefix` in the configured repository (`manifest.yaml` first):

| File | Contents |
| --- | --- |
| `manifest.yaml` | Format version, timestamp, author, storage backend, app catalog/schema, file list |
| `settings/data_access.yaml` | Data-access sync settings (secret-looking keys redacted) |
| `identity/attribute_definitions.yaml` | Identity attribute definitions and their vocabularies |
| `identity/assignments.yaml` | Active identity assignments (revoked and expired rows omitted) |
| `approvals/category_approvers.yaml` | Category and dimension approvers |
| `masking/rules.yaml` | Mask rule registry (including custom expressions) |
| `masking/config.yaml` | Joinable salt status only; the value is redacted and never exported |
| `exceptions/policy_exceptions.yaml` | User-added policy exceptions (system principals omitted) |

Lists are sorted by stable keys so repeated exports diff cleanly.

## Not exported

- Access requests, approval history and audit events (v1).
- The joinable salt value. Keep a copy outside Git; joinable hashes cannot be reproduced without it.
- The Git token. The settings store only the name of the secret-scope key that holds it.
- Unity Catalog objects (tags, ABAC policies, UDFs, grants). Those are recreated by sync and setup.

## Settings

`GET`/`PUT /api/recovery/settings` store the repository URL (https only), branch, path prefix,
Git user name, and the token secret key name. Path and branch values reject `..` and absolute paths.

## API

- `GET /api/recovery/settings`
- `PUT /api/recovery/settings`
- `POST /api/recovery/export/preview`: returns the files with their YAML content, target, and warnings.

Implementation: `backend/app/services/recovery.py`, `backend/app/models/recovery.py`,
`backend/app/api/routes/recovery.py`.
