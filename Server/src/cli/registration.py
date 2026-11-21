"""Lightweight top-level command metadata for help and shell completion."""

from functools import cached_property
from importlib import import_module

import click
from click.shell_completion import CompletionItem


class LazyCommand(click.Command):
    """Advertise a command without importing its Click implementation."""

    def __init__(self, name: str, help: str, *, module: str | None = None) -> None:
        super().__init__(name, help=help)
        self.attribute = name.replace("-", "_")
        self.module = f"cli.commands.{module or self.attribute}"

    @cached_property
    def implementation(self) -> click.Command:
        command = getattr(import_module(self.module), self.attribute)
        if not isinstance(command, click.Command):
            raise TypeError(f"{self.module}.{self.attribute} is not a Click command")
        return command


class LazyCommandGroup(click.Group):
    """Resolve real commands for dispatch while listing their metadata."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = super().get_command(ctx, cmd_name)
        if not isinstance(command, LazyCommand):
            return command
        try:
            return command.implementation
        except Exception as error:
            if ctx.resilient_parsing:
                return None
            raise click.UsageError(f"Failed to load command '{cmd_name}'.", ctx=ctx) from error

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        # Let Click wrap the short help using the advertised commands, without resolving them.
        click.Group(commands=self.commands).format_commands(ctx, formatter)

    def shell_complete(self, ctx: click.Context, incomplete: str) -> list[CompletionItem]:
        completions = [
            CompletionItem(name, help=self.commands[name].get_short_help_str())
            for name in self.list_commands(ctx)
            if name.startswith(incomplete) and not self.commands[name].hidden
        ]
        # Keep Click's ordinary option completion as well as the advertised command names.
        return completions + click.Command.shell_complete(self, ctx, incomplete)


COMMANDS = (
    LazyCommand("animation", "Animation operations - control Animator, manage AnimationClips."),
    LazyCommand("asset", "Asset operations - search, import, create, delete assets."),
    LazyCommand(
        "asset-gen",
        "AI asset generation - generate 3D models, import marketplace models, generate images.",
    ),
    LazyCommand("audio", "AudioSource playback in Play mode and volume settings."),
    LazyCommand("batch", "Batch operations - execute multiple commands efficiently."),
    LazyCommand(
        "blender", "Blender Bridge - talk to a running Blender (BlenderMCP addon) from Unity."
    ),
    LazyCommand("build", "Build management - player builds, platforms, settings, batch."),
    LazyCommand("camera", "Camera operations - create, configure, and control cameras."),
    LazyCommand("code", "Code operations - read, search, and execute."),
    LazyCommand(
        "component", "Component operations - add, remove, modify components on GameObjects."
    ),
    LazyCommand("custom_tool", "Alias for tool management (custom tools).", module="tool"),
    LazyCommand("docs", "Fetch Unity API documentation."),
    LazyCommand(
        "doctor",
        "Verify this diagnostic SDK connection; does not attest another desktop client's session.",
    ),
    LazyCommand("editor", "Editor operations - play mode, console, tags, layers."),
    LazyCommand("gameobject", "GameObject operations - create, find, modify, delete GameObjects."),
    LazyCommand("graphics", "Manage rendering graphics: volumes, effects, and pipeline settings."),
    LazyCommand(
        "instance",
        "Unity instance management - list the connected instances and show the one in use.",
    ),
    LazyCommand("lighting", "Lighting operations - create, modify lights and lighting settings."),
    LazyCommand("material", "Material operations - create, modify, assign materials."),
    LazyCommand("packages", "Package management - install, remove, search, registries."),
    LazyCommand(
        "physics",
        "Manage 3D and 2D physics: settings, collision matrix, materials, joints, queries, validation.",
    ),
    LazyCommand(
        "play-scenario", "Save and run repeatable Play Mode scenarios; status never waits or polls."
    ),
    LazyCommand(
        "prefab", "Prefab operations - info, hierarchy, open, save, close, create prefabs."
    ),
    LazyCommand("probuilder", "ProBuilder operations - 3D modeling, mesh editing, UV management."),
    LazyCommand(
        "profiler",
        "Unity Profiler session control, counter reads, memory snapshots, and Frame Debugger.",
    ),
    LazyCommand(
        "project-auditor", "Run Project Auditor analysis with compatible installed APIs and rules."
    ),
    LazyCommand("receipt", "Inspect execution records or explicitly protect one native command."),
    LazyCommand("reflect", "Inspect Unity C# APIs via reflection."),
    LazyCommand(
        "scenario-recording",
        "Record Play Mode Button interactions and observed scene changes into a draft.",
    ),
    LazyCommand(
        "scene", "Scene operations - hierarchy, load, save, create, multi-scene, validation."
    ),
    LazyCommand(
        "scene-edit",
        "Preview and apply allowlisted scene edits with one named Undo group.",
        module="scene_edits",
    ),
    LazyCommand("script", "Script operations - create, read, edit C# scripts."),
    LazyCommand("shader", "Shader operations - create, read, update, delete shaders."),
    LazyCommand(
        "sprite", "Sprite sheet operations - slice sheets, build clips and Animator controllers."
    ),
    LazyCommand("texture", "Texture operations - create, modify, generate sprites."),
    LazyCommand("tool", "List project tools or inspect a named server-advertised tool."),
    LazyCommand("ui", "UI operations - create and modify UI elements."),
    LazyCommand(
        "verification",
        "Prepare source-linked checks and observe runs without automatically replaying work.",
    ),
    LazyCommand(
        "verification-candidate",
        "Review source evidence and manage local Player candidate registrations.",
    ),
    LazyCommand(
        "verification-evidence",
        "Compare retained results and their input identities without starting Unity.",
        module="verification_workflow_evidence",
    ),
    LazyCommand("vfx", "VFX operations - particle systems, line renderers, trails."),
)
