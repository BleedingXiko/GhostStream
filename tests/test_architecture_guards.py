import re
from pathlib import Path

from ghoststream.server.domain.jobs.models import Job


REPO_ROOT = Path(__file__).resolve().parent.parent
GHOSTSTREAM_ROOT = REPO_ROOT / "ghoststream"
DOC_SURFACE_ROOTS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs",
    REPO_ROOT / "examples",
)


def _iter_python_files():
    for path in GHOSTSTREAM_ROOT.rglob("*.py"):
        yield path


def _iter_release_surface_files():
    for root in DOC_SURFACE_ROOTS:
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.is_file():
                yield path


def test_deleted_legacy_modules_do_not_return() -> None:
    assert not (GHOSTSTREAM_ROOT / "runtime.py").exists()
    assert list((GHOSTSTREAM_ROOT / "api").rglob("*.py")) == []
    assert list((GHOSTSTREAM_ROOT / "discovery").rglob("*.py")) == []
    assert list((GHOSTSTREAM_ROOT / "jobs").rglob("*.py")) == []
    assert not (GHOSTSTREAM_ROOT / "security.py").exists()
    assert not (GHOSTSTREAM_ROOT / "tui.py").exists()
    assert not (GHOSTSTREAM_ROOT / "tui" / "engine.py").exists()
    assert not (GHOSTSTREAM_ROOT / "tui" / "models.py").exists()
    assert list((GHOSTSTREAM_ROOT / "transcoding").rglob("*.py")) == []
    assert not (GHOSTSTREAM_ROOT / "network" / "__init__.py").exists()
    assert not (GHOSTSTREAM_ROOT / "network" / "handlers.py").exists()
    assert not (GHOSTSTREAM_ROOT / "network" / "middleware.py").exists()
    assert not (GHOSTSTREAM_ROOT / "network" / "security.py").exists()
    assert not (GHOSTSTREAM_ROOT / "network" / "server.py").exists()
    assert not (GHOSTSTREAM_ROOT / "network" / "websocket.py").exists()


def test_runtime_singleton_accessors_do_not_return() -> None:
    forbidden_defs = {
        "def get_runtime(",
        "def set_runtime(",
        "def get_job_manager(",
        "def set_job_manager(",
        "def get_websocket_manager(",
        "def get_capability_service(",
        "def set_capability_service(",
        "def get_node_identity(",
    }

    offenders = []
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for marker in forbidden_defs:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{marker}")

    assert offenders == []


def test_known_dead_job_manager_helpers_do_not_return() -> None:
    forbidden_defs = {
        "def get_all_jobs(",
        "def is_stream_stale(",
    }

    offenders = []
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for marker in forbidden_defs:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{marker}")

    assert offenders == []


def test_known_dead_job_state_fields_do_not_return() -> None:
    assert "control_token" not in Job.__dataclass_fields__


def test_deleted_internal_imports_do_not_return() -> None:
    forbidden_imports = (
        "from ghoststream.runtime import",
        "import ghoststream.runtime",
        "from ghoststream.api import",
        "import ghoststream.api",
        "from ghoststream.discovery import",
        "import ghoststream.discovery",
        "from ghoststream.discovery.",
        "import ghoststream.discovery.",
        "from ghoststream.security import",
        "import ghoststream.security",
        "from ghoststream.jobs import",
        "import ghoststream.jobs",
        "from ghoststream.jobs.",
        "import ghoststream.jobs.",
        "from ghoststream.network import",
        "import ghoststream.network",
        "from ghoststream.network.",
        "import ghoststream.network.",
        "from ghoststream.transcoding import",
        "import ghoststream.transcoding",
        "from ghoststream.transcoding.",
        "import ghoststream.transcoding.",
        "from ghoststream.tui.engine import",
        "import ghoststream.tui.engine",
        "from ghoststream.tui.models import",
        "import ghoststream.tui.models",
    )

    offenders = []
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for marker in forbidden_imports:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{marker}")

    assert offenders == []


def test_wire_contract_models_stay_in_contract_modules() -> None:
    allowed = {
        Path("ghoststream/contracts/api.py"),
        Path("ghoststream/contracts/websocket.py"),
        Path("ghoststream/config.py"),
    }

    offenders = []
    base_model_pattern = re.compile(r"class\s+\w+\(BaseModel\)")
    import_pattern = re.compile(r"from\s+pydantic\s+import\s+.*\bBaseModel\b")

    for path in _iter_python_files():
        relative = path.relative_to(REPO_ROOT)
        if relative in allowed:
            continue

        text = path.read_text(encoding="utf-8")
        if base_model_pattern.search(text) or import_pattern.search(text):
            offenders.append(str(relative))

    assert offenders == []


