"""
Bridge: translate openclaw.json (HiClaw Worker config) into hermes-agent's
``~/.hermes/`` layout.

Hermes-agent reads its configuration from two main sources, in this order:

  1. ``${HERMES_HOME}/.env``        — secrets, MATRIX_*, OPENAI_*, …
  2. ``${HERMES_HOME}/config.yaml`` — model / terminal / platforms / memory

The bridge owns a small, well-defined slice of these files (everything that
flows from openclaw.json) and leaves all other keys untouched, so users may
still customise hermes through the standard ``hermes config`` workflow.

Bridge-owned env keys (always rewritten):
  ``MATRIX_*``, ``OPENAI_API_KEY``, ``OPENAI_BASE_URL``, ``HERMES_DEFAULT_MODEL``

Bridge-owned YAML blocks:
  ``model.{default,provider,base_url,context_length}``,
  ``auxiliary.vision.{provider,model,base_url,api_key}``,
  ``logging.level`` (when ``HICLAW_MATRIX_DEBUG=1``),
  ``platforms.matrix.enabled`` / ``platforms.matrix.reply_to_mode``,
  top-level ``matrix.{require_mention,free_response_rooms,auto_thread,…}``.

Anything else the user puts in ``config.yaml`` (terminal backend, memory
limits, mcp servers, skills paths) is preserved verbatim.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def _is_in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _port_remap(url: str, is_container: bool) -> str:
    """Remap container-internal :8080 to host-exposed gateway port when needed.

    The Manager publishes baseUrls like ``http://172.17.0.2:8080`` that are
    only routable from sibling containers. When the worker runs on the host
    (dev) we redirect to the published Higress port (default 18080).
    """
    if not is_container and url and ":8080" in url:
        gateway_port = os.environ.get("HICLAW_PORT_GATEWAY", "18080")
        return url.replace(":8080", f":{gateway_port}")
    return url


def _csv(values: Any) -> str:
    """Render a list as a comma-separated string suitable for .env."""
    if not values:
        return ""
    if isinstance(values, str):
        return values
    return ",".join(str(v) for v in values)


# ---------------------------------------------------------------------------
# openclaw.json field resolution
# ---------------------------------------------------------------------------

def _resolve_active_model(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the active model + parent provider from openclaw.json, or None.

    Selection order:
      1. ``agents.defaults.model.primary`` ("provider_id/model_id")
      2. First model of the first provider (deterministic via dict ordering)

    The result is the raw model dict augmented with two private keys:
      ``_provider``    — full provider config (for baseUrl / apiKey lookups)
      ``_provider_id`` — provider id from openclaw.json
    """
    providers_raw = cfg.get("models", {}).get("providers", {})
    if not providers_raw:
        return None

    primary = (
        cfg.get("agents", {})
        .get("defaults", {})
        .get("model", {})
        .get("primary", "")
    )
    if primary and "/" in primary:
        pid, mid = primary.split("/", 1)
        provider = providers_raw.get(pid, {})
        for m in provider.get("models", []):
            if m.get("id") == mid:
                return {**m, "_provider": provider, "_provider_id": pid}

    for pid, provider_cfg in providers_raw.items():
        models = provider_cfg.get("models", [])
        if models:
            return {**models[0], "_provider": provider_cfg, "_provider_id": pid}

    return None


def _resolve_context_window(cfg: Dict[str, Any]) -> Optional[int]:
    m = _resolve_active_model(cfg)
    if not m or "contextWindow" not in m:
        return None
    try:
        return int(m["contextWindow"])
    except (TypeError, ValueError):
        return None


def _resolve_vision_enabled(cfg: Dict[str, Any]) -> bool:
    """True if the active model declares ``image`` in its input modalities."""
    m = _resolve_active_model(cfg)
    if m is None:
        return False
    return "image" in (m.get("input") or [])


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------

# Keys the bridge OWNS — rewritten on every call to reflect openclaw.json.
# Any other env var (TAVILY_API_KEY, OPENROUTER_API_KEY user added by hand,
# tool-specific tokens, etc.) is preserved across re-bridging.
_BRIDGE_ENV_PREFIXES = ("MATRIX_",)
_BRIDGE_ENV_EXACT = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "HERMES_DEFAULT_MODEL",
}


