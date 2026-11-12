# Recovery export

Snapshot of app-owned governance state as YAML, for restoring the app after a failure.
The **Recovery** page (admin only) builds the files and shows a preview. **Push to Git** commits
the same files to the saved repository and branch.

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

Git runs through the `git` command-line client in the app runtime. The token is passed as an
`http.extraHeader` via `GIT_CONFIG_*` environment variables: it never appears in the remote URL,
process arguments, logs, or error messages. Pushes are rejected when the branch moved since the
clone (a race with another push); retry the push to take a fresh snapshot.

Add the token once with the Databricks CLI:

```bash
databricks secrets put-secret databricks-governance-app git-token --string-value "<github-pat>" --profile <profile>
```

The app service principal needs READ on the `databricks-governance-app` scope.

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
- `POST /api/recovery/export/push`: commits the export; returns the commit SHA and whether it changed.

Implementation: `backend/app/services/recovery.py`, `backend/app/clients/git_publisher.py`,
`backend/app/models/recovery.py`, `backend/app/api/routes/recovery.py`.
