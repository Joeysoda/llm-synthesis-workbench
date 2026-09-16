from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_load(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                input_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                log_path TEXT,
                artifact_path TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS samples (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                reasoning TEXT NOT NULL,
                source_json TEXT NOT NULL,
                generation_json TEXT NOT NULL,
                quality_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS assets_project_idx ON assets(project_id)",
            "CREATE INDEX IF NOT EXISTS runs_project_idx ON runs(project_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id, id)",
            "CREATE INDEX IF NOT EXISTS samples_run_idx ON samples(run_id)",
            """
            CREATE TABLE IF NOT EXISTS pipelines (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                domain TEXT NOT NULL DEFAULT 'general',
                project_id TEXT,
                nodes_json TEXT NOT NULL,
                edges_json TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                project_id TEXT,
                status TEXT NOT NULL,
                current_node TEXT NOT NULL DEFAULT '',
                nodes_json TEXT NOT NULL,
                error TEXT,
                result_json TEXT,
                artifact_path TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY(pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pipeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                domain TEXT NOT NULL,
                project_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS dataset_versions (
                id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                quality_json TEXT NOT NULL,
                artifact_path TEXT,
                created_at TEXT NOT NULL,
                published_at TEXT,
                FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE,
                UNIQUE(dataset_id, version)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS lineage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_version_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(dataset_version_id) REFERENCES dataset_versions(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS pipeline_runs_pipeline_idx ON pipeline_runs(pipeline_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS pipeline_events_run_idx ON pipeline_events(pipeline_run_id, id)",
            "CREATE INDEX IF NOT EXISTS dataset_versions_dataset_idx ON dataset_versions(dataset_id, created_at DESC)",
        ]
        with self.connect() as connection:
            for statement in statements:
                connection.execute(statement)
            project_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(projects)")
            }
            if "tool_id" not in project_columns:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN tool_id TEXT NOT NULL DEFAULT 'legacy'"
                )
            if "display_name" not in project_columns:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "UPDATE projects SET display_name = name WHERE display_name = ''"
                )
            if "archived_at" not in project_columns:
                connection.execute("ALTER TABLE projects ADD COLUMN archived_at TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS projects_tool_display_name_idx
                ON projects(tool_id, display_name)
                """
            )

            run_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)")
            }
            if "workflow_type" not in run_columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN workflow_type TEXT NOT NULL DEFAULT ''"
                )
            if "current_stage" not in run_columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN current_stage TEXT NOT NULL DEFAULT ''"
                )
            if "stages_json" not in run_columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN stages_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "progress_json" not in run_columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'"
                )

    def create_pipeline(self, data: dict[str, Any]) -> dict[str, Any]:
        pipeline_id = str(uuid.uuid4())
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO pipelines(id,name,description,domain,project_id,nodes_json,edges_json,version,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,1,?,?)""",
                (pipeline_id, data["name"].strip(), data.get("description", "").strip(),
                 data.get("domain", "general"), data.get("project_id"), json_dump(data["nodes"]),
                 json_dump(data["edges"]), timestamp, timestamp),
            )
        return self.get_pipeline(pipeline_id) or {}

    def list_pipelines(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM pipelines ORDER BY updated_at DESC").fetchall()
        return [self._decode_pipeline(row) for row in rows]

    def get_pipeline(self, pipeline_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)).fetchone()
        return self._decode_pipeline(row) if row else None

    def update_pipeline(self, pipeline_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        pipeline = self.get_pipeline(pipeline_id)
        if not pipeline:
            return None
        values: dict[str, Any] = {"updated_at": now_iso(), "version": int(pipeline["version"]) + 1}
        for key in ("name", "description"):
            if key in data and data[key] is not None:
                values[key] = str(data[key]).strip()
        if data.get("nodes") is not None:
            values["nodes_json"] = json_dump(data["nodes"])
        if data.get("edges") is not None:
            values["edges_json"] = json_dump(data["edges"])
        columns = ", ".join(f"{key} = ?" for key in values)
        with self.connect() as connection:
            connection.execute(f"UPDATE pipelines SET {columns} WHERE id = ?", (*values.values(), pipeline_id))
        return self.get_pipeline(pipeline_id)

    @staticmethod
    def _decode_pipeline(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["nodes"] = json_load(data.pop("nodes_json"), [])
        data["edges"] = json_load(data.pop("edges_json"), [])
        return data

    def create_pipeline_run(self, pipeline_id: str, project_id: str | None, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        timestamp = now_iso()
        state = [{**node, "status": "pending"} for node in nodes]
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO pipeline_runs(id,pipeline_id,project_id,status,nodes_json,created_at) VALUES(?,?,?,'queued',?,?)",
                (run_id, pipeline_id, project_id, json_dump(state), timestamp),
            )
        return self.get_pipeline_run(run_id) or {}

    def add_pipeline_event(
        self, pipeline_run_id: str, event_type: str, message: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        timestamp = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO pipeline_events(pipeline_run_id,event_type,message,data_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (pipeline_run_id, event_type, message, json_dump(data or {}), timestamp),
            )
            event_id = cursor.lastrowid
            row = connection.execute("SELECT * FROM pipeline_events WHERE id = ?", (event_id,)).fetchone()
        return self._decode_pipeline_event(row)

    def list_pipeline_events(self, pipeline_run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pipeline_events WHERE pipeline_run_id=? AND id>? ORDER BY id",
                (pipeline_run_id, after_id),
            ).fetchall()
        return [self._decode_pipeline_event(row) for row in rows]

    @staticmethod
    def _decode_pipeline_event(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["data"] = json_load(item.pop("data_json"), {})
        return item

    def get_pipeline_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_pipeline_run(row) if row else None

    def get_latest_pipeline_run(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return self._decode_pipeline_run(row) if row else None

    def update_pipeline_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"status", "current_node", "nodes_json", "error", "result_json", "artifact_path", "started_at", "completed_at"}
        values = {key: value for key, value in fields.items() if key in allowed}
        for key in ("nodes_json", "result_json"):
            if key in values and not isinstance(values[key], str):
                values[key] = json_dump(values[key])
        if not values:
            return self.get_pipeline_run(run_id)
        columns = ", ".join(f"{key} = ?" for key in values)
        with self.connect() as connection:
            connection.execute(f"UPDATE pipeline_runs SET {columns} WHERE id = ?", (*values.values(), run_id))
        return self.get_pipeline_run(run_id)

    @staticmethod
    def _decode_pipeline_run(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["nodes"] = json_load(data.pop("nodes_json"), [])
        data["result"] = json_load(data.pop("result_json"), None)
        return data

    def create_dataset_version(self, *, name: str, domain: str, project_id: str | None, manifest: dict[str, Any], quality: dict[str, Any], artifact_path: str | None) -> dict[str, Any]:
        dataset_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute("INSERT INTO datasets(id,name,domain,project_id,created_at) VALUES(?,?,?,?,?)", (dataset_id, name, domain, project_id, timestamp))
            connection.execute("INSERT INTO dataset_versions(id,dataset_id,version,status,manifest_json,quality_json,artifact_path,created_at) VALUES(?,?,?,'review_required',?,?,?,?)", (version_id, dataset_id, "v1", json_dump(manifest), json_dump(quality), artifact_path, timestamp))
            connection.execute("INSERT INTO lineage_events(dataset_version_id,event_type,detail_json,created_at) VALUES(?,?,?,?)", (version_id, "created", json_dump({"synthetic": manifest.get("synthetic", False)}), timestamp))
        return self.get_dataset_version(version_id) or {}

    def list_datasets(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT d.*, p.display_name AS project_name,
                          v.id AS version_id, v.version, v.status, v.quality_json,
                          v.manifest_json, v.artifact_path,
                          v.created_at AS version_created_at
                   FROM datasets d
                   LEFT JOIN projects p ON p.id = d.project_id
                   LEFT JOIN dataset_versions v ON v.id = (
                       SELECT id FROM dataset_versions x
                       WHERE x.dataset_id = d.id ORDER BY x.created_at DESC LIMIT 1
                   )
                   ORDER BY d.created_at DESC"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["quality"] = json_load(item.pop("quality_json", None), {})
            item["manifest"] = json_load(item.pop("manifest_json", None), {})
            result.append(item)
        return result

    def get_dataset_version(self, version_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT v.*, d.name AS dataset_name, d.domain, d.project_id FROM dataset_versions v JOIN datasets d ON d.id=v.dataset_id WHERE v.id=?", (version_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["manifest"] = json_load(item.pop("manifest_json"), {})
        item["quality"] = json_load(item.pop("quality_json"), {})
        return item

    def update_dataset_version_metadata(
        self,
        version_id: str,
        *,
        manifest: dict[str, Any] | None = None,
        quality: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        updates: dict[str, str] = {}
        if manifest is not None:
            updates["manifest_json"] = json_dump(manifest)
        if quality is not None:
            updates["quality_json"] = json_dump(quality)
        if updates:
            columns = ", ".join(f"{key} = ?" for key in updates)
            with self.connect() as connection:
                connection.execute(
                    f"UPDATE dataset_versions SET {columns} WHERE id = ?",
                    (*updates.values(), version_id),
                )
        return self.get_dataset_version(version_id)

    def list_dataset_versions(self, dataset_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM dataset_versions WHERE dataset_id=? ORDER BY created_at DESC", (dataset_id,)).fetchall()
        return [self.get_dataset_version(row["id"]) for row in rows]

    def publish_dataset_version(self, version_id: str) -> dict[str, Any] | None:
        version = self.get_dataset_version(version_id)
        if not version:
            return None
        if not version["quality"].get("passed"):
            raise ValueError("质量门禁未通过，不能发布")
        with self.connect() as connection:
            connection.execute("UPDATE dataset_versions SET status='published', published_at=? WHERE id=?", (now_iso(), version_id))
        return self.get_dataset_version(version_id)

    def list_lineage_events(self, version_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM lineage_events WHERE dataset_version_id=? ORDER BY id", (version_id,)).fetchall()
        return [{**dict(row), "detail": json_load(row["detail_json"], {})} for row in rows]

    def create_project(self, name: str, description: str = "") -> dict[str, Any]:
        project_id = str(uuid.uuid4())
        created_at = now_iso()
        display_name = name.strip()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    id, name, display_name, description, tool_id, created_at
                ) VALUES (?, ?, ?, ?, 'legacy', ?)
                """,
                (project_id, display_name, display_name, description.strip(), created_at),
            )
        return self.get_project(project_id)

    def create_tool_project(
        self, tool_id: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        project_id = str(uuid.uuid4())
        created_at = now_iso()
        display_name = name.strip()
        internal_name = f"{tool_id}:{project_id}:{display_name}"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    id, name, display_name, description, tool_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    internal_name,
                    display_name,
                    description.strip(),
                    tool_id,
                    created_at,
                ),
            )
        return self.get_project(project_id)

    def list_projects(
        self, tool_id: str | None = None, include_archived: bool = True
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[Any] = []
        if tool_id:
            conditions.append("p.tool_id = ?")
            values.append(tool_id)
        if not include_archived:
            conditions.append("p.archived_at IS NULL")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*,
                       (SELECT COUNT(*) FROM assets a WHERE a.project_id = p.id) AS asset_count,
                       (SELECT COUNT(*) FROM runs r WHERE r.project_id = p.id) AS run_count,
                       (SELECT r.id FROM runs r
                        WHERE r.project_id = p.id
                        ORDER BY r.created_at DESC LIMIT 1) AS latest_run_id
                FROM projects p {where} ORDER BY p.created_at DESC
                """,
                values,
            ).fetchall()
        return [self._decode_project(row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return self._decode_project(row) if row else None

    def update_project(
        self,
        project_id: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any] | None:
        project = self.get_project(project_id)
        if not project:
            return None
        updates: dict[str, Any] = {}
        if display_name is not None:
            value = display_name.strip()
            updates["display_name"] = value
            updates["name"] = f"{project['tool_id']}:{project_id}:{value}"
        if description is not None:
            updates["description"] = description.strip()
        if archived is not None:
            updates["archived_at"] = now_iso() if archived else None
        if updates:
            columns = ", ".join(f"{key} = ?" for key in updates)
            with self.connect() as connection:
                connection.execute(
                    f"UPDATE projects SET {columns} WHERE id = ?",
                    (*updates.values(), project_id),
                )
        return self.get_project(project_id)

    @staticmethod
    def _decode_project(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["display_name"] = item.get("display_name") or item["name"]
        return item

    def create_asset(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO assets(
                    id, project_id, filename, path, content_type, size, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    data["project_id"],
                    data["filename"],
                    data["path"],
                    data["content_type"],
                    data["size"],
                    data["sha256"],
                    data["created_at"],
                ),
            )
        return self.get_asset(data["id"])

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_assets(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assets WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_run(
        self,
        project_id: str,
        tool_name: str,
        input_data: dict[str, Any],
        *,
        workflow_type: str = "",
        stages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        created_at = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    id, project_id, tool_name, status, input_json, workflow_type,
                    stages_json, progress_json, created_at
                )
                VALUES (?, ?, ?, 'queued', ?, ?, ?, '{}', ?)
                """,
                (
                    run_id,
                    project_id,
                    tool_name,
                    json_dump(input_data),
                    workflow_type,
                    json_dump(stages or []),
                    created_at,
                ),
            )
        return self.get_run(run_id)

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "result_json",
            "error",
            "log_path",
            "artifact_path",
            "started_at",
            "completed_at",
            "current_stage",
            "stages_json",
            "progress_json",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return self.get_run(run_id)
        if "result_json" in values and not isinstance(values["result_json"], str):
            values["result_json"] = json_dump(values["result_json"])
        for key in ("stages_json", "progress_json"):
            if key in values and not isinstance(values[key], str):
                values[key] = json_dump(values[key])
        columns = ", ".join(f"{key} = ?" for key in values)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE runs SET {columns} WHERE id = ?",
                (*values.values(), run_id),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._decode_run(row) if row else None

    def set_run_stage(
        self,
        run_id: str,
        stage_id: str,
        *,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if not run:
            return None
        stages = run.get("stages") or []
        found = False
        for stage in stages:
            if stage["id"] == stage_id:
                stage["status"] = "running"
                found = True
            elif not found and stage.get("status") == "running":
                stage["status"] = "succeeded"
        return self.update_run(
            run_id,
            current_stage=stage_id,
            stages_json=stages,
            progress_json=progress or run.get("progress") or {},
        )

    def finish_run_stages(self, run_id: str, succeeded: bool) -> None:
        run = self.get_run(run_id)
        if not run:
            return
        stages = run.get("stages") or []
        marked_failure = False
        for stage in stages:
            if succeeded:
                stage["status"] = "succeeded"
            elif stage.get("status") == "running":
                stage["status"] = "failed"
                marked_failure = True
        if not succeeded and not marked_failure:
            for stage in stages:
                if stage.get("status") == "pending":
                    stage["status"] = "failed"
                    break
        current_stage = run.get("current_stage")
        if succeeded and stages:
            current_stage = stages[-1]["id"]
        elif not succeeded:
            failed_stage = next(
                (stage for stage in stages if stage.get("status") == "failed"),
                None,
            )
            if failed_stage:
                current_stage = failed_stage["id"]
        self.update_run(
            run_id,
            current_stage=current_stage,
            stages_json=stages,
        )

    def list_runs(
        self,
        project_id: str | None = None,
        limit: int = 100,
        *,
        tool_id: str | None = None,
        status: str | None = None,
        workflow_type: str | None = None,
        artifacts_only: bool = False,
    ) -> list[dict]:
        conditions: list[str] = []
        values: list[Any] = []
        if project_id:
            conditions.append("r.project_id = ?")
            values.append(project_id)
        if tool_id:
            conditions.append("p.tool_id = ?")
            values.append(tool_id)
        if status:
            conditions.append("r.status = ?")
            values.append(status)
        if workflow_type:
            conditions.append("r.workflow_type = ?")
            values.append(workflow_type)
        if artifacts_only:
            conditions.append("r.artifact_path IS NOT NULL")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, p.tool_id, p.display_name AS project_name
                FROM runs r JOIN projects p ON p.id = r.project_id
                {where}
                ORDER BY r.created_at DESC LIMIT ?
                """,
                (*values, limit),
            ).fetchall()
        return [self._decode_run(row) for row in rows]

    def _decode_run(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["input"] = json_load(data.pop("input_json"), {})
        data["result"] = json_load(data.pop("result_json"), None)
        data["stages"] = json_load(data.pop("stages_json", None), [])
        data["progress"] = json_load(data.pop("progress_json", None), {})
        return data

    def append_event(
        self,
        run_id: str,
        message: str,
        event_type: str = "log",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        created_at = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events(run_id, event_type, message, data_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, event_type, message, json_dump(data or {}), created_at),
            )
            event_id = cursor.lastrowid
        return {
            "id": event_id,
            "run_id": run_id,
            "event_type": event_type,
            "message": message,
            "data": data or {},
            "created_at": created_at,
        }

    def list_events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id ASC",
                (run_id, after_id),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json_load(item.pop("data_json"), {})
            result.append(item)
        return result

    def insert_samples(
        self, project_id: str, run_id: str, samples: list[dict[str, Any]]
    ) -> int:
        created_at = now_iso()
        rows = []
        for index, sample in enumerate(samples):
            sample_id = str(sample.get("id") or f"{run_id}/{index + 1}")
            rows.append(
                (
                    sample_id,
                    run_id,
                    project_id,
                    sample.get("task_type", "unknown"),
                    str(sample.get("question", "")),
                    str(sample.get("answer", "")),
                    str(sample.get("reasoning", "")),
                    json_dump(sample.get("source", {})),
                    json_dump(sample.get("generation", {})),
                    json_dump(
                        sample.get(
                            "quality",
                            {
                                "rule_passed": None,
                                "model_score": 0,
                                "human_status": "pending",
                            },
                        )
                    ),
                    created_at,
                )
            )
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO samples(
                    id, run_id, project_id, task_type, question, answer, reasoning,
                    source_json, generation_json, quality_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def list_samples(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM samples WHERE run_id = ? ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
        return [self._decode_sample(row) for row in rows]

    def get_sample(self, sample_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM samples WHERE id = ?", (sample_id,)
            ).fetchone()
        return self._decode_sample(row) if row else None

    def update_sample(self, sample_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        sample = self.get_sample(sample_id)
        if not sample:
            return None
        allowed_text = {"question", "answer", "reasoning"}
        updates = {key: patch[key] for key in allowed_text if key in patch}
        if "quality" in patch:
            quality = {**sample["quality"], **patch["quality"]}
            updates["quality_json"] = json_dump(quality)
        if not updates:
            return sample
        columns = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE samples SET {columns} WHERE id = ?",
                (*updates.values(), sample_id),
            )
        return self.get_sample(sample_id)

    def _decode_sample(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["source"] = json_load(item.pop("source_json"), {})
        item["generation"] = json_load(item.pop("generation_json"), {})
        item["quality"] = json_load(item.pop("quality_json"), {})
        return item