def _is_bridge_owned(key: str) -> bool:
    if key in _BRIDGE_ENV_EXACT:
        return True
    return any(key.startswith(p) for p in _BRIDGE_ENV_PREFIXES)


def _quote_env_value(value: str) -> str:
    """Quote a value for inclusion in a python-dotenv compatible .env file."""
    if value is None:
        return '""'
    s = str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _read_env_file(path: Path) -> Dict[str, str]:
    """Parse a simple ``KEY="value"`` .env file into a dict.

    Tolerates blank lines, ``# comments``, unquoted values, and single- or
    double-quoted values. Lines that can't be parsed as ``KEY=VALUE`` are
    dropped silently.
    """
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
            val = val.replace('\\"', '"').replace("\\\\", "\\")
        out[key] = val
    return out


def _write_env_file(path: Path, env: Dict[str, str]) -> None:
    """Write env dict to a dotenv file with stable key ordering.

    Empty bridge-owned values are dropped entirely so that a missing setting
    on the Manager side does not silently shadow a still-valid value the
    Worker had received before. (The next bridge run will re-add it once the
    Manager publishes the value again.)
    """
    lines = [
        "# Generated by hermes-worker bridge.py — do not edit by hand.",
        "# Bridge-owned keys (MATRIX_*, OPENAI_*, HERMES_DEFAULT_MODEL) are",
        "# rewritten on every restart and Manager config push.",
        "",
    ]
    for key in sorted(env):
        val = env.get(key, "")
        if val == "":
            continue
        lines.append(f"{key}={_quote_env_value(val)}")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# .env value derivation
# ---------------------------------------------------------------------------

