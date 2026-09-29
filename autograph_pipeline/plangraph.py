"""The last stage: the project's new knowledge graph through the five stations.

`grasp_web.bridge` already runs Stations 1-5 over one project in-process; this
only connects it to the database the way the projects page does, so a pipeline
build and a Build PlanGraph click cannot disagree about what a build is.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping


def bridge_builder(
    env: Mapping[str, str] | None = None, provider: Any = None
) -> Callable[[str, Callable[[str, Any], None]], Any]:
    """A `build_plangraph(project, on_stage)` for `Pipeline`, over the live database."""

    def build(project: str, on_stage: Callable[[str, Any], None]) -> Any:
        from grasp_web import bridge
        from kg_read_harness.client import connect
        from kg_read_harness.config import load_config

        # Station 1 and 5 are configured for one project through PROJECT_NAME;
        # this build is for the project the pipeline just built, whatever the
        # environment names.
        scoped = dict(os.environ if env is None else env)
        scoped["PROJECT_NAME"] = project
        config = bridge.project_config(load_config(scoped), project)
        db = connect(config)
        # Station 5 may write as a different user than Station 1 reads as.
        writer = bridge.writer_config(project, dry_run=False, env=scoped)
        write_db = None if writer.arango.username == config.username else connect(writer.arango)
        return bridge.build(
            project, db, env=scoped, provider=provider, write_db=write_db, on_stage=on_stage
        )

    return build


__all__ = ["bridge_builder"]
