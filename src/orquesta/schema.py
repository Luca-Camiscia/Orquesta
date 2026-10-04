"""Esquemas de orquesta.yaml y backends.yaml (pydantic) y validaciones cruzadas."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from orquesta import catalog

Mode = Literal["read-only", "write"]
Isolation = Literal["none", "worktree"]

_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MOOD_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class ConfigError(Exception):
    """Errores de configuración, ya formateados para mostrarle al usuario."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- orquesta.yaml ---------------------------------------------------------------------------

class Orchestrator(_Strict):
    enabled: bool = True
    default_mood: str
    show_mood_tag: bool = True
    moods: dict[str, str]

    @field_validator("moods")
    @classmethod
    def _moods_validos(cls, moods: dict[str, str]) -> dict[str, str]:
        if not moods:
            raise ValueError("tiene que haber al menos un mood")
        for name, desc in moods.items():
            if not _MOOD_RE.fullmatch(name):
                raise ValueError(f"nombre de mood {name!r} inválido (minúsculas, números, - o _)")
            if not str(desc).strip():
                raise ValueError(f"el mood {name!r} no tiene descripción")
        return moods

    @model_validator(mode="after")
    def _default_existe(self) -> "Orchestrator":
        if self.default_mood not in self.moods:
            raise ValueError(f"default_mood {self.default_mood!r} no existe en moods "
                             f"(disponibles: {', '.join(self.moods)})")
        return self


class Limits(_Strict):
    max_parallel: dict[str, int] = Field(default_factory=dict)

    @field_validator("max_parallel")
    @classmethod
    def _positivos(cls, limits: dict[str, int]) -> dict[str, int]:
        for backend, n in limits.items():
            if isinstance(n, bool) or not isinstance(n, int) or n < 1:
                raise ValueError(f"max_parallel.{backend} tiene que ser un entero >= 1")
        return limits


class Agent(_Strict):
    name: str
    display_name: str | None = None
    enabled: bool = True
    backend: str
    wrapper_model: str | None = None
    backend_model: str | None = None
    role: str
    route_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    mode: Mode = "read-only"
    isolation: Isolation | None = None
    prompt: str = ""
    protected: bool = False
    manual: bool = False
    extra_frontmatter: dict[str, Any] = Field(default_factory=dict)
    plugins: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _nombre_valido(cls, name: str) -> str:
        if not name or len(name) > 64 or not _NAME_RE.fullmatch(name):
            raise ValueError(f"name {name!r} inválido: solo minúsculas, números y guiones simples "
                             f"(sin guion al inicio ni al final), máx. 64 caracteres")
        return name

    @property
    def is_external(self) -> bool:
        return self.backend != "claude"

    @property
    def effective_isolation(self) -> str:
        if self.isolation:
            return self.isolation
        return "worktree" if self.mode == "write" else "none"

    @property
    def effective_wrapper_model(self) -> str:
        if self.wrapper_model:
            return self.wrapper_model
        return "haiku" if self.is_external else "inherit"

    @property
    def label(self) -> str:
        return self.display_name or self.name