def _matrix_env(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Map ``openclaw.channels.matrix`` → MATRIX_* env vars.

    These are read by ``hermes_matrix.adapter`` (our overlay adapter) and
    by hermes-agent's gateway loader (``gateway/config.py:_apply_env_overrides``)
    which gates platform startup on ``MATRIX_ACCESS_TOKEN`` being present.
    """
    matrix_raw = cfg.get("channels", {}).get("matrix", {})
    if not matrix_raw:
        return {}
    in_container = _is_in_container()

    env: Dict[str, str] = {
        "MATRIX_HOMESERVER": _port_remap(
            matrix_raw.get("homeserver", ""), in_container
        ),
        "MATRIX_ACCESS_TOKEN": matrix_raw.get("accessToken", ""),
        "MATRIX_USER_ID": matrix_raw.get("userId", ""),
        "MATRIX_DEVICE_ID": matrix_raw.get("deviceId", ""),
        "MATRIX_ENCRYPTION": "true" if matrix_raw.get("encryption") else "false",
        "MATRIX_DM_POLICY": matrix_raw.get("dm", {}).get("policy", "allowlist"),
        "MATRIX_ALLOWED_USERS": _csv(matrix_raw.get("dm", {}).get("allowFrom")),
        "MATRIX_GROUP_POLICY": matrix_raw.get("groupPolicy", "allowlist"),
        "MATRIX_GROUP_ALLOW_FROM": _csv(matrix_raw.get("groupAllowFrom")),
        # Hermes's own bridge in gateway/config.py reads these; keep the names.
        "MATRIX_REQUIRE_MENTION": "true" if matrix_raw.get(
            "requireMention", True
        ) else "false",
        "MATRIX_FREE_RESPONSE_ROOMS": _csv(matrix_raw.get("freeResponseRooms")),
        "MATRIX_AUTO_THREAD": "true" if matrix_raw.get(
            "autoThread", True
        ) else "false",
        "MATRIX_DM_MENTION_THREADS": "true" if matrix_raw.get(
            "dmMentionThreads"
        ) else "false",
        "MATRIX_HOME_ROOM": matrix_raw.get("homeRoomId", ""),
        # Custom adapter knobs (not read by upstream hermes; consumed by
        # hermes_matrix.adapter directly).
        "MATRIX_VISION_ENABLED": "true" if _resolve_vision_enabled(cfg) else "false",
        "MATRIX_FILTER_TOOL_MESSAGES": "true",
        "MATRIX_FILTER_THINKING": "true",
    }

    history_limit = matrix_raw.get("historyLimit") or (
        cfg.get("messages", {}).get("groupChat", {}).get("historyLimit")
    )
    if history_limit is not None:
        env["MATRIX_HISTORY_LIMIT"] = str(history_limit)

    return env


def _model_env(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Map openclaw active provider → OPENAI_* env vars.

    Hermes uses OPENAI_BASE_URL + OPENAI_API_KEY for any OpenAI-compatible
    endpoint when ``model.provider`` is "custom". This is the standard path
    for Higress-fronted models in hiclaw.
    """
    m = _resolve_active_model(cfg)
    if not m:
        return {}
    in_container = _is_in_container()
    provider_cfg = m.get("_provider", {})
    return {
        "OPENAI_BASE_URL": _port_remap(provider_cfg.get("baseUrl", ""), in_container),
        "OPENAI_API_KEY": provider_cfg.get("apiKey", ""),
        # Read by hermes when ``model.default`` in YAML is empty.
        "HERMES_DEFAULT_MODEL": m.get("id", ""),
    }


def _runtime_env(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Pass-through string env vars from openclaw.env.* to the hermes process.

    Only string/number/bool values are propagated — nested dicts (like
    ``vars`` itself) are recursed into one level deep. This matches the
    semantics of the copaw bridge.
    """
    env_cfg = cfg.get("env") or {}
    out: Dict[str, str] = {}

    for k, v in (env_cfg.get("vars") or {}).items():
        if isinstance(v, (str, int, float, bool)) and str(v) != "":
            out[k] = str(v)

    for k, v in env_cfg.items():
        if k in ("vars", "shellEnv"):
            continue
        if not isinstance(v, (str, int, float, bool)):
            continue
        s = str(v).strip()
        if s and k not in out:
            out[k] = s

    return out


# ---------------------------------------------------------------------------
# config.yaml building
# ---------------------------------------------------------------------------

def _model_yaml_block(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build the ``model:`` block of hermes config.yaml from openclaw.json."""
    m = _resolve_active_model(cfg)
    if not m:
        return {}

    in_container = _is_in_container()
    provider_cfg = m.get("_provider", {})
    base_url = _port_remap(provider_cfg.get("baseUrl", ""), in_container)
    model_id = m.get("id", "")

    block: Dict[str, Any] = {
        "default": model_id,
        # ``custom`` instructs hermes to use OPENAI_BASE_URL + OPENAI_API_KEY
        # without provider-specific routing logic. Works with Higress and any
        # OpenAI-compatible endpoint.
        "provider": "custom",
    }
    if base_url:
        block["base_url"] = base_url
    ctx = _resolve_context_window(cfg)
    if ctx is not None:
        block["context_length"] = ctx
    return block


def _matrix_yaml_block(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build the top-level ``matrix:`` block (behavior knobs only)."""
    matrix_raw = cfg.get("channels", {}).get("matrix", {})
    if not matrix_raw:
        return {}
    block: Dict[str, Any] = {
        "require_mention": bool(matrix_raw.get("requireMention", True)),
        "auto_thread": bool(matrix_raw.get("autoThread", True)),
        "dm_mention_threads": bool(matrix_raw.get("dmMentionThreads", False)),
    }
    free_rooms = matrix_raw.get("freeResponseRooms") or []
    if free_rooms:
        block["free_response_rooms"] = list(free_rooms)
    return block


def _auxiliary_vision_yaml_block(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build ``auxiliary.vision`` to match the active model endpoint.

    Hermes routes image understanding through ``vision_analyze_tool`` which
    reads its own auxiliary vision config rather than inheriting the main
    model's runtime env. To keep worker-side image handling aligned with the
    active HiClaw model, explicitly point auxiliary vision at the same
    OpenAI-compatible endpoint and token.
    """
    if not _resolve_vision_enabled(cfg):
        return {}

    model = _resolve_active_model(cfg)
    if not model:
        return {}

    in_container = _is_in_container()
    provider_cfg = model.get("_provider", {})
    return {
        "provider": "custom",
        "model": model.get("id", ""),
        "base_url": _port_remap(provider_cfg.get("baseUrl", ""), in_container),
        "api_key": provider_cfg.get("apiKey", ""),
    }


def _logging_yaml_block() -> Dict[str, Any]:
    """Raise Hermes log verbosity when HiClaw Matrix debugging is enabled.

    Hermes' native Matrix plugin logs through the normal Python logging tree.
    Unlike OpenClaw there is no dedicated ``*_MATRIX_DEBUG`` env toggle, so we
    bridge HiClaw's runtime-wide ``HICLAW_MATRIX_DEBUG=1`` flag to
    ``config.yaml -> logging.level: DEBUG``.
    """
    if os.environ.get("HICLAW_MATRIX_DEBUG") != "1":
        return {}
    return {"level": "DEBUG"}


# ---------------------------------------------------------------------------
# mcporter config bridge
# ---------------------------------------------------------------------------

def _bridge_mcporter_config(hermes_home: Path) -> None:
    """Ensure mcporter can find MCP server config regardless of cwd.

    The controller writes ``mcporter-servers.json`` at workspace root, but
    mcporter looks for ``config/mcporter.json`` relative to ``$HOME`` (the
    workspace root).  Copy the file so the agent never wastes steps fixing
    mcporter configuration.
    """
    workspace_root = hermes_home.parent
    servers_json = workspace_root / "mcporter-servers.json"
    if not servers_json.exists():
        return

    config_dir = workspace_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / "mcporter.json"

    # Only write if source is newer (idempotent — avoids touching mtime
    # on every bridge run, which would trigger unnecessary MinIO syncs).
    if target.exists() and servers_json.stat().st_mtime <= target.stat().st_mtime:
        return

    target.write_text(servers_json.read_text())
    logger.info("bridge: mcporter config synced to %s", target)


# ---------------------------------------------------------------------------
# heartbeat → cron bridge
# ---------------------------------------------------------------------------

_HEARTBEAT_CRON_JOB_NAME = "hiclaw-heartbeat"


def _bridge_heartbeat_to_cron(
    openclaw_cfg: Dict[str, Any],
    hermes_home: Path,
    soul: Optional[str] = None,
) -> None:
    """Ensure a heartbeat cron job exists with prompt from Worker SOUL.md.

    The cron prompt = the Worker CRD ``spec.soul`` content — users edit
    the Worker YAML to customise agent behaviour, no image rebuild needed.

    Schedule detection (two strategies):

    1. openclaw.json ``agents.defaults.heartbeat`` → use that interval.
    2. Fallback: scan existing cron jobs for interval patterns.
    """
    hb = (
        openclaw_cfg.get("agents", {})
        .get("defaults", {})
        .get("heartbeat", {})
    )

    if hb and hb.get("enabled"):
        every = hb.get("every", "5m")
        schedule_str = f"every {every}"
        should_create = True
    else:
        # Fallback: no heartbeat in openclaw.json — look for existing
        # cron jobs with our schedule pattern and update their prompts.
        logger.info(
            "bridge: openclaw.json has no heartbeat — falling back to "
            "existing cron job scan"
        )
        schedule_str = None
        should_create = False

    try:
        from cron.jobs import load_jobs, save_jobs, create_job, update_job
    except ImportError:
        logger.warning(
            "bridge: cron module not available — skipping heartbeat cron job"
        )
        return

    # Cron prompt = Worker SOUL.md content (user-editable via CRD spec.soul).
    # An empty soul means no coordination logic to run — skip cron setup.
    prompt = (soul or "").strip()
    if not prompt:
        logger.info(
            "bridge: Worker has no SOUL.md — skipping heartbeat cron job"
        )
        return

    jobs = load_jobs()

    # ---- find existing heartbeat job ----
    existing = None
    for j in jobs:
        if j.get("name") == _HEARTBEAT_CRON_JOB_NAME:
            existing = j
            break
        # Backward compat: match old "Scan trade..." cron jobs
        if j.get("name", "").startswith("Scan trade"):
            existing = j
            break
        # Fallback: match any job with an interval schedule in the
        # 1m–10m range (typical heartbeat intervals).
        sch = j.get("schedule", {})
        if sch.get("kind") == "interval" and 1 <= sch.get("minutes", 0) <= 10:
            if existing is None:
                existing = j  # first candidate; prefer named match above

    if existing:
        current_prompt = existing.get("prompt", "")
        prompt_changed = current_prompt.strip() != prompt.strip()

        if schedule_str and existing.get("schedule", {}).get("display") != schedule_str:
            # Schedule also changed — full update
            update_job(existing["id"], {
                "prompt": prompt,
                "schedule": schedule_str,
                "name": _HEARTBEAT_CRON_JOB_NAME,
            })
            logger.info(
                "bridge: heartbeat cron job updated (id=%s schedule=%s)",
                existing["id"], schedule_str,
            )
        elif prompt_changed:
            # Prompt-only update (fallback path)
            update_job(existing["id"], {
                "prompt": prompt,
                "name": _HEARTBEAT_CRON_JOB_NAME,
            })
            logger.info(
                "bridge: heartbeat cron prompt updated (id=%s, schedule unchanged)",
                existing["id"],
            )
        else:
            logger.debug("bridge: heartbeat cron job already up-to-date (id=%s)", existing["id"])
    elif should_create and schedule_str:
        job = create_job(
            prompt=prompt,
            schedule=schedule_str,
            name=_HEARTBEAT_CRON_JOB_NAME,
            deliver="local",
        )
        logger.info(
            "bridge: heartbeat cron job created id=%s schedule=%s",
            job["id"], schedule_str,
        )
    else:
        logger.info(
            "bridge: no heartbeat in openclaw.json and no existing cron job "
            "found — skipping (controller rebuild needed for full automation)"
        )

def bridge_openclaw_to_hermes(
    openclaw_cfg: Dict[str, Any],
    hermes_home: Path,
    soul: Optional[str] = None,
    agents_md: Optional[str] = None,
) -> None:
    """Translate ``openclaw_cfg`` into hermes config under ``hermes_home``.

    Writes (idempotent across runs):

      ``<hermes_home>/.env``
          MATRIX_*, OPENAI_*, HERMES_DEFAULT_MODEL plus any pass-through
          vars from openclaw.env. User-managed keys are preserved.

      ``<hermes_home>/config.yaml``
          ``model:`` block, ``auxiliary.vision:`` bridge block, optional
          ``logging.level: DEBUG`` (when ``HICLAW_MATRIX_DEBUG=1``), top-level
          ``matrix:`` block, and
          ``platforms.matrix.enabled = true``. Other YAML keys (terminal,
          memory, mcp_servers, skills, …) are preserved.

      ``<hermes_home>/SOUL.md``     — when ``soul`` is provided
      ``<hermes_home>/AGENTS.md``   — when ``agents_md`` is provided

    Side effects:
      - Sets ``HERMES_HOME`` env var so subprocesses (mcporter, hermes-cli)
        pick up the same workspace.
    """
    hermes_home.mkdir(parents=True, exist_ok=True)

    # ── .env ────────────────────────────────────────────────────────────
    env_path = hermes_home / ".env"
    existing = _read_env_file(env_path)

    # Preserve user-managed env vars (anything the bridge does NOT own).
    merged: Dict[str, str] = {
        k: v for k, v in existing.items() if not _is_bridge_owned(k)
    }
    for source in (
        _matrix_env(openclaw_cfg),
        _model_env(openclaw_cfg),
        _runtime_env(openclaw_cfg),
    ):
        for k, v in source.items():
            merged[k] = v

    _write_env_file(env_path, merged)

    # ── config.yaml ─────────────────────────────────────────────────────
    config_path = hermes_home / "config.yaml"
    existing_yaml: Dict[str, Any] = {}
    if config_path.exists():
        try:
            existing_yaml = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError as exc:
            logger.warning(
                "Existing config.yaml is invalid (%s); starting fresh", exc
            )
            existing_yaml = {}

    # Replace owned blocks wholesale rather than field-merging — an openclaw
    # change to e.g. free_response_rooms must propagate as a list replacement,
    # not append.
    model_block = _model_yaml_block(openclaw_cfg)
    if model_block:
        # Preserve user-set fields like max_tokens or reasoning_effort.
        existing_yaml["model"] = {**existing_yaml.get("model", {}), **model_block}

    matrix_block = _matrix_yaml_block(openclaw_cfg)
    if matrix_block:
        existing_yaml["matrix"] = {**existing_yaml.get("matrix", {}), **matrix_block}

    auxiliary_vision_block = _auxiliary_vision_yaml_block(openclaw_cfg)
    if auxiliary_vision_block:
        auxiliary = existing_yaml.setdefault("auxiliary", {})
        if not isinstance(auxiliary, dict):
            auxiliary = {}
            existing_yaml["auxiliary"] = auxiliary
        auxiliary_vision = auxiliary.setdefault("vision", {})
        if not isinstance(auxiliary_vision, dict):
            auxiliary_vision = {}
        auxiliary["vision"] = {**auxiliary_vision, **auxiliary_vision_block}

    logging_block = _logging_yaml_block()
    if logging_block:
        logging_cfg = existing_yaml.setdefault("logging", {})
        if not isinstance(logging_cfg, dict):
            logging_cfg = {}
        existing_yaml["logging"] = {**logging_cfg, **logging_block}

    platforms = existing_yaml.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        platforms = {}
        existing_yaml["platforms"] = platforms
    platforms["matrix"] = {
        **platforms.get("matrix", {}),
        "enabled": True,
        "reply_to_mode": "first",
    }

    # Sensible Worker defaults — only set if the user did not configure them.
    existing_yaml.setdefault("memory", {}).setdefault("memory_enabled", True)
    existing_yaml.setdefault("group_sessions_per_user", True)

    # The hermes terminal sandbox defaults to ``local`` which is what we want
    # inside the worker container (no docker-in-docker).
    terminal = existing_yaml.setdefault("terminal", {})
    terminal.setdefault("backend", "local")
    terminal.setdefault("cwd", str(hermes_home))

    # ── providers block ─────────────────────────────────────────────────
    # Hermes resolve_runtime_provider() needs a `providers` section with
    # the API key. Without this, cron jobs get `no-key-required` (HTTP 401).
    _provider_cfg = (_resolve_active_model(openclaw_cfg) or {}).get("_provider", {})
    _api_key = _provider_cfg.get("apiKey", "")
    _base_url = _provider_cfg.get("baseUrl", "")
    if _api_key and _base_url:
        providers = existing_yaml.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            existing_yaml["providers"] = providers
        providers["custom"] = {
            "api_key": _api_key,
            "base_url": _base_url,
            "key_env": "OPENAI_API_KEY",
        }

    config_path.write_text(
        yaml.safe_dump(
            existing_yaml,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
    )

    # ── SOUL.md / AGENTS.md ─────────────────────────────────────────────
    # Hermes-agent has no fixed system-prompt filename, but the standard
    # convention (and what `hermes init` writes) is ``SOUL.md`` at the
    # root of HERMES_HOME. We mirror copaw's flow so manager-side workflows
    # are identical across worker types.
    if soul is not None:
        (hermes_home / "SOUL.md").write_text(soul)
    if agents_md is not None:
        (hermes_home / "AGENTS.md").write_text(agents_md)

    # ── mcporter config bridge ──────────────────────────────────────────
    # mcporter reads from $HOME/config/mcporter.json (cwd-relative).
    # The controller writes mcporter-servers.json at workspace root.
    # Bridge copies it to the path mcporter actually reads from so the
    # agent never wastes steps fixing config.
    _bridge_mcporter_config(hermes_home)

    # ── heartbeat → cron job bridge ─────────────────────────────────────
    # openclaw.json agents.defaults.heartbeat is designed for the openclaw
    # runtime. Hermes uses its own cron system. Bridge translates heartbeat
    # settings into a Hermes cron job so standalone workers get autonomous
    # wake-up without manual configuration.
    _bridge_heartbeat_to_cron(openclaw_cfg, hermes_home, soul)

    # Make HERMES_HOME visible to spawned tools (mcporter, hermes CLI helpers).
    os.environ["HERMES_HOME"] = str(hermes_home)

    logger.info(
        "bridge: wrote %d env keys, model=%s, matrix=%s",
        sum(1 for v in merged.values() if v),
        existing_yaml.get("model", {}).get("default", ""),
        bool(matrix_block),
    )
