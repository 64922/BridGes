"""Contract tests: Python models and generated TypeScript types stay compatible.

The seam under test: the OpenAPI document generated from Python Pydantic models
contains the HealthProjection schema consumed by the Web UI.
"""

from science_companion.api.main import create_app


def test_openapi_contains_health_projection_schema() -> None:
    app = create_app()
    schema = app.openapi()
    schemas = schema["components"]["schemas"]

    assert "HealthProjection" in schemas
    projection = schemas["HealthProjection"]
    required = set(projection["required"])
    assert required == {"service", "version", "live", "ready", "degraded"}

    properties = projection["properties"]
    assert properties["live"]["$ref"].endswith("HealthStatus")
    assert properties["ready"]["$ref"].endswith("HealthStatus")
    assert properties["degraded"]["$ref"].endswith("HealthStatus")
    assert properties["dependencies"]["items"]["$ref"].endswith("DependencyHealth")

    status = schemas["HealthStatus"]
    assert set(status["enum"]) == {"pass", "fail", "unknown"}