class Registry(_Strict):
    orchestrator: Orchestrator
    limits: Limits = Field(default_factory=Limits)
    agents: list[Agent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _nombres_unicos(self) -> "Registry":
        seen: set[str] = set()
        dups = sorted({a.name for a in self.agents if a.name in seen or seen.add(a.name)})
        if dups:
            raise ValueError(f"nombres de agente duplicados: {', '.join(dups)}")
        return self

    def agent(self, name: str) -> Agent | None:
        return next((a for a in self.agents if a.name == name), None)

    def enabled_agents(self) -> list[Agent]:
        return [a for a in self.agents if a.enabled]


# --- backends.yaml ---------------------------------------------------------------------------

class PromptSpec(_Strict):
    via: Literal["stdin", "arg"]
    flag: list[str] = Field(default_factory=list)
    max_arg_chars: int | None = None


class OutputSpec(_Strict):
    format: Literal["text", "json"] = "text"
    text_key: str | None = None
    status_key: str | None = None
    ok_status: str | None = None


class ModeSpec(_Strict):
    flags: list[str] = Field(default_factory=list)
    os_sandbox: Literal["readonly", "workspace"] | None = None
    timeout_s: int = 540


class PingSpec(_Strict):
    prompt: str = "Reply PONG"
    expect: str = "PONG"
    timeout_s: int = 120
    model: str | None = None
    extra_flags: list[str] = Field(default_factory=list)


class AuthCheck(_Strict):
    cmd: list[str]
    ok_pattern: str


class LatestVersion(_Strict):
    """De dónde sacar la última versión publicada (doctor solo avisa, nunca instala)."""
    npm: str | None = None             # paquete npm: `npm view <pkg> version`
    cmd: list[str] | None = None       # comando que imprime la última versión...
    pattern: str | None = None         # ...y regex (grupo 1) para extraerla


class OsSandbox(_Strict):
    tool: Literal["bwrap"]
    writable: list[str] = Field(default_factory=list)


class Backend(_Strict):
    label: str
    native: bool = False
    command: list[str]
    common_flags: list[str] = Field(default_factory=list)
    cwd_flag: list[str] = Field(default_factory=list)
    model_flag: list[str] = Field(default_factory=list)
    prompt: PromptSpec
    output: OutputSpec = Field(default_factory=OutputSpec)
    modes: dict[Mode, ModeSpec] = Field(default_factory=dict)
    os_sandbox: dict[str, OsSandbox] = Field(default_factory=dict)  # clave: sys.platform
    ping: PingSpec = Field(default_factory=PingSpec)
    auth_check: AuthCheck | None = None
    version_cmd: list[str] = Field(default_factory=list)
    latest_version: LatestVersion | None = None
    rate_limit_patterns: list[str] = Field(default_factory=list)


class BackendsConfig(_Strict):
    version: int = 1
    backends: dict[str, Backend]


# --- parseo con errores legibles -------------------------------------------------------------

def _where(loc: tuple, data: Any) -> str:
    if len(loc) >= 2 and loc[0] == "agents" and isinstance(loc[1], int):
        agents = data.get("agents") if isinstance(data, dict) else None
        name = None
        if isinstance(agents, list) and loc[1] < len(agents) and isinstance(agents[loc[1]], dict):
            name = agents[loc[1]].get("name")
        who = f"agente {name!r}" if isinstance(name, str) and name else f"agente #{loc[1] + 1}"
        rest = ".".join(str(p) for p in loc[2:])
        return f"{who}: {rest}" if rest else who
    return ".".join(str(p) for p in loc) or "(raíz)"


def _format(exc: ValidationError, data: Any) -> list[str]:
    out = []
    for err in exc.errors():
        loc, kind, msg = tuple(err["loc"]), err["type"], err["msg"]
        if kind == "extra_forbidden":
            field = loc[-1]
            out.append(f"{_where(loc[:-1], data)}: campo desconocido {field!r}")
            continue
        if kind == "missing":
            out.append(f"{_where(loc, data)}: falta este campo (obligatorio)")
            continue
        msg = msg.removeprefix("Value error, ")
        out.append(f"{_where(loc, data)}: {msg}")
    return out


def parse_registry(data: Any) -> Registry:
    if not isinstance(data, dict):
        raise ConfigError(["orquesta.yaml tiene que ser un mapeo con orchestrator, limits y agents"])
    try:
        return Registry.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format(exc, data)) from None


def parse_backends(data: Any) -> BackendsConfig:
    if not isinstance(data, dict):
        raise ConfigError(["backends.yaml tiene que ser un mapeo con la clave backends"])
    try:
        return BackendsConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError([f"backends.yaml: {e}" for e in _format(exc, data)]) from None


def check_registry(reg: Registry, backends: BackendsConfig) -> list[str]:
    """Validaciones cruzadas entre el registro, backends.yaml y el catálogo de Claude Code."""
    errs: list[str] = []
    available = ", ".join(backends.backends)
    for a in reg.agents:
        who = f"agente {a.name!r}"
        backend = backends.backends.get(a.backend)
        if backend is None:
            errs.append(f"{who}: el backend {a.backend!r} no existe en backends.yaml "
                        f"(disponibles: {available})")
        elif not backend.native and a.mode not in backend.modes:
            errs.append(f"{who}: backends.yaml no define el modo {a.mode!r} para {a.backend}")
        if not a.role.strip():
            errs.append(f"{who}: role está vacío")
        if not a.tools:
            errs.append(f"{who}: tools está vacío; declaralas explícitamente (least privilege): "
                        f"sin tools, Claude Code le daría todas")
        for tool in a.tools:
            if not catalog.is_known_tool(tool):
                guess = catalog.suggest_tool(tool)
                hint = f" (¿quisiste decir {guess!r}?)" if guess else ""
                errs.append(f"{who}: tool desconocida {tool!r}{hint}; Claude Code "
                            f"{catalog.CLAUDE_CODE_VERSION} no la tiene")
        if a.mode == "read-only":
            writers = [t for t in a.tools if t in catalog.WRITE_TOOLS]
            if writers:
                errs.append(f"{who}: es read-only pero tools incluye {', '.join(writers)}")
        if a.isolation == "worktree" and a.mode != "write":
            errs.append(f"{who}: isolation worktree solo aplica a agentes con mode write")
        if not catalog.is_valid_model(a.effective_wrapper_model):
            errs.append(f"{who}: wrapper_model {a.effective_wrapper_model!r} inválido (usá haiku, "
                        f"sonnet, opus, fable, inherit o un ID claude-...)")
        for key, value in a.extra_frontmatter.items():
            errs.extend(catalog.check_frontmatter_field(who, key, value))
    for backend_name in reg.limits.max_parallel:
        if backend_name not in backends.backends:
            errs.append(f"limits.max_parallel: el backend {backend_name!r} no existe en backends.yaml")
    return errs
