"""Provision the Account Admin SP used by the data-access sync Job."""

from __future__ import annotations

import logging

from databricks.sdk.errors import NotFound, PermissionDenied
from databricks.sdk.service.postgres import Role, RoleAuthMethod, RoleIdentityType, RoleRoleSpec

from provisioning.context import ProvisioningContext
from provisioning.steps.base import ProvisioningStep

logger = logging.getLogger("provisioning")

_ENROLLMENT_TAG = "data_access_controlled"
_ENROLLMENT_VALUE = "true"


class GrantAdminSpPermissionsStep(ProvisioningStep):
    """Give the Account Admin SP everything the sync Job needs.

    - Own the app schema (and its current tables) so UC mirror DELETE/INSERT works.
    - Have SELECT/MODIFY/CREATE TABLE on the app schema for the App SP's companion grants.
    - Have MANAGE on every catalog tagged ``data_access_controlled=true`` so GRANT/REVOKE
      for ``dac_*`` groups works without relying on metastore-admin quirks.
    - Have a Lakebase Postgres role with DML on ``governance_app`` (role created here;
      table grants are applied when tables already exist, and re-applied by the app on
      startup after it creates them as the app SP).
    """

    name = "admin_sp_permissions"

    def run(self, ctx: ProvisioningContext) -> None:
        admin_sp = (ctx.admin_sp_client_id or "").strip()
        if not admin_sp:
            logger.warning("No --admin-sp-client-id / sync_job_run_as_sp; skipping Admin SP grants")
            return

        self._grant_app_schema(ctx, admin_sp)
        self._grant_enrolled_catalogs(ctx, admin_sp)
        self._ensure_lakebase_role(ctx, admin_sp)
        logger.info("Admin SP %s permissions ready", admin_sp)

    def _grant_app_schema(self, ctx: ProvisioningContext, admin_sp: str) -> None:
        schema = ctx.app_schema_fqn
        statements = [
            f"GRANT USE CATALOG ON CATALOG `{ctx.app_catalog}` TO `{admin_sp}`",
            (f"GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY ON SCHEMA {schema} TO `{admin_sp}`"),
            f"ALTER SCHEMA {schema} OWNER TO `{admin_sp}`",
        ]
        for statement in statements:
            ctx.sql.execute(statement)

        # Existing tables keep their prior owner; move them so DELETE/INSERT works.
        tables = ctx.sql.execute(
            f"""
            SELECT table_name FROM system.information_schema.tables
            WHERE table_catalog = '{ctx.app_catalog}'
              AND table_schema = '{ctx.app_schema}'
              AND table_type IN ('MANAGED', 'EXTERNAL')
            """
        )
        for row in tables:
            name = (row[0] or "").strip()
            if not name:
                continue
            ctx.sql.execute(
                f"ALTER TABLE `{ctx.app_catalog}`.`{ctx.app_schema}`.`{name}` OWNER TO `{admin_sp}`"
            )
        logger.info("Admin SP owns schema %s (%s tables reassigned)", schema, len(tables))

    def _grant_enrolled_catalogs(self, ctx: ProvisioningContext, admin_sp: str) -> None:
        rows = ctx.sql.execute(
            f"""
            SELECT catalog_name FROM system.information_schema.catalog_tags
            WHERE lower(tag_name) = '{_ENROLLMENT_TAG}'
              AND lower(tag_value) = '{_ENROLLMENT_VALUE}'
            """
        )
        catalogs = sorted({(r[0] or "").strip() for r in rows if r and r[0]})
        if not catalogs:
            logger.info(
                "No enrolled catalogs (tag %s=%s); skipping MANAGE grants", _ENROLLMENT_TAG, _ENROLLMENT_VALUE
            )
            return
        for catalog in catalogs:
            ctx.sql.execute(f"GRANT MANAGE ON CATALOG `{catalog}` TO `{admin_sp}`")
            ctx.sql.execute(f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{admin_sp}`")
        logger.info("Granted Admin SP MANAGE on enrolled catalogs: %s", ", ".join(catalogs))

    def _ensure_lakebase_role(self, ctx: ProvisioningContext, admin_sp: str) -> None:
        project = (ctx.lakebase_project_id or "").strip()
        if not project:
            logger.info("No lakebase_project_id; skipping Admin SP Lakebase role")
            return
        parent = f"projects/{project}/branches/production"
        role_id = f"dbrx-apps-{admin_sp}"
        role_name = f"{parent}/roles/{role_id}"
        try:
            ctx.client.postgres.get_role(name=role_name)
            logger.info("Lakebase role already exists for Admin SP: %s", role_id)
        except NotFound:
            try:
                ctx.client.postgres.create_role(
                    parent=parent,
                    role_id=role_id,
                    role=Role(
                        spec=RoleRoleSpec(
                            identity_type=RoleIdentityType.SERVICE_PRINCIPAL,
                            postgres_role=admin_sp,
                            auth_method=RoleAuthMethod.LAKEBASE_OAUTH_V1,
                        )
                    ),
                )
                logger.info("Created Lakebase role for Admin SP: %s", role_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not create Lakebase role for Admin SP: %s", exc)
                return
        except PermissionDenied as exc:
            logger.warning("Cannot read/create Lakebase role for Admin SP: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001
            # SDK shape varies; treat unknown errors as non-fatal for first deploy.
            logger.warning("Lakebase role check failed for Admin SP: %s", exc)
            return

        self._grant_lakebase_sql(ctx, admin_sp, parent)

    def _grant_lakebase_sql(self, ctx: ProvisioningContext, admin_sp: str, parent: str) -> None:
        """Best-effort DML grants when the app schema tables already exist."""
        try:
            import psycopg
            from psycopg import sql
        except ImportError:
            logger.info("psycopg not installed in setup env; Lakebase SQL grants deferred to app startup")
            return

        database_id = (ctx.lakebase_database_id or "governance").strip()
        try:
            endpoints = list(ctx.client.postgres.list_endpoints(parent=parent))
            if not endpoints:
                logger.warning("No Lakebase endpoints under %s", parent)
                return
            endpoint = endpoints[0]
            hosts = getattr(getattr(endpoint, "status", None), "hosts", None)
            host = getattr(hosts, "host", None) if hosts is not None else None
            if not host:
                logger.warning("Lakebase endpoint %s has no host", endpoint.name)
                return
            database = ctx.client.postgres.get_database(name=f"{parent}/databases/{database_id}")
            dbname = getattr(getattr(database, "status", None), "postgres_database", None) or database_id
            cred = ctx.client.postgres.generate_database_credential(endpoint=endpoint.name)
            me = ctx.client.current_user.me()
            user_name = (getattr(me, "user_name", None) or "").strip()
            conn = psycopg.connect(
                host=str(host),
                dbname=str(dbname),
                user=user_name,
                password=cred.token,
                sslmode="require",
                autocommit=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not open Lakebase connection for Admin SP grants: %s", exc)
            return

        try:
            cur = conn.cursor()
            role = sql.Identifier(admin_sp)
            cur.execute(
                sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(str(dbname)), role)
            )
            # Schema may not exist until the app starts; ignore missing-schema errors.
            try:
                cur.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA governance_app TO {}").format(role))
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'governance_app' ORDER BY 1")
                tables = [r[0] for r in cur.fetchall()]
                for table in tables:
                    cur.execute(
                        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON governance_app.{} TO {}").format(
                            sql.Identifier(table), role
                        )
                    )
                logger.info("Granted Admin SP Lakebase DML on governance_app (%s tables)", len(tables))
            except Exception as exc:  # noqa: BLE001
                logger.info("Lakebase schema grants deferred (schema/tables may not exist yet): %s", exc)
        finally:
            conn.close()