def test_direct_flask_route_registration_stays_in_controller_glue() -> None:
    allowed_add_url_rule = {
        Path("ghoststream/specter/router.py"),
    }
    allowed_request_hooks = {
        Path("ghoststream/server/controllers/websocket.py"),
    }

    add_url_rule_offenders = []
    hook_offenders = []

    for path in _iter_python_files():
        relative = path.relative_to(REPO_ROOT)
        text = path.read_text(encoding="utf-8")

        if "add_url_rule(" in text and relative not in allowed_add_url_rule:
            add_url_rule_offenders.append(str(relative))

        if (
            ("before_request(" in text or "after_request(" in text)
            and relative not in allowed_request_hooks
            and relative != Path("ghoststream/server/controllers/middleware.py")
        ):
            hook_offenders.append(str(relative))

    assert add_url_rule_offenders == []
    assert hook_offenders == []


def test_registry_writes_stay_in_runtime_assembly() -> None:
    allowed = {
        Path("ghoststream/app/runtime.py"),
        Path("ghoststream/specter/core/manager.py"),
        Path("ghoststream/specter/core/registry.py"),
    }

    offenders = []
    for path in _iter_python_files():
        relative = path.relative_to(REPO_ROOT)
        if relative in allowed:
            continue

        text = path.read_text(encoding="utf-8")
        if "registry.provide(" in text:
            offenders.append(str(relative))

    assert offenders == []


def test_runtime_construction_stays_in_app_layer() -> None:
    allowed = {
        Path("ghoststream/app/factories.py"),
        Path("ghoststream/app/entrypoints.py"),
        Path("ghoststream/app/runtime.py"),
    }
    forbidden_patterns = (
        re.compile(r"(?<!class )\bNodeIdentityStore\("),
        re.compile(r"(?<!class )\bCapabilityService\("),
        re.compile(r"(?<!class )\bWebSocketManager\("),
        re.compile(r"(?<!class )\bJobManager\("),
        re.compile(r"(?<!class )\bTranscodeEngine\("),
        re.compile(r"(?<!class )\bGhostStreamDiscoveryService\("),
        re.compile(r"(?<!class )\bRegistrationAuthService\("),
        re.compile(r"(?<!class )\bGhostHubRegistrationService\("),
        re.compile(r"(?<!class )\bOperationsController\("),
        re.compile(r"(?<!class )\bAPIController\("),
        re.compile(r"(?<!class )\bStreamsController\("),
        re.compile(r"(?<!class )\bGhostStreamIngressController\("),
        re.compile(r"(?<!class )\bGhostStreamRuntime\("),
    )

    offenders = []
    for path in _iter_python_files():
        relative = path.relative_to(REPO_ROOT)
        if relative in allowed:
            continue

        text = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if pattern.search(text):
                offenders.append(f"{relative}:{pattern.pattern}")

    assert offenders == []


def test_http_ingress_uses_specter_controller_classes() -> None:
    controller_files = (
        Path("ghoststream/server/controllers/api.py"),
        Path("ghoststream/server/controllers/ops.py"),
        Path("ghoststream/server/controllers/streams.py"),
    )

    offenders = []
    pattern = re.compile(r"class\s+\w+Controller\(Controller\):")
    for relative in controller_files:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        if not pattern.search(text):
            offenders.append(str(relative))

    assert offenders == []


def test_internal_package_markers_stay_minimal() -> None:
    allowed_nonminimal = {
        Path("ghoststream/__init__.py"),
        Path("ghoststream/hardware/__init__.py"),
        Path("ghoststream/specter/__init__.py"),
        Path("ghoststream/specter/core/__init__.py"),
    }

    offenders = []
    for path in GHOSTSTREAM_ROOT.rglob("__init__.py"):
        relative = path.relative_to(REPO_ROOT)
        if relative in allowed_nonminimal:
            continue

        text = path.read_text(encoding="utf-8")
        if "import " in text or "__all__" in text:
            offenders.append(str(relative))

    assert offenders == []


def test_docs_and_examples_do_not_reference_removed_entrypoints_or_legacy_seams() -> None:
    forbidden_markers = (
        "python run.py",
        "ghoststream.transcoding",
        "ghoststream.runtime",
        "ghoststream.network",
        "ghoststream.jobs",
        "ghoststream.discovery",
        "ghoststream.security",
        "ghoststream.tui.engine",
        "ghoststream.tui.models",
    )

    offenders = []
    for path in _iter_release_surface_files():
        text = path.read_text(encoding="utf-8")
        for marker in forbidden_markers:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{marker}")

    assert offenders == []


def test_release_examples_do_not_strip_stream_token_urls() -> None:
    forbidden_markers = (
        "new URL(streamUrl).pathname",
        'f"http://{GHOSTSTREAM_SERVER}{parsed.path}"',
    )

    offenders = []
    for path in _iter_release_surface_files():
        text = path.read_text(encoding="utf-8")
        for marker in forbidden_markers:
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{marker}")

    assert offenders == []


def test_controllers_do_not_call_job_manager_private_cleanup_methods() -> None:
    controller_files = (
        Path("ghoststream/server/controllers/api.py"),
        Path("ghoststream/server/controllers/ops.py"),
        Path("ghoststream/server/controllers/streams.py"),
    )
    forbidden_markers = (
        "._cleanup_stale_jobs(",
        "._cleanup_orphaned_dirs(",
    )

    offenders = []
    for relative in controller_files:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for marker in forbidden_markers:
            if marker in text:
                offenders.append(f"{relative}:{marker}")

    assert offenders == []
