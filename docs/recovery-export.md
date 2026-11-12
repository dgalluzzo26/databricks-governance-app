# Recovery export and import

Snapshot of app-owned governance state as YAML. Use **export / push** to back up a healthy app,
and **import / recover** to seed a **new** app (different workspace or fresh storage) from that
snapshot. The **Recovery** page is admin only.

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

## Push

`POST /api/recovery/export/push` (admin only):

1. Builds the export (same files as the preview).
2. Reads the token from the admin secret scope (`databricks-governance-app`) under the saved
   `tokenSecretKey`, using the app service principal. The token is read at push time only.
3. Clones only the branch tip (`--depth 1`), replaces every file under `path_prefix` with the
   export, and leaves the rest of the branch alone. Files under the prefix that the export no
   longer produces are removed.
4. Commits only when the files changed, then pushes. An unchanged export pushes nothing.
5. Writes an audit event `recovery.export_pushed` (repository, branch, path, commit, file count).

The branch is created from the export alone when it does not exist. The commit author is the
saved Git user name (or `governance-app`) with the admin's email.

## Import and recover

Two steps on the Recovery page:

1. **Import from Git** (`POST /api/recovery/import`) — shallow-fetches the branch, reads files under
   `path_prefix`, parses YAML, and returns them with an occupancy report for the current app.
   Writes nothing.
2. **Recover** (`POST /api/recovery/recover` with `{ "confirm": true }`) — pulls again (does not
   trust a stale browser payload), then upserts into app storage.

### Apply semantics

- Upsert / merge by stable key (not wipe-and-replace):
  - Definitions by `key`
  - Assignments by `assignment_id`
  - Approvers by `approver_id`
  - Mask rules by `rule_key`
  - Policy exceptions by `exception_id` (snapshot rows with `system: true` are skipped)
  - Data-access settings: overwrite the `data_access` settings blob; redacted secret-looking
    values are dropped and never written back as placeholders
- Local rows **not** in the snapshot are **kept**.
- The joinable salt is never restored from Git. If the snapshot says it was configured and the
  target app has none, recover warns the operator to set it manually.
- Recover does **not** run identity UC mirror sync, mask-rule pack sync, or data-access apply.
  Re-run those from their own pages after recovering so Unity Catalog matches.

### Occupancy warning

Import reports counts of definitions, active assignments, approvers, mask rules, non-system
exceptions, and whether data-access settings exist. When any are present, the UI warns and the
confirm dialog for Recover says existing rows will be upserted/merged. Prefer an empty app when
seeding a new workspace.

Recover requires `confirm=true` and writes audit event `recovery.imported`.

## Git auth

Git runs through the `git` command-line client in the app runtime. The token is passed as an
`http.extraHeader` via `GIT_CONFIG_*` environment variables: it never appears in the remote URL,
process arguments, logs, or error messages. Pushes are rejected when the branch moved since the
clone (a race with another push); retry the push to take a fresh snapshot.

Add the token once with the Databricks CLI:

```bash
databricks secrets put-secret databricks-governance-app git-token --string-value "<github-pat>" --profile <profile>
```

The app service principal needs READ on the `databricks-governance-app` scope.

## Not exported / not restored

- Access requests, approval history and audit events (v1).
- The joinable salt value. Keep a copy outside Git; joinable hashes cannot be reproduced without it.
- The Git token. The settings store only the name of the secret-scope key that holds it.
- Unity Catalog objects (tags, ABAC policies, UDFs, grants). Those are recreated by sync and setup.

## Settings

`GET`/`PUT /api/recovery/settings` store the repository URL (https only), branch, path prefix,
Git user name, and the token secret key name. Path and branch values reject `..` and absolute paths.
Configure these on the new app before importing; Git settings are not transferred automatically.

## API

- `GET /api/recovery/settings`
- `PUT /api/recovery/settings`
- `POST /api/recovery/export/preview`: returns the files with their YAML content, target, and warnings.
- `POST /api/recovery/export/push`: commits the export; returns the commit SHA and whether it changed.
- `POST /api/recovery/import`: reads the snapshot; returns files, occupancy, and warnings.
- `POST /api/recovery/recover`: upserts the snapshot; body `{ "confirm": true }`.

Implementation: `backend/app/services/recovery.py`, `backend/app/clients/git_publisher.py`,
`backend/app/models/recovery.py`, `backend/app/api/routes/recovery.py`.
