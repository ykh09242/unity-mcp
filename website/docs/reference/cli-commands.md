---
id: cli-commands
title: CLI Commands
sidebar_label: CLI Commands
---

# CLI Commands

> **Auto-generated** from the actual Click tree by `tools/generate_docs_reference.py`.

See [CLI setup and operational guidance](./cli.md). Global options precede subcommands.

Examples are checked for Click syntax and inert scalar types only. File/Path values are lexical only; filesystem checks, callbacks, custom conversion and command behavior are not executed.

## `unity-mcp`

Unity MCP (ykh09242) Command Line Interface. Control Unity Editor directly from the command line using the Model Context Protocol. Examples: unity-mcp status unity-mcp gameobject find "Player" unity-mcp --format json scene hierarchy unity-mcp editor play Environment Variables: UNITY_MCP_HOST Server host (default: 127.0.0.1) UNITY_MCP_HTTP_PORT Server port (default: 8080) UNITY_MCP_TIMEOUT Timeout in seconds (default: 30) UNITY_MCP_FORMAT Output format (default: text) UNITY_MCP_INSTANCE Target Unity instance

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --version | boolean | optional | nargs=1, flag | — | — | Show the version and exit. |
| --host / -h | text | optional | nargs=1 | "127.0.0.1" | UNITY_MCP_HOST | MCP server host address. |
| --port / -p | integer range: [1..65535] | optional | nargs=1 | 8080 | UNITY_MCP_HTTP_PORT | MCP server port. |
| --timeout / -t | integer range: [1..None] | optional | nargs=1 | 30 | UNITY_MCP_TIMEOUT | Command timeout in seconds. |
| --format / -f | choice: text, json, table | optional | nargs=1 | "text" | UNITY_MCP_FORMAT | Output format. |
| --instance / -i | text | optional | nargs=1 | — | UNITY_MCP_INSTANCE | Target Unity instance (hash or Name@hash). |
| --verbose / -v | boolean | optional | nargs=1, flag | — | — | Print each command sent to Unity and its raw response to stderr. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation`

Animation operations - control Animator, manage AnimationClips.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation animator`

Animator component operations.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation animator crossfade`

Crossfade to an animation state. Examples: unity-mcp animation animator crossfade "Player" "Run" --duration 0.5

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| state_name | text | required | nargs=1 | — | — |  |
| --duration / -d | float | optional | nargs=1 | 0.25 | — | Crossfade duration in seconds. |
| --layer / -l | integer | optional | nargs=1 | -1 | — | Animator layer index (-1 for default). |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation animator get-parameter`

Get the current value of an Animator parameter. Examples: unity-mcp animation animator get-parameter "Player" "Speed"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| param_name | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation animator info`

Get Animator state, parameters, clips, and layers. Examples: unity-mcp animation animator info "Player" unity-mcp animation animator info --search-method by_id -- -12345

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation animator play`

Play an animation state on a target's Animator. Examples: unity-mcp animation animator play "Player" "Walk" unity-mcp animation animator play "Enemy" "Attack" --layer 1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| state_name | text | required | nargs=1 | — | — |  |
| --layer / -l | integer | optional | nargs=1 | -1 | — | Animator layer index (-1 for default). |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation animator set-enabled`

Enable or disable an Animator component. Examples: unity-mcp animation animator set-enabled "Player" true unity-mcp animation animator set-enabled "Player" false

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| enabled | boolean | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation animator set-parameter`

Set an Animator parameter. Examples: unity-mcp animation animator set-parameter "Player" "Speed" 5.0 unity-mcp animation animator set-parameter "Player" "IsRunning" true --type bool unity-mcp animation animator set-parameter "Player" "Jump" "" --type trigger

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| param_name | text | required | nargs=1 | — | — |  |
| value | text | required | nargs=1 | — | — |  |
| --type / -t | choice: float, int, bool, trigger | optional | nargs=1 | — | — | Parameter type (auto-detected if omitted). |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation animator set-speed`

Set Animator playback speed. Examples: unity-mcp animation animator set-speed "Player" 2.0 unity-mcp animation animator set-speed "Player" 0 # pause

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| speed | float | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip`

AnimationClip operations.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip add-curve`

Add a keyframe curve to an AnimationClip. Examples: unity-mcp animation clip add-curve "Assets/Anim/Bounce.anim" \ --property "localPosition.y" --type Transform \ --keys "[[0,0],[0.5,2],[1,0]]"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| clip_path | text | required | nargs=1 | — | — |  |
| --property / -p | text | required | nargs=1 | — | — | Property path (e.g. 'localPosition.x'). |
| --type / -t | text | optional | nargs=1 | "Transform" | — | Component type name. |
| --keys / -k | text | required | nargs=1 | — | — | Keyframes as JSON: [[0,0],[0.5,1],[1,0]] or [&#123;"time":0,"value":0&#125;,...] Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip add-event`

Add an animation event to a clip. Examples: unity-mcp animation clip add-event "Assets/Anim/Attack.anim" \ --function "OnAttackHit" --time 0.5 unity-mcp animation clip add-event "Assets/Anim/Footstep.anim" \ --function "PlaySound" --time 0.3 --string-param "footstep"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| clip_path | text | required | nargs=1 | — | — |  |
| --function | text | required | nargs=1 | — | — | Function name to call. |
| --time | float | required | nargs=1 | — | — | Time in seconds. |
| --string-param | text | optional | nargs=1 | "" | — | String parameter to pass. |
| --float-param | float | optional | nargs=1 | 0.0 | — | Float parameter to pass. |
| --int-param | integer | optional | nargs=1 | 0 | — | Int parameter to pass. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip assign`

Assign an AnimationClip to a GameObject. Adds an Animation component if the GameObject has no Animator or Animation. Examples: unity-mcp animation clip assign "Cube" "Assets/Animations/Bounce.anim"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| clip_path | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip create`

Create a new AnimationClip asset. Examples: unity-mcp animation clip create "Assets/Animations/Bounce.anim" --length 2.0 --loop unity-mcp animation clip create "Assets/Anim/Walk.anim" --frame-rate 30

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| clip_path | text | required | nargs=1 | — | — |  |
| --name | text | optional | nargs=1 | — | — | Clip name (defaults to filename). |
| --length / -l | float | optional | nargs=1 | 1.0 | — | Clip length in seconds. |
| --loop / --no-loop | boolean | optional | nargs=1, flag | false | — | Whether clip loops. |
| --frame-rate | float | optional | nargs=1 | 60.0 | — | Frame rate. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip create-preset`

Create an AnimationClip from a named preset. Presets: bounce, rotate, pulse, fade, shake, hover, spin, sway, bob, wiggle, blink, slide_in, elastic Examples: unity-mcp animation clip create-preset "Assets/Anim/Bounce.anim" bounce --duration 2.0 unity-mcp animation clip create-preset "Assets/Anim/Spin.anim" spin --amplitude 2 --no-loop

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| clip_path | text | required | nargs=1 | — | — |  |
| preset | choice: bounce, rotate, pulse, fade, shake, hover, spin, sway, bob, wiggle, blink, slide_in, elastic | required | nargs=1 | — | — |  |
| --duration / -d | float | optional | nargs=1 | 1.0 | — | Duration in seconds. |
| --amplitude / -a | float | optional | nargs=1 | 1.0 | — | Amplitude/intensity multiplier. |
| --loop / --no-loop | boolean | optional | nargs=1, flag | true | — | Whether clip loops. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip info`

Get AnimationClip info (curves, length, events). Examples: unity-mcp animation clip info "Assets/Animations/Walk.anim"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| clip_path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip remove-event`

Remove animation event(s) from a clip. Examples: unity-mcp animation clip remove-event "Assets/Anim/Attack.anim" --event-index 0 unity-mcp animation clip remove-event "Assets/Anim/Attack.anim" --function "OnAttackHit" unity-mcp animation clip remove-event "Assets/Anim/Attack.anim" --function "OnAttackHit" --time 0.5

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| clip_path | text | required | nargs=1 | — | — |  |
| --event-index | integer | optional | nargs=1 | — | — | Index of event to remove. |
| --function | text | optional | nargs=1 | — | — | Remove events by function name. |
| --time | float | optional | nargs=1 | — | — | Filter by time when removing by function name. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip set-curve`

Replace all keyframes on a curve in an AnimationClip. Examples: unity-mcp animation clip set-curve "Assets/Anim/Bounce.anim" \ --property "localPosition.y" --type Transform \ --keys "[[0,0],[1,3]]"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| clip_path | text | required | nargs=1 | — | — |  |
| --property / -p | text | required | nargs=1 | — | — | Property path (e.g. 'localPosition.x'). |
| --type / -t | text | optional | nargs=1 | "Transform" | — | Component type name. |
| --keys / -k | text | required | nargs=1 | — | — | Keyframes as JSON: [[0,0],[0.5,1],[1,0]] Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation clip set-vector-curve`

Set 3 curves (x/y/z) from Vector3 keyframes in one call. Examples: unity-mcp animation clip set-vector-curve "Assets/Anim/Move.anim" \ --property "localPosition" \ --keys '[&#123;"time":0,"value":[0,1,-10]&#125;,&#123;"time":1,"value":[2,1,-10]&#125;]'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| clip_path | text | required | nargs=1 | — | — |  |
| --property / -p | text | required | nargs=1 | — | — | Property group (e.g. 'localPosition', 'localEulerAngles', 'localScale'). |
| --type / -t | text | optional | nargs=1 | "Transform" | — | Component type name. |
| --keys / -k | text | required | nargs=1 | — | — | Vector3 keyframes as JSON: [&#123;"time":0,"value":[0,1,0]&#125;,...] Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller`

AnimatorController operations.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller add-blend-tree-child`

Add a child motion to a blend tree. Examples: unity-mcp animation controller add-blend-tree-child "Assets/Anim/Player.controller" "Locomotion" \ --clip-path "Assets/Anim/Walk.anim" --threshold 1.0 unity-mcp animation controller add-blend-tree-child "Assets/Anim/Player.controller" "Movement" \ --clip-path "Assets/Anim/WalkForward.anim" --position 0 1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| state_name | text | required | nargs=1 | — | — |  |
| --clip-path | text | required | nargs=1 | — | — | AnimationClip path. |
| --threshold | float | optional | nargs=1 | — | — | Threshold for 1D blend tree. |
| --position | tuple: float, float | optional | nargs=2 | — | — | Position (x, y) for 2D blend tree. |
| --layer-index | integer | optional | nargs=1 | 0 | — | Layer index. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller add-layer`

Add a layer to an AnimatorController. Examples: unity-mcp animation controller add-layer "Assets/Anim/Player.controller" "UpperBody" --weight 0.8 unity-mcp animation controller add-layer "Assets/Anim/Player.controller" "Effects" --blending-mode additive

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| layer_name | text | required | nargs=1 | — | — |  |
| --weight | float | optional | nargs=1 | 1.0 | — | Layer weight (default: 1.0). |
| --blending-mode | choice: override, additive | optional | nargs=1 | "override" | — | Blending mode. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller add-parameter`

Add a parameter to an AnimatorController. Examples: unity-mcp animation controller add-parameter "Assets/Anim/Player.controller" "Speed" --type float --default-value 0.0 unity-mcp animation controller add-parameter "Assets/Anim/Player.controller" "Jump" --type trigger

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| param_name | text | required | nargs=1 | — | — |  |
| --type / -t | choice: float, int, bool, trigger | optional | nargs=1 | "float" | — | Parameter type. |
| --default-value | text | optional | nargs=1 | — | — | Default value for the parameter. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller add-state`

Add a state to an AnimatorController. Examples: unity-mcp animation controller add-state "Assets/Anim/Player.controller" "Walk" \ --clip-path "Assets/Anim/Walk.anim" unity-mcp animation controller add-state "Assets/Anim/Player.controller" "Idle" --is-default

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| state_name | text | required | nargs=1 | — | — |  |
| --clip-path | text | optional | nargs=1 | — | — | AnimationClip to assign as motion. |
| --speed | float | optional | nargs=1 | 1.0 | — | State playback speed. |
| --is-default / --no-default | boolean | optional | nargs=1, flag | false | — | Set as default state. |
| --layer-index | integer | optional | nargs=1 | 0 | — | Layer index. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller add-transition`

Add a transition between states in an AnimatorController. Examples: unity-mcp animation controller add-transition "Assets/Anim/Player.controller" "Idle" "Walk" \ --no-exit-time --duration 0.25 \ --conditions '[&#123;"parameter":"Speed","mode":"greater","threshold":0.1&#125;]'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| from_state | text | required | nargs=1 | — | — |  |
| to_state | text | required | nargs=1 | — | — |  |
| --has-exit-time / --no-exit-time | boolean | optional | nargs=1, flag | true | — | Whether transition uses exit time. |
| --duration / -d | float | optional | nargs=1 | 0.25 | — | Transition duration. |
| --conditions / -c | text | optional | nargs=1 | — | — | Conditions as JSON: [&#123;"parameter":"Speed","mode":"greater","threshold":0.1&#125;] Accepts @file or -/@- stdin. |
| --layer-index | integer | optional | nargs=1 | 0 | — | Layer index. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller assign`

Assign an AnimatorController to a GameObject. Adds an Animator component if needed. Examples: unity-mcp animation controller assign "Assets/Animations/Player.controller" "Player"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller create`

Create a new AnimatorController asset. Examples: unity-mcp animation controller create "Assets/Animations/Player.controller"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller create-blend-tree-1d`

Create a 1D blend tree state in an AnimatorController. Examples: unity-mcp animation controller create-blend-tree-1d "Assets/Anim/Player.controller" "Locomotion" --blend-param "Speed"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| state_name | text | required | nargs=1 | — | — |  |
| --blend-param | text | required | nargs=1 | — | — | Blend parameter name. |
| --layer-index | integer | optional | nargs=1 | 0 | — | Layer index. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller create-blend-tree-2d`

Create a 2D blend tree state in an AnimatorController. Examples: unity-mcp animation controller create-blend-tree-2d "Assets/Anim/Player.controller" "Movement" \ --blend-param-x "VelocityX" --blend-param-y "VelocityZ"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| state_name | text | required | nargs=1 | — | — |  |
| --blend-param-x | text | required | nargs=1 | — | — | X-axis blend parameter name. |
| --blend-param-y | text | required | nargs=1 | — | — | Y-axis blend parameter name. |
| --blend-type | choice: simpledirectional2d, freeformdirectional2d, freeformcartesian2d | optional | nargs=1 | "simpledirectional2d" | — | Blend tree type. |
| --layer-index | integer | optional | nargs=1 | 0 | — | Layer index. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller info`

Get AnimatorController info (states, transitions, parameters). Examples: unity-mcp animation controller info "Assets/Animations/Player.controller"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller remove-layer`

Remove a layer from an AnimatorController. Examples: unity-mcp animation controller remove-layer "Assets/Anim/Player.controller" --layer-index 1 unity-mcp animation controller remove-layer "Assets/Anim/Player.controller" --layer-name "UpperBody"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| --layer-index | integer | optional | nargs=1 | — | — | Layer index to remove. |
| --layer-name | text | optional | nargs=1 | — | — | Layer name to remove. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation controller set-layer-weight`

Set the weight of a layer in an AnimatorController. Examples: unity-mcp animation controller set-layer-weight "Assets/Anim/Player.controller" 0.5 --layer-index 1 unity-mcp animation controller set-layer-weight "Assets/Anim/Player.controller" 0.8 --layer-name "UpperBody"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| weight | float | required | nargs=1 | — | — |  |
| --layer-index | integer | optional | nargs=1 | — | — | Layer index. |
| --layer-name | text | optional | nargs=1 | — | — | Layer name. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp animation raw`

Execute any animation action directly. Actions include: animator_*: animator_get_info, animator_play, animator_crossfade, ... controller_*: controller_create, controller_add_state, controller_add_transition, ... clip_*: clip_create, clip_get_info, clip_add_curve, clip_set_curve, clip_set_vector_curve, clip_create_preset, clip_assign Examples: unity-mcp animation raw animator_play "Player" --params '&#123;"stateName": "Walk"&#125;' unity-mcp animation raw clip_create --clip-path "Assets/Anim/Test.anim" --params '&#123;"length": 2.0, "loop": true&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| action | text | required | nargs=1 | — | — |  |
| target | text | optional | nargs=1 | — | — |  |
| --clip-path | text | optional | nargs=1 | — | — | AnimationClip asset path. |
| --params / -p | text | optional | nargs=1 | "&#123;&#125;" | — | Additional parameters as JSON. Accepts @file or -/@- stdin. |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset`

Asset operations - search, import, create, delete assets.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset create`

Create a new asset. Examples: unity-mcp asset create "Assets/Materials/Blue.mat" Material unity-mcp asset create "Assets/NewFolder" Folder unity-mcp asset create "Assets/Materials/Custom.mat" Material --properties '&#123;"color": [0,0,1,1]&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| asset_type | text | required | nargs=1 | — | — |  |
| --properties / -p | text | optional | nargs=1 | — | — | Initial properties as JSON. Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset delete`

Delete an asset. Examples: unity-mcp asset delete "Assets/OldMaterial.mat" unity-mcp asset delete "Assets/Unused" --force

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --force / -f | boolean | optional | nargs=1, flag | — | — | Skip confirmation prompt. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset duplicate`

Duplicate an asset. Examples: unity-mcp asset duplicate "Assets/Materials/Red.mat" "Assets/Materials/RedCopy.mat"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| source | text | required | nargs=1 | — | — |  |
| destination | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset import`

Import/reimport an asset. Examples: unity-mcp asset import "Assets/Textures/NewTexture.png"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset info`

Get detailed information about an asset. Examples: unity-mcp asset info "Assets/Materials/Red.mat" unity-mcp asset info "Assets/Prefabs/Player.prefab" --preview

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --preview | boolean | optional | nargs=1, flag | — | — | Generate preview thumbnail (may be large). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset mkdir`

Create a folder. Examples: unity-mcp asset mkdir "Assets/NewFolder" unity-mcp asset mkdir "Assets/Levels/Chapter1"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset move`

Move an asset to a new location. Examples: unity-mcp asset move "Assets/Old/Material.mat" "Assets/New/Material.mat"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| source | text | required | nargs=1 | — | — |  |
| destination | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset rename`

Rename an asset. Examples: unity-mcp asset rename "Assets/Materials/Old.mat" "New.mat"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| new_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset search`

Search for assets. Examples: unity-mcp asset search "*.prefab" unity-mcp asset search "Player*" --path "Assets/Characters" unity-mcp asset search "*" --type Material unity-mcp asset search "t:MonoScript" --path "Assets/Scripts"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| pattern | text | optional | nargs=1 | "*" | — |  |
| --path / -p | text | optional | nargs=1 | "Assets" | — | Folder path to search in. |
| --type / -t | text | optional | nargs=1 | — | — | Filter by asset type (e.g., Material, Prefab, MonoScript). |
| --limit / -l | integer | optional | nargs=1 | 25 | — | Maximum results per page. |
| --page | integer | optional | nargs=1 | 1 | — | Page number (1-based). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset-gen`

AI asset generation - generate 3D models, import marketplace models, generate images.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset-gen generate-audio`

Generate audio (SFX / music) with a fal.ai model. Examples: unity-mcp asset-gen generate-audio --provider fal --prompt "8-bit coin pickup" unity-mcp asset-gen generate-audio --prompt "calm ambient loop" --duration 30

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --provider | text | optional | nargs=1 | — | — | Provider id (fal). |
| --prompt | text | optional | nargs=1 | — | — | Text prompt describing the sound or music. |
| --model | text | optional | nargs=1 | — | — | fal model id (omit for the GUI-selected default). |
| --duration | float | optional | nargs=1 | — | — | Requested length in seconds (soft-clamped per model). |
| --name | text | optional | nargs=1 | — | — | Base name for the imported asset. |
| --output-folder | text | optional | nargs=1 | — | — | Destination folder under Assets/. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset-gen generate-image`

Generate a 2D image with an AI provider. Examples: unity-mcp asset-gen generate-image --provider fal --prompt "a stone texture" unity-mcp asset-gen generate-image --provider openrouter --prompt "logo" --transparent

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --provider | text | optional | nargs=1 | — | — | Provider id (fal, openrouter). |
| --mode | text | optional | nargs=1 | — | — | Generation mode: text or image. |
| --prompt | text | optional | nargs=1 | — | — | Text prompt for text-&gt;image. |
| --image-path | text | optional | nargs=1 | — | — | Source image path for image-&gt;image. |
| --image-url | text | optional | nargs=1 | — | — | Source image URL for image-&gt;image. |
| --model | text | optional | nargs=1 | — | — | Provider model id/slug. |
| --transparent / --no-transparent | boolean | optional | nargs=1, flag | — | — | Request transparency. |
| --width | integer | optional | nargs=1 | — | — | Output width in pixels. |
| --height | integer | optional | nargs=1 | — | — | Output height in pixels. |
| --name | text | optional | nargs=1 | — | — | Base name for the imported asset. |
| --output-folder | text | optional | nargs=1 | — | — | Destination folder under Assets/. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset-gen generate-model`

Generate a 3D model with an AI provider. Examples: unity-mcp asset-gen generate-model --provider tripo --mode text --prompt "a red chair" unity-mcp asset-gen generate-model --provider meshy --mode image --image-path Assets/ref.png

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --provider | text | optional | nargs=1 | — | — | Provider id (tripo, meshy, fal). |
| --mode | text | optional | nargs=1 | — | — | Generation mode: text or image. |
| --prompt | text | optional | nargs=1 | — | — | Text prompt for text-&gt;3D. |
| --image-path | text | optional | nargs=1 | — | — | Source image path for image-&gt;3D. |
| --image-url | text | optional | nargs=1 | — | — | Source image URL for image-&gt;3D. |
| --format | text | optional | nargs=1 | — | — | Output format: glb, fbx, obj, usdz. |
| --target-size | float | optional | nargs=1 | — | — | Normalize largest dimension (meters). |
| --texture / --no-texture | boolean | optional | nargs=1, flag | — | — | Generate textures. |
| --tier | text | optional | nargs=1 | — | — | Provider quality/cost tier. |
| --model | text | optional | nargs=1 | — | — | Provider model id/version (omit for the GUI-selected default). |
| --name | text | optional | nargs=1 | — | — | Base name for the imported asset. |
| --output-folder | text | optional | nargs=1 | — | — | Destination folder under Assets/. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset-gen import-model`

Import a 3D model from the Sketchfab marketplace by uid. Examples: unity-mcp asset-gen import-model --uid abc123 unity-mcp asset-gen import-model --uid abc123 --name MyProp --output-folder Assets/Props

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --uid | text | required | nargs=1 | — | — | Sketchfab model uid to import. |
| --target-size | float | optional | nargs=1 | — | — | Normalize largest dimension (meters). |
| --name | text | optional | nargs=1 | — | — | Base name for the imported asset. |
| --output-folder | text | optional | nargs=1 | — | — | Destination folder under Assets/. |
| --archive-model-path | text | optional | nargs=1 | — | — | Exact case-sensitive ZIP model entry; omit for deterministic FBX/OBJ-first selection. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset-gen import-model-file`

Import a 3D model file already within this Unity project's Assets folder.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --source-path | text | required | nargs=1 | — | — | Assets-relative or absolute-within-Assets model file path (.fbx/.obj/.glb/.gltf/.zip); traversal and links are rejected. |
| --name | text | optional | nargs=1 | — | — | Base name for the imported asset. |
| --output-folder | text | optional | nargs=1 | — | — | Destination folder under Assets/. |
| --target-size | float | optional | nargs=1 | — | — | Normalize largest dimension (meters). |
| --animation-type | choice: none, generic, humanoid, legacy | optional | nargs=1 | — | — | FBX/OBJ rig mode: generic/humanoid surface animation clips; legacy selects Unity's legacy Animation system (glTF ignores this). |
| --archive-model-path | text | optional | nargs=1 | — | — | Exact case-sensitive ZIP model entry; omit for deterministic FBX/OBJ-first selection. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset-gen list-models`

List compatible models, capabilities, and catalog freshness. If catalogs[].refreshing is true, repeat this command without --refresh later. Discovered entries are checked for compatibility before generation.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --kind | choice: audio, image, model | required | nargs=1 | — | — | Asset kind to discover. |
| --provider | text | optional | nargs=1 | — | — | Restrict results to one provider. |
| --refresh | boolean | optional | nargs=1, flag | — | — | Force live catalog discovery. |
| --search | text | optional | nargs=1 | — | — | Filter models by name, id or use case. |
| --mode | choice: text, image | optional | nargs=1 | — | — | Filter models by input mode. |
| --limit | integer range: [1..200] | optional | nargs=1 | — | — | Page size (default 50). |
| --offset | integer range: [0..None] | optional | nargs=1 | — | — | Page offset (default 0). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp asset-gen status`

Check the status of an asset generation/import job. Examples: unity-mcp asset-gen status --job-id abc123

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --job-id | text | required | nargs=1 | — | — | Job id returned by a generate/import command. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp audio`

AudioSource playback in Play mode and volume settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp audio play`

Request playback on an existing AudioSource in Play mode. Examples: unity-mcp audio play "MusicPlayer" unity-mcp audio play "SFXSource" --clip "Assets/Audio/explosion.wav"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --clip / -c | text | optional | nargs=1 | — | — | Audio clip path to play. |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — | How to find the target. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp audio stop`

Request stopping an existing AudioSource in Play mode. Examples: unity-mcp audio stop "MusicPlayer"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — | How to find the target. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp audio volume`

Set audio volume on a target's AudioSource. Examples: unity-mcp audio volume "MusicPlayer" 0.5

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| level | float | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — | How to find the target. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp batch`

Batch operations - execute multiple commands efficiently.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp batch inline`

Execute commands from inline JSON. Examples: unity-mcp batch inline '[&#123;"tool": "manage_scene", "params": &#123;"action": "get_active"&#125;&#125;]' unity-mcp batch inline '[ &#123;"tool": "manage_gameobject", "params": &#123;"action": "create", "name": "A", "primitiveType": "Cube"&#125;&#125;, &#123;"tool": "manage_gameobject", "params": &#123;"action": "create", "name": "B", "primitiveType": "Sphere"&#125;&#125; ]' JSON input also accepts @file or -/@- stdin.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| commands_json | text | required | nargs=1 | — | — |  |
| --parallel | boolean | optional | nargs=1, flag | — | — | Execute read-only commands in parallel. |
| --fail-fast | boolean | optional | nargs=1, flag | — | — | Stop on first failure. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp batch run`

Execute commands from a JSON file. The JSON file should contain an array of command objects with 'tool' and 'params' keys. File format: [ &#123;"tool": "manage_gameobject", "params": &#123;"action": "create", "name": "Cube1"&#125;&#125;, &#123;"tool": "manage_gameobject", "params": &#123;"action": "create", "name": "Cube2"&#125;&#125;, &#123;"tool": "manage_components", "params": &#123;"action": "add", "target": "Cube1", "componentType": "Rigidbody"&#125;&#125; ] Examples: unity-mcp batch run commands.json unity-mcp batch run setup.json --parallel unity-mcp batch run critical.json --fail-fast

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| file | file/path (lexical only) | required | nargs=1 | — | — |  |
| --parallel | boolean | optional | nargs=1, flag | — | — | Execute read-only commands in parallel. |
| --fail-fast | boolean | optional | nargs=1, flag | — | — | Stop on first failure. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp batch template`

Generate a sample batch commands file. Examples: unity-mcp batch template &gt; commands.json unity-mcp batch template -o my_batch.json

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --output / -o | file/path (lexical only) | optional | nargs=1 | — | — | Output file (default: stdout) |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender`

Blender Bridge - talk to a running Blender (BlenderMCP addon) from Unity.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender check-updates`

git fetch the blender-mcp checkout and report how far behind its remotes it is.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender compare-screenshot`

Composite Blender's viewport and a Unity capture of the placed object side by side.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --game-object | text | required | nargs=1 | — | — | Placed GameObject to frame in Unity. |
| --max-size | integer | optional | nargs=1 | — | — | Max pixels on the longest side of each half. |
| --output-folder | text | optional | nargs=1 | — | — | Copy the PNG under this Assets/ folder. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender import-model`

Export from Blender, import into the project, and place the model in the open scene. Examples: unity-mcp blender import-model --selection-only --target-size 2 unity-mcp blender import-model --object House --format glb --position 0 0 0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --object | text | optional | nargs=1, repeatable | — | — | Blender object to export (repeatable). Children included. |
| --selection-only | boolean | optional | nargs=1, flag | false | — | Export only what is selected in Blender. |
| --format | choice: glb, fbx | optional | nargs=1 | — | — | Export format (default glb). |
| --name | text | optional | nargs=1 | — | — | Asset and GameObject name. |
| --target-size | float | optional | nargs=1 | — | — | Final size in meters of the largest dimension (0 keeps the imported scale). |
| --position | float | optional | nargs=3 | — | — | World position of the placed instance. |
| --no-place | boolean | optional | nargs=1, flag | false | — | Import only; do not place in the scene. |
| --keep-modifiers | boolean | optional | nargs=1, flag | false | — | Do not bake modifiers (use for rigs / shape keys). |
| --output-folder | text | optional | nargs=1 | — | — | Destination folder under Assets/. |
| --animation-type | choice: none, generic, humanoid, legacy | optional | nargs=1 | — | — | FBX only: rig/animation import mode. |
| --no-animate | boolean | optional | nargs=1, flag | false | — | Do not create a looping AnimatorController for imported clips. |
| --save-prefab | boolean | optional | nargs=1, flag | false | — | Save the placed instance as a prefab. |
| --ensure-bloom | boolean | optional | nargs=1, flag | false | — | Add a Bloom volume when the model has emissive materials. |
| --timeout | integer | optional | nargs=1 | — | — | Seconds to wait for Blender. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender object-info`

Print details about one Blender object.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --object-name | text | required | nargs=1 | — | — | Blender object name. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender run-python`

Execute Python inside Blender and print its stdout.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --code | text | optional | nargs=1 | — | — | Python source to run inside Blender. |
| --file | file/path (lexical only) | optional | nargs=1 | — | — | Read the Python source from this file instead of --code. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender scene-info`

Print Blender's current scene summary.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender screenshot`

Capture Blender's viewport to a PNG.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --max-size | integer | optional | nargs=1 | — | — | Max pixels on the longest side. |
| --output-folder | text | optional | nargs=1 | — | — | Copy the PNG under this Assets/ folder. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender setup-bloom`

Enable camera post-processing and add a Bloom override to the global volume.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender status`

Report whether Blender is reachable and whether the installed addon matches the checkout.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp blender sync-addon`

Copy the checkout's addon.py into Blender's addons folder.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --force | boolean | optional | nargs=1, flag | false | — | Overwrite even when the installed addon already matches. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build`

Build management - player builds, platforms, settings, batch.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build batch`

Run batch builds across multiple platforms or profiles. Examples: unity-mcp build batch --targets windows64,linux64,webgl unity-mcp build batch --profiles "Assets/Profiles/A.asset,Assets/Profiles/B.asset" unity-mcp build batch --targets windows64,android --development

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --targets / -t | text | optional | nargs=1 | — | — | Comma-separated targets: windows64,linux64,webgl |
| --profiles / -p | text | optional | nargs=1 | — | — | Comma-separated profile paths (Unity 6+) |
| --output-dir / -o | text | optional | nargs=1 | — | — | Base output directory |
| --development / -d | boolean | optional | nargs=1, flag | — | — | Development build for all |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build cancel`

Cancel a build or batch job (best-effort). Examples: unity-mcp build cancel batch-xyz789

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build clean-output`

Preview or delete an explicit build output under Builds/.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| output_path | text | required | nargs=1 | — | — |  |
| --delete | boolean | optional | nargs=1, flag | — | — | Delete the selected Builds/ descendant; omission previews only. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build platform`

Read or switch the active build platform. Examples: unity-mcp build platform unity-mcp build platform android unity-mcp build platform windows64 --subtarget server

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | optional | nargs=1 | — | — |  |
| --subtarget | choice: player, server | optional | nargs=1 | — | — | Build subtarget |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build profiles`

List, inspect, or activate build profiles (Unity 6+). Examples: unity-mcp build profiles unity-mcp build profiles "Assets/Settings/Build Profiles/iOS.asset" unity-mcp build profiles "Assets/Settings/Build Profiles/iOS.asset" --activate

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| profile | text | optional | nargs=1 | — | — |  |
| --activate | boolean | optional | nargs=1, flag | — | — | Activate the specified profile |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build run`

Trigger a player build. Examples: unity-mcp build run --target windows64 --development unity-mcp build run --target android --backend il2cpp unity-mcp build run --profile "Assets/Settings/Build Profiles/iOS.asset"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | optional | nargs=1 | — | — | Build target: windows64, osx, linux64, android, ios, webgl |
| --output / -o | text | optional | nargs=1 | — | — | Output path |
| --development / -d | boolean | optional | nargs=1, flag | — | — | Development build |
| --backend | choice: mono, il2cpp | optional | nargs=1 | — | — | Scripting backend |
| --subtarget | choice: player, server | optional | nargs=1 | — | — | Build subtarget |
| --profile | text | optional | nargs=1 | — | — | Build Profile asset path (Unity 6+) |
| --clean | boolean | optional | nargs=1, flag | — | — | Clean build cache |
| --auto-run | boolean | optional | nargs=1, flag | — | — | Auto-run after build |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build scenes`

Read or update the build scene list. Examples: unity-mcp build scenes unity-mcp build scenes --set "Assets/Scenes/Main.unity,Assets/Scenes/Level1.unity"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --set | text | optional | nargs=1 | — | — | Comma-separated scene paths to set |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build settings`

Read or write player settings. Properties: product_name, company_name, version, bundle_id, scripting_backend, defines, architecture Examples: unity-mcp build settings product_name unity-mcp build settings product_name --value "My Game" unity-mcp build settings scripting_backend --value il2cpp --target android

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| property_name | text | required | nargs=1 | — | — |  |
| --value / -v | text | optional | nargs=1 | — | — | Value to set. Omit to read. |
| --target / -t | text | optional | nargs=1 | — | — | Build target for platform-specific settings |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp build status`

Check build status or get last build report. Examples: unity-mcp build status unity-mcp build status build-abc123

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera`

Camera operations - create, configure, and control cameras.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera add-extension`

Add extension to CinemachineCamera. Examples: unity-mcp camera add-extension "CM Camera" CinemachineDeoccluder unity-mcp camera add-extension "CM Camera" CinemachineImpulseListener

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| extension_type | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --props | text | optional | nargs=1 | — | — | Extension properties as JSON. Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera brain-status`

Get CinemachineBrain status. Examples: unity-mcp camera brain-status

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera create`

Create a new camera. Examples: unity-mcp camera create --name "FollowCam" --preset third_person --follow Player unity-mcp camera create --name "MainCam" --fov 50

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --name / -n | text | optional | nargs=1 | — | — | Name for the camera GameObject. |
| --preset / -p | choice: follow, third_person, freelook, dolly, static, top_down, side_scroller | optional | nargs=1 | — | — | Camera preset (Cinemachine only). |
| --follow | text | optional | nargs=1 | — | — | Follow target (name/path/ID). |
| --look-at | text | optional | nargs=1 | — | — | LookAt target (name/path/ID). |
| --priority | integer | optional | nargs=1 | — | — | Camera priority. |
| --fov | float | optional | nargs=1 | — | — | Field of view. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera ensure-brain`

Ensure CinemachineBrain exists on main camera. Examples: unity-mcp camera ensure-brain unity-mcp camera ensure-brain --blend-style EaseInOut --blend-duration 2.0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --camera-ref | text | optional | nargs=1 | — | — | Camera to add Brain to (name/path/ID). |
| --blend-style | text | optional | nargs=1 | — | — | Default blend style. |
| --blend-duration | float | optional | nargs=1 | — | — | Default blend duration. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera force`

Force Brain to use a specific camera. Examples: unity-mcp camera force "CM Cinematic"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera list`

List all cameras in the scene. Examples: unity-mcp camera list

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera ping`

Check if Cinemachine is available. Examples: unity-mcp camera ping

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera release`

Release camera override. Examples: unity-mcp camera release

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera remove-extension`

Remove extension from CinemachineCamera. Examples: unity-mcp camera remove-extension "CM Camera" CinemachineDeoccluder

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| extension_type | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera screenshot`

Capture a screenshot from a camera. Examples: unity-mcp camera screenshot unity-mcp camera screenshot --camera-ref "CM FollowCam" --include-image --max-resolution 512 unity-mcp camera screenshot --capture-source scene_view --view-target Canvas --include-image unity-mcp camera screenshot --batch surround --view-target Player unity-mcp camera screenshot --output-folder Captures

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --camera-ref | text | optional | nargs=1 | — | — | Camera to capture from (name/path/ID). |
| --file-name | text | optional | nargs=1 | — | — | Output file name. |
| --super-size | integer | optional | nargs=1 | — | — | Supersize multiplier. |
| --include-image / --no-include-image | boolean | optional | nargs=1, flag | — | — | Return inline base64 PNG. |
| --max-resolution | integer | optional | nargs=1 | — | — | Max resolution for inline image. |
| --capture-source | choice: game_view, scene_view | optional | nargs=1 | — | — | Capture source: game_view (default) or scene_view. |
| --batch | choice: surround, orbit | optional | nargs=1 | — | — | Batch capture mode. |
| --view-target | text | optional | nargs=1 | — | — | Target to focus on (name/path/ID or [x,y,z]). Aims camera (game_view) or frames Scene View (scene_view). |
| --output-folder | text | optional | nargs=1 | — | — | Output folder, project-relative (e.g. 'Assets/Screenshots' or 'Captures') or absolute inside the project. Overrides Editor preference; falls back to Assets/Screenshots when unset. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera screenshot-multiview`

Capture a 6-angle contact sheet around the scene. Examples: unity-mcp camera screenshot-multiview unity-mcp camera screenshot-multiview --view-target Player --max-resolution 480

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --max-resolution | integer | optional | nargs=1 | — | — | Max resolution per tile. |
| --view-target | text | optional | nargs=1 | — | — | Center target: GameObject name/path/ID or [x,y,z]. |
| --output-folder | text | optional | nargs=1 | — | — | Output folder, project-relative or absolute inside the project. Overrides Editor preference; falls back to Assets/Screenshots when unset. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera set-aim`

Configure Aim component on CinemachineCamera. Examples: unity-mcp camera set-aim "CM Camera" --aim-type CinemachineHardLookAt

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --aim-type | text | optional | nargs=1 | — | — | Aim component type to swap to. |
| --props | text | optional | nargs=1 | — | — | Aim properties as JSON. Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera set-blend`

Configure default blend on CinemachineBrain. Examples: unity-mcp camera set-blend --style EaseInOut --duration 2.0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --style | text | optional | nargs=1 | — | — | Blend style (Cut, EaseInOut, Linear, etc.). |
| --duration | float | optional | nargs=1 | — | — | Blend duration in seconds. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera set-body`

Configure Body component on CinemachineCamera. Examples: unity-mcp camera set-body "CM Camera" --body-type CinemachineFollow unity-mcp camera set-body "CM Camera" --props '&#123;"cameraDistance": 5.0&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --body-type | text | optional | nargs=1 | — | — | Body component type to swap to. |
| --props | text | optional | nargs=1 | — | — | Body properties as JSON. Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera set-lens`

Set camera lens properties. Examples: unity-mcp camera set-lens "CM Camera" --fov 40 --near 0.1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --fov | float | optional | nargs=1 | — | — | Field of view. |
| --near | float | optional | nargs=1 | — | — | Near clip plane. |
| --far | float | optional | nargs=1 | — | — | Far clip plane. |
| --ortho-size | float | optional | nargs=1 | — | — | Orthographic size. |
| --dutch | float | optional | nargs=1 | — | — | Dutch angle (Cinemachine). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera set-noise`

Configure Noise on CinemachineCamera. Examples: unity-mcp camera set-noise "CM Camera" --amplitude 0.5 --frequency 1.0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --amplitude | float | optional | nargs=1 | — | — | Amplitude gain. |
| --frequency | float | optional | nargs=1 | — | — | Frequency gain. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera set-priority`

Set camera priority. Examples: unity-mcp camera set-priority "CM Camera" --priority 20

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --priority / -p | integer | required | nargs=1 | — | — | Priority value. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp camera set-target`

Set camera Follow/LookAt targets. Examples: unity-mcp camera set-target "CM Camera" --follow Player --look-at Player

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method / -s | choice: by_id, by_name, by_path | optional | nargs=1 | — | — |  |
| --follow | text | optional | nargs=1 | — | — | Follow target (name/path/ID). |
| --look-at | text | optional | nargs=1 | — | — | LookAt target (name/path/ID). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp code`

Code operations - read, search, and execute.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp code clear-history`

Clear execution history.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp code execute`

Execute C# code in Unity Editor. Code runs as a method body with access to UnityEngine and UnityEditor. Use 'return' to send data back. Examples: unity-mcp code execute "return Application.unityVersion;" unity-mcp code execute "Debug.Log(Camera.main.name);" unity-mcp code execute -f my_script.cs

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| source | text | optional | nargs=1 | — | — |  |
| --file / -f | file/path (lexical only) | optional | nargs=1 | — | — | Read code from a file instead of argument. |
| --no-safety-checks | boolean | optional | nargs=1, flag | — | — | Disable blocked-pattern checks (allows File.Delete, Process.Start, etc). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp code history`

Show execution history. Examples: unity-mcp code history unity-mcp code history --limit 5

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --limit / -n | integer | optional | nargs=1 | 10 | — | Number of entries to show (default: 10). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp code read`

Read a source file. Examples: unity-mcp code read "Assets/Scripts/Player.cs" unity-mcp code read "Assets/Scripts/Player.cs" --start-line 10 --line-count 20

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --start-line / -s | integer | optional | nargs=1 | — | — | Starting line number (1-based). |
| --line-count / -n | integer | optional | nargs=1 | — | — | Number of lines to read. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp code replay`

Replay a history entry by index. Examples: unity-mcp code replay 0 unity-mcp code replay 3

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| index | integer | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp code search`

Search for patterns in Unity scripts using regex. PATTERN is a regex pattern to search for. PATH is the script path (e.g., Assets/Scripts/Player.cs). Examples: unity-mcp code search "class.*Player" "Assets/Scripts/Player.cs" unity-mcp code search "private.*int" "Assets/Scripts/GameManager.cs" unity-mcp code search "TODO\|FIXME" "Assets/Scripts/Utils.cs"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| pattern | text | required | nargs=1 | — | — |  |
| path | text | required | nargs=1 | — | — |  |
| --max-results / -n | integer | optional | nargs=1 | 50 | — | Maximum number of results (default: 50). |
| --case-sensitive / -c | boolean | optional | nargs=1, flag | — | — | Make search case-sensitive (default: case-insensitive). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp component`

Component operations - add, remove, modify components on GameObjects.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp component add`

Add a component to a GameObject. Examples: unity-mcp component add "Player" Rigidbody unity-mcp component add --search-method by_id -- -81840 BoxCollider unity-mcp component add "Enemy" Rigidbody --properties '&#123;"mass": 5.0, "useGravity": true&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| component_type | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — | How to find the target GameObject. |
| --properties / -p | text | optional | nargs=1 | — | — | Initial properties as JSON (e.g., '&#123;"mass": 5.0&#125;'). Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp component modify`

Set multiple properties on a component at once. Examples: unity-mcp component modify "Player" Rigidbody --properties '&#123;"mass": 5.0, "useGravity": false&#125;' unity-mcp component modify "Light" Light --properties '&#123;"intensity": 2.0, "color": [1, 0, 0, 1]&#125;' unity-mcp component modify "Player" BoxCollider --properties '&#123;"size": [2,2,2]&#125;' --component-index 1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| component_type | text | required | nargs=1 | — | — |  |
| --properties / -p | text | required | nargs=1 | — | — | Properties to set as JSON (e.g., '&#123;"mass": 5.0, "useGravity": false&#125;'). Accepts @file or -/@- stdin. |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — | How to find the target GameObject. |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple components of the same type exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp component remove`

Remove a component from a GameObject. Examples: unity-mcp component remove "Player" Rigidbody unity-mcp component remove --search-method by_id --force -- -81840 BoxCollider unity-mcp component remove "Player" BoxCollider --component-index 1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| component_type | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — | How to find the target GameObject. |
| --force / -f | boolean | optional | nargs=1, flag | — | — | Skip confirmation prompt. |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple components of the same type exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp component set`

Set a single property on a component. Examples: unity-mcp component set "Player" Rigidbody mass 5.0 unity-mcp component set "Enemy" Transform position "[0, 5, 0]" unity-mcp component set --search-method by_id -- -81840 Light intensity 2.5 unity-mcp component set "Player" BoxCollider size "[2,2,2]" --component-index 1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| component_type | text | required | nargs=1 | — | — |  |
| property_name | text | required | nargs=1 | — | — |  |
| value | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path | optional | nargs=1 | — | — | How to find the target GameObject. |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple components of the same type exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp custom_tool`

Alias for tool management (custom tools).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp custom_tool list`

List custom tools registered for the active Unity project.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp docs`

Fetch Unity API documentation.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp docs get`

Fetch documentation for a Unity class or member. Examples: unity-mcp docs get Physics unity-mcp docs get Physics Raycast unity-mcp docs get NavMeshAgent SetDestination --version 6000.0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| class_name | text | required | nargs=1 | — | — |  |
| member_name | text | optional | nargs=1 | — | — |  |
| --version / -v | text | optional | nargs=1 | — | — | Unity version (e.g., 6000.0). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp doctor`

Verify this diagnostic SDK connection; does not attest another desktop client's session.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --protocol | choice: auto, legacy | optional | nargs=1 | "auto" | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor`

Editor operations - play mode, console, tags, layers.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor add-layer`

Add a new layer. Examples: unity-mcp editor add-layer "Interactable"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| layer_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor add-tag`

Add a new tag. Examples: unity-mcp editor add-tag "Enemy" unity-mcp editor add-tag "Collectible"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| tag_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor cancel-play-wait`

Cancel readiness monitoring without stopping Play Mode.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor cancel-test`

Request native cancellation; accepted intent is not confirmed termination.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor console`

Read or clear the Unity console. Examples: unity-mcp editor console unity-mcp editor console --type error --count 20 unity-mcp editor console --filter "NullReference" --stacktrace unity-mcp editor console --clear

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --type / -t | choice: error, warning, log, all | optional | nargs=1, repeatable | ["error", "warning", "log"] | — | Message types to retrieve. |
| --count / -n | integer | optional | nargs=1 | 10 | — | Number of messages to retrieve. |
| --filter / -f | text | optional | nargs=1 | — | — | Filter messages containing this text. |
| --stacktrace / -s | boolean | optional | nargs=1, flag | — | — | Include stack traces. |
| --clear | boolean | optional | nargs=1, flag | — | — | Clear the console instead of reading. |
| --include-mcp-logs | boolean | optional | nargs=1, flag | — | — | Include MCP internal logger messages. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor custom-tool`

Execute a custom Unity tool. Custom tools are registered by Unity projects via the MCP plugin. Examples: unity-mcp editor custom-tool "MyCustomTool" unity-mcp editor custom-tool "BuildPipeline" --params '&#123;"target": "Android"&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| tool_name | text | required | nargs=1 | — | — |  |
| --params / -p | text | optional | nargs=1 | "&#123;&#125;" | — | Tool parameters as JSON. Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor deploy`

Deploy MCPForUnity package from configured source. Copies the configured MCPForUnity source folder into the project's installed package location. The source path must be set in the MCP for Unity Advanced Settings first. Triggers recompilation. Examples: unity-mcp editor deploy

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor export-test`

Export completed test evidence to a new local JSON or JUnit file.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | required | nargs=1 | — | — |  |
| --format | choice: json, junit | optional | nargs=1 | "json" | — |  |
| --output | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor game-view-size`

Read or select the size of an open Game View.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --width | integer range: [1..8192] | optional | nargs=1 | — | — |  |
| --height | integer range: [1..8192] | optional | nargs=1 | — | — |  |
| --aspect-ratio | text | optional | nargs=1 | — | — | Aspect ratio W:H, for example 16:9. |
| --preset | text | optional | nargs=1 | — | — | Unique label returned by this command without options. |
| --restore-token | text | optional | nargs=1 | — | — | Restore a previous selection using a returned token. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor menu`

Execute a menu item. Examples: unity-mcp editor menu "File/Save" unity-mcp editor menu "Edit/Undo" unity-mcp editor menu "GameObject/Create Empty"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| menu_path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor pause`

Toggle pause/resume in play mode.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor play`

Enter play mode.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --wait-until | choice: scene_loaded, first_frame | optional | nargs=1 | — | — | Wait for loaded active scene or subsequent simulation frame (not app async initialization). |
| --timeout-seconds | integer range: [1..300] | optional | nargs=1 | 30 | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor play-status`

Read a persisted play readiness job.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor poll-test`

Poll an async test job for status/results. Examples: unity-mcp editor poll-test abc123 unity-mcp editor poll-test abc123 --wait 60 unity-mcp editor poll-test abc123 --failed-only

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | required | nargs=1 | — | — |  |
| --wait / -w | integer | optional | nargs=1 | 30 | — | Wait up to N seconds for completion (default: 30). |
| --details | boolean | optional | nargs=1, flag | — | — | Include detailed results for all tests. |
| --failed-only | boolean | optional | nargs=1, flag | — | — | Include details for failed/skipped tests only. |
| --page-size | integer range: [1..100] | optional | nargs=1 | — | — | Read one bounded terminal detail page; requires --details or --failed-only. |
| --cursor | text | optional | nargs=1 | — | — | Opaque next_cursor from the previous page. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor redo`

Redo the last undone action. Examples: unity-mcp editor redo

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor refresh`

Force Unity to refresh assets/scripts. Examples: unity-mcp editor refresh unity-mcp editor refresh --mode force unity-mcp editor refresh --compile unity-mcp editor refresh --scope scripts --compile

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --mode | choice: if_dirty, force | optional | nargs=1 | "if_dirty" | — | Refresh mode. |
| --scope | choice: assets, scripts, all | optional | nargs=1 | "all" | — | What to refresh. |
| --compile | boolean | optional | nargs=1, flag | — | — | Request script compilation. |
| --no-wait | boolean | optional | nargs=1, flag | — | — | Don't wait for refresh to complete. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor remove-layer`

Remove a layer. Examples: unity-mcp editor remove-layer "OldLayer"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| layer_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor remove-tag`

Remove a tag. Examples: unity-mcp editor remove-tag "OldTag"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| tag_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor restore`

Restore MCPForUnity package from last backup. Reverts the last deployment by restoring from backup. Examples: unity-mcp editor restore

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor stop`

Stop play mode.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor tests`

Run Unity tests. Examples: unity-mcp editor tests unity-mcp editor tests --mode PlayMode unity-mcp editor tests --async unity-mcp editor tests --wait 60 --failed-only unity-mcp editor tests --clear-stuck

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --mode / -m | choice: EditMode, PlayMode | optional | nargs=1 | "EditMode" | — | Test mode to run. |
| --async | boolean | optional | nargs=1, flag | — | — | Run asynchronously and return job ID for polling. |
| --wait / -w | integer | optional | nargs=1 | — | — | Wait up to N seconds for completion (default: no wait). |
| --run-timeout-ms | integer range: [1..600000] | optional | nargs=1 | — | — | Opt-in native job deadline; requires supported native control. |
| --details | boolean | optional | nargs=1, flag | — | — | Include detailed results for all tests. |
| --failed-only | boolean | optional | nargs=1, flag | — | — | Include details for failed/skipped tests only. |
| --clear-stuck | boolean | optional | nargs=1, flag | — | — | Clear an orphaned running job that is blocking new runs, instead of starting a run. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor tool`

Set the active editor tool. Examples: unity-mcp editor tool "Move" unity-mcp editor tool "Rotate" unity-mcp editor tool "Scale"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| tool_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp editor undo`

Undo the last editor action. Examples: unity-mcp editor undo

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp gameobject`

GameObject operations - create, find, modify, delete GameObjects.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp gameobject create`

Create a new GameObject. Examples: unity-mcp gameobject create "MyCube" --primitive Cube unity-mcp gameobject create "Player" --position 0 1 0 unity-mcp gameobject create "Enemy" --primitive Sphere --tag Enemy unity-mcp gameobject create "Child" --parent "ParentObject" unity-mcp gameobject create "Item" --components "Rigidbody,BoxCollider"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --primitive / -p | choice: Cube, Sphere, Cylinder, Plane, Capsule, Quad | optional | nargs=1 | — | — | Create a primitive type. |
| --position / -pos | float | optional | nargs=3 | — | — | Position as X Y Z. |
| --rotation / -rot | float | optional | nargs=3 | — | — | Rotation as X Y Z (euler angles). |
| --scale / -s | float | optional | nargs=3 | — | — | Scale as X Y Z. |
| --parent | text | optional | nargs=1 | — | — | Parent GameObject name or path. |
| --tag / -t | text | optional | nargs=1 | — | — | Tag to assign. |
| --layer | text | optional | nargs=1 | — | — | Layer to assign. |
| --components | text | optional | nargs=1 | — | — | Comma-separated list of components to add. |
| --save-prefab | boolean | optional | nargs=1, flag | — | — | Save as prefab after creation. |
| --prefab-path | text | optional | nargs=1 | — | — | Path for prefab (e.g., Assets/Prefabs/MyPrefab.prefab). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp gameobject delete`

Delete a GameObject. Examples: unity-mcp gameobject delete "OldObject" unity-mcp gameobject delete --search-method by_id -- -81840 unity-mcp gameobject delete "TempObjects" --search-method by_tag --force

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — | How to find the target GameObject. |
| --force / -f | boolean | optional | nargs=1, flag | — | — | Skip confirmation prompt. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp gameobject duplicate`

Duplicate a GameObject. Examples: unity-mcp gameobject duplicate "Player" unity-mcp gameobject duplicate "Enemy" --name "Enemy2" --offset 5 0 0 unity-mcp gameobject duplicate --search-method by_id -- -81840

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --name / -n | text | optional | nargs=1 | — | — | Name for the duplicate (default: OriginalName_Copy). |
| --offset | float | optional | nargs=3 | — | — | Position offset from original as X Y Z. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — | How to find the target GameObject. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp gameobject find`

Find GameObjects by search criteria. Examples: unity-mcp gameobject find "Player" unity-mcp gameobject find "Enemy" --method by_tag unity-mcp gameobject find --method by_id -- "-81840" unity-mcp gameobject find "Rigidbody" --method by_component unity-mcp gameobject find "/Canvas/Panel" --method by_path

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| search_term | text | required | nargs=1 | — | — |  |
| --method / -m | choice: by_name, by_path, by_id, by_tag, by_layer, by_component | optional | nargs=1 | "by_name" | — | Search method. |
| --include-inactive / -i | boolean | optional | nargs=1, flag | — | — | Include inactive GameObjects. |
| --limit / -l | integer | optional | nargs=1 | 50 | — | Maximum results to return. |
| --cursor / -c | integer | optional | nargs=1 | 0 | — | Pagination cursor (offset). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp gameobject modify`

Modify an existing GameObject. TARGET can be a name, path, instance ID, or tag depending on --search-method. Examples: unity-mcp gameobject modify "Player" --position 0 5 0 unity-mcp gameobject modify "Enemy" --name "Boss" --tag "Boss" unity-mcp gameobject modify --search-method by_id --active -- -81840 unity-mcp gameobject modify "/Canvas/Panel" --search-method by_path --inactive unity-mcp gameobject modify "Cube" --add-components "Rigidbody,BoxCollider" unity-mcp gameobject modify "Ground" --static

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --name / -n | text | optional | nargs=1 | — | — | New name for the GameObject. |
| --position / -pos | float | optional | nargs=3 | — | — | New position as X Y Z. |
| --rotation / -rot | float | optional | nargs=3 | — | — | New rotation as X Y Z (euler angles). |
| --scale / -s | float | optional | nargs=3 | — | — | New scale as X Y Z. |
| --parent | text | optional | nargs=1 | — | — | New parent GameObject. |
| --tag / -t | text | optional | nargs=1 | — | — | New tag. |
| --layer | text | optional | nargs=1 | — | — | New layer. |
| --active / --inactive | boolean | optional | nargs=1, flag | — | — | Set active state. |
| --static / --no-static | boolean | optional | nargs=1, flag | — | — | Set static flag (all StaticEditorFlags on/off). |
| --add-components | text | optional | nargs=1 | — | — | Comma-separated list of components to add. |
| --remove-components | text | optional | nargs=1 | — | — | Comma-separated list of components to remove. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — | How to find the target GameObject. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp gameobject move`

Move a GameObject relative to another object. Examples: unity-mcp gameobject move "Chair" --reference "Table" --direction right --distance 2 unity-mcp gameobject move "Light" --reference "Player" --direction up --distance 3 unity-mcp gameobject move "NPC" --reference "Player" --direction forward --local

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --reference / -r | text | required | nargs=1 | — | — | Reference object for relative movement. |
| --direction / -d | choice: left, right, up, down, forward, back, front, backward, behind | required | nargs=1 | — | — | Direction to move. |
| --distance | float | optional | nargs=1 | 1.0 | — | Distance to move (default: 1.0). |
| --local | boolean | optional | nargs=1, flag | — | — | Use reference object's local space instead of world space. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — | How to find the target GameObject. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics`

Manage rendering graphics: volumes, effects, and pipeline settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-cancel`

Cancel running bake.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-clear`

Clear all baked lighting data.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-create-probes`

Create a light probe group with grid layout.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --name / -n | text | optional | nargs=1 | — | — | Name for the probe group. |
| --spacing / -s | float | optional | nargs=1 | — | — | Grid spacing. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-create-reflection`

Create a reflection probe.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --name / -n | text | optional | nargs=1 | — | — | Name for the reflection probe. |
| --resolution / -r | integer | optional | nargs=1 | — | — | Probe resolution. |
| --mode / -m | text | optional | nargs=1 | — | — | Baked/Realtime/Custom. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-reflection-probe`

Bake a specific reflection probe.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Name or instance ID of GameObject with ReflectionProbe. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-set-settings`

Set lighting/bake settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --setting / -s | tuple: text, text | required | nargs=2, repeatable | — | — | Lighting setting key-value pair. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-settings`

Get current lighting/bake settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-start`

Start lightmap bake.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --sync | boolean | optional | nargs=1, flag | — | — | Synchronous bake (blocks until done). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics bake-status`

Get bake progress/status.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics feature-add`

Add a renderer feature.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --type / -t | text | required | nargs=1 | — | — | Feature type (e.g., FullScreenPassRendererFeature). |
| --name / -n | text | optional | nargs=1 | — | — | Display name. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics feature-configure`

Configure properties on a renderer feature.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --index / -i | integer | optional | nargs=1 | — | — | Feature index. |
| --name / -n | text | optional | nargs=1 | — | — | Feature name. |
| --prop / -p | tuple: text, text | required | nargs=2, repeatable | — | — | Property key-value pair. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics feature-list`

List URP renderer features.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics feature-remove`

Remove a renderer feature.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --index / -i | integer | optional | nargs=1 | — | — | Feature index. |
| --name / -n | text | optional | nargs=1 | — | — | Feature name. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics feature-reorder`

Reorder renderer features.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --order / -o | text | required | nargs=1 | — | — | Comma-separated list of indices (e.g., '2,0,1'). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics feature-toggle`

Enable/disable a renderer feature.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --index / -i | integer | optional | nargs=1 | — | — | Feature index. |
| --name / -n | text | optional | nargs=1 | — | — | Feature name. |
| --active / --inactive | boolean | optional | nargs=1, flag | true | — | Enable or disable. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics ping`

Check graphics system status.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics pipeline-info`

Get active render pipeline, quality level, and settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics pipeline-set-quality`

Switch quality level.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --level / -l | text | required | nargs=1 | — | — | Quality level name or index. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics pipeline-set-settings`

Set pipeline asset settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --setting / -s | tuple: text, text | required | nargs=2, repeatable | — | — | Setting key-value pair (e.g., -s renderScale 0.5 -s supportsHDR true). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics pipeline-settings`

Get detailed pipeline settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics skybox-info`

Get all environment settings (skybox, ambient, fog, reflection, sun).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics skybox-set-ambient`

Set ambient lighting mode and colors.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --mode / -m | text | optional | nargs=1 | — | — | Ambient mode: Skybox, Trilight, Flat, Custom. |
| --intensity / -i | float | optional | nargs=1 | — | — | Ambient intensity. |
| --color / -c | text | optional | nargs=1 | — | — | Sky/ambient color as 'r,g,b[,a]'. |
| --equator-color | text | optional | nargs=1 | — | — | Equator color as 'r,g,b[,a]' (Trilight mode). |
| --ground-color | text | optional | nargs=1 | — | — | Ground color as 'r,g,b[,a]' (Trilight mode). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics skybox-set-fog`

Enable and configure fog.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --enable / --disable | boolean | optional | nargs=1, flag | — | — | Enable or disable fog. |
| --mode / -m | text | optional | nargs=1 | — | — | Fog mode: Linear, Exponential, ExponentialSquared. |
| --color / -c | text | optional | nargs=1 | — | — | Fog color as 'r,g,b[,a]'. |
| --density / -d | float | optional | nargs=1 | — | — | Fog density. |
| --start | float | optional | nargs=1 | — | — | Fog start distance (Linear). |
| --end | float | optional | nargs=1 | — | — | Fog end distance (Linear). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics skybox-set-material`

Set the skybox material by asset path.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --material / -m | text | required | nargs=1 | — | — | Asset path to skybox material. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics skybox-set-properties`

Set properties on the current skybox material.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --prop / -p | tuple: text, text | required | nargs=2, repeatable | — | — | Material property key-value pair (e.g., -p _Exposure 1.3). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics skybox-set-reflection`

Configure environment reflection settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --intensity / -i | float | optional | nargs=1 | — | — | Reflection intensity. |
| --bounces / -b | integer | optional | nargs=1 | — | — | Reflection bounces. |
| --mode / -m | text | optional | nargs=1 | — | — | Reflection mode: Skybox, Custom. |
| --resolution / -r | integer | optional | nargs=1 | — | — | Default reflection resolution. |
| --cubemap | text | optional | nargs=1 | — | — | Custom cubemap asset path. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics skybox-set-sun`

Set the sun source light for the environment.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Light GameObject name or instance ID. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics stats`

Get rendering performance stats.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics stats-debug-mode`

Set Scene view debug visualization mode.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --mode / -m | text | required | nargs=1 | — | — | Debug mode (Overdraw, Wireframe, Mipmaps, etc.). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics stats-memory`

Get memory allocation stats.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics volume-add-effect`

Add an effect override to a Volume.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Volume name or instance ID. |
| --effect / -e | text | required | nargs=1 | — | — | Effect type (e.g., Bloom, Vignette). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics volume-create`

Create a Volume GameObject with a profile.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --name / -n | text | optional | nargs=1 | — | — | Name for the Volume GameObject. |
| --global / --local | boolean | optional | nargs=1, flag | true | — | Global or local Volume. |
| --weight / -w | float | optional | nargs=1 | — | — | Volume weight (0-1). |
| --priority / -p | float | optional | nargs=1 | — | — | Volume priority. |
| --profile-path | text | optional | nargs=1 | — | — | Existing VolumeProfile asset path to assign. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics volume-create-profile`

Create a standalone VolumeProfile asset.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --path / -p | text | required | nargs=1 | — | — | Asset path for the VolumeProfile (e.g., Assets/Profiles/MyProfile.asset). |
| --name / -n | text | optional | nargs=1 | — | — | Display name for the profile. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics volume-info`

Get all effects and parameters on a Volume.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Volume name or instance ID. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics volume-list-effects`

List available VolumeComponent effect types.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics volume-remove-effect`

Remove an effect from a Volume.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Volume name or instance ID. |
| --effect / -e | text | required | nargs=1 | — | — | Effect type to remove. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics volume-set-effect`

Set parameters on a Volume effect.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Volume name or instance ID. |
| --effect / -e | text | required | nargs=1 | — | — | Effect type (e.g., Bloom). |
| --param / -p | tuple: text, text | optional | nargs=2, repeatable | — | — | Parameter key-value pair. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp graphics volume-set-properties`

Set Volume properties (weight, priority, is_global).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Volume name or instance ID. |
| --weight / -w | float | optional | nargs=1 | — | — | Volume weight (0-1). |
| --priority / -p | float | optional | nargs=1 | — | — | Volume priority. |
| --global / --local | boolean | optional | nargs=1, flag | — | — | Global or local Volume. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp instance`

Unity instance management - list the connected instances and show the one in use.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp instance current`

Show the currently selected Unity instance. Examples: unity-mcp instance current

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp instance list`

List available Unity instances. Examples: unity-mcp instance list

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp instance set`

Explain how to target stateless CLI requests with INSTANCE_ID. Use --instance on each command or set UNITY_MCP_INSTANCE in your shell. CLI requests cannot persist an active MCP session selection.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| instance_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp instances`

List available Unity instances.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp lighting`

Lighting operations - create, modify lights and lighting settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp lighting create`

Create a new light. Examples: unity-mcp lighting create "MainLight" --type Directional unity-mcp lighting create "PointLight1" --position 0 5 0 --intensity 2 unity-mcp lighting create "RedLight" --type Spot --color 1 0 0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --type / -t | choice: Directional, Point, Spot, Area | optional | nargs=1 | "Point" | — | Type of light to create. |
| --position / -pos | float | optional | nargs=3 | [0, 3, 0] | — | Position as X Y Z. |
| --color / -c | float | optional | nargs=3 | — | — | Color as R G B (0-1). |
| --intensity / -i | float | optional | nargs=1 | — | — | Light intensity. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp material`

Material operations - create, modify, assign materials.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp material assign`

Assign a material to a GameObject's renderer. Examples: unity-mcp material assign "Assets/Materials/Red.mat" "Cube" unity-mcp material assign "Assets/Materials/Blue.mat" "Player" --mode instance unity-mcp material assign --search-method by_id --slot 1 -- "Assets/Materials/Mat.mat" -81840

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| material_path | text | required | nargs=1 | — | — |  |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_id, by_name, by_path, by_tag, by_layer, by_component | optional | nargs=1 | — | — | How to find the target GameObject. |
| --slot / -s | integer | optional | nargs=1 | 0 | — | Material slot index (default: 0). |
| --mode / -m | choice: shared, instance, property_block, create_unique | optional | nargs=1 | "shared" | — | Assignment mode. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp material create`

Create a new material. Examples: unity-mcp material create "Assets/Materials/NewMat.mat" unity-mcp material create "Assets/Materials/Red.mat" --shader "Universal Render Pipeline/Lit" unity-mcp material create "Assets/Materials/Blue.mat" --properties '&#123;"_Color": [0,0,1,1]&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --shader / -s | text | optional | nargs=1 | "Standard" | — | Shader to use (default: Standard). |
| --properties / -p | text | optional | nargs=1 | — | — | Initial properties as JSON. Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp material info`

Get information about a material. Examples: unity-mcp material info "Assets/Materials/Red.mat"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp material set-color`

Set a material's color. Examples: unity-mcp material set-color "Assets/Materials/Red.mat" 1 0 0 unity-mcp material set-color "Assets/Materials/Blue.mat" 0 0 1 0.5 unity-mcp material set-color "Assets/Materials/Mat.mat" 1 1 0 --property "_BaseColor"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| r | float | required | nargs=1 | — | — |  |
| g | float | required | nargs=1 | — | — |  |
| b | float | required | nargs=1 | — | — |  |
| a | float | optional | nargs=1 | 1.0 | — |  |
| --property / -p | text | optional | nargs=1 | "_Color" | — | Color property name (default: _Color). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp material set-property`

Set a shader property on a material. Examples: unity-mcp material set-property "Assets/Materials/Mat.mat" _Metallic 0.5 unity-mcp material set-property "Assets/Materials/Mat.mat" _Smoothness 0.8 unity-mcp material set-property "Assets/Materials/Mat.mat" _MainTex "Assets/Textures/Tex.png"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| property_name | text | required | nargs=1 | — | — |  |
| value | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp material set-renderer-color`

Set a renderer's material color directly. Examples: unity-mcp material set-renderer-color "Cube" 1 0 0 unity-mcp material set-renderer-color "Player" 0 1 0 --mode instance

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| r | float | required | nargs=1 | — | — |  |
| g | float | required | nargs=1 | — | — |  |
| b | float | required | nargs=1 | — | — |  |
| a | float | optional | nargs=1 | 1.0 | — |  |
| --search-method | choice: by_id, by_name, by_path, by_tag, by_layer, by_component | optional | nargs=1 | — | — | How to find the target GameObject. |
| --mode / -m | choice: shared, instance, property_block, create_unique | optional | nargs=1 | "property_block" | — | Modification mode (default: property_block — use create_unique for persistent per-object material). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages`

Package management - install, remove, search, registries.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages add`

Install a package. Examples: unity-mcp packages add com.unity.inputsystem unity-mcp packages add com.unity.inputsystem@1.8.0 unity-mcp packages add https://github.com/user/repo.git

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| package | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages add-registry`

Add a scoped registry. Examples: unity-mcp packages add-registry OpenUPM --url https://package.openupm.com --scope com.cysharp --scope com.neuecc

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| registry_name | text | required | nargs=1 | — | — |  |
| --url | text | required | nargs=1 | — | — | Registry URL. |
| --scope / -s | text | required | nargs=1, repeatable | — | — | Package scope (can specify multiple). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages embed`

Embed a package for local editing. Examples: unity-mcp packages embed com.unity.timeline

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| package | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages info`

Get detailed package info. Examples: unity-mcp packages info com.unity.inputsystem

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| package | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages list`

List installed packages. Examples: unity-mcp packages list

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages list-registries`

List all scoped registries. Examples: unity-mcp packages list-registries

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages ping`

Check package manager status. Examples: unity-mcp packages ping

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages remove`

Remove a package. Examples: unity-mcp packages remove com.unity.inputsystem unity-mcp packages remove com.unity.inputsystem --force

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| package | text | required | nargs=1 | — | — |  |
| --force / -f | boolean | optional | nargs=1, flag | — | — | Force removal even if other packages depend on it. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages remove-registry`

Remove a scoped registry. Examples: unity-mcp packages remove-registry OpenUPM

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| registry_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages resolve`

Force re-resolution of all packages. Examples: unity-mcp packages resolve

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages search`

Search Unity package registry. Examples: unity-mcp packages search input unity-mcp packages search xr

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| query | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp packages status`

Check package operation status. Examples: unity-mcp packages status unity-mcp packages status abc123

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics`

Manage 3D and 2D physics: settings, collision matrix, materials, joints, queries, validation.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics add-joint`

Add a physics joint to a GameObject.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Target GameObject. |
| --joint-type / -j | text | required | nargs=1 | — | — | Joint type (e.g. hinge, fixed, spring). |
| --connected-body | text | optional | nargs=1 | — | — | Connected body GameObject. |
| --dimension / -d | text | optional | nargs=1 | — | — | 3d or 2d (auto-detected if omitted). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics apply-force`

Apply force to a Rigidbody.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Target GameObject. |
| --force / -f | text | required | nargs=1 | — | — | Force vector as 'x,y,z' or 'x,y'. |
| --force-mode | text | optional | nargs=1 | "Force" | — | Force, Impulse, Acceleration, VelocityChange. |
| --dimension / -d | text | optional | nargs=1 | — | — | 3d or 2d. |
| --torque | text | optional | nargs=1 | — | — | Torque as 'x,y,z' (3D) or float (2D). |
| --position / -p | text | optional | nargs=1 | — | — | Point to apply force at, as 'x,y,z'. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics assign-material`

Assign a physics material to a GameObject's collider.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Target GameObject name or instance ID. |
| --material-path / -m | text | required | nargs=1 | — | — | Path to physics material asset. |
| --collider-type | text | optional | nargs=1 | — | — | Specific collider type. |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple colliders of the same type exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics configure-joint`

Configure a joint on a GameObject (key=value ...).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Target GameObject. |
| --joint-type / -j | text | optional | nargs=1 | — | — | Joint type to target. |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple joints of the same type exist. |
| properties | text | optional | nargs=-1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics configure-material`

Configure a physics material asset (key=value ...).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --path / -p | text | required | nargs=1 | — | — | Asset path to physics material. |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| properties | text | optional | nargs=-1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics configure-rigidbody`

Configure Rigidbody properties (key=value ...).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Target GameObject. |
| --dimension / -d | text | optional | nargs=1 | — | — | 3d or 2d. |
| properties | text | optional | nargs=-1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics create-material`

Create a physics material asset.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --name / -n | text | required | nargs=1 | — | — | Material name. |
| --path / -p | text | optional | nargs=1 | — | — | Folder path. |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --bounciness / -b | float | optional | nargs=1 | — | — | Bounciness (0-1). |
| --dynamic-friction | float | optional | nargs=1 | — | — | Dynamic friction. |
| --static-friction | float | optional | nargs=1 | — | — | Static friction. |
| --friction | float | optional | nargs=1 | — | — | Friction (2D only). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics get-collision-matrix`

Get layer collision matrix.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics get-rigidbody`

Get Rigidbody state (mass, velocity, position, etc.).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --dimension / -d | text | optional | nargs=1 | — | — | 3d or 2d (auto-detected if omitted). |
| --search-method | text | optional | nargs=1 | — | — | Search method for target resolution. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics get-settings`

Get physics project settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics linecast`

Check if anything intersects the line between two points.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --start / -s | text | required | nargs=1 | — | — | Start point as 'x,y,z'. |
| --end / -e | text | required | nargs=1 | — | — | End point as 'x,y,z'. |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics overlap`

Perform a physics overlap query.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --shape / -s | text | required | nargs=1 | — | — | Shape: sphere, box, capsule (3D); circle, box, capsule (2D). |
| --position / -p | text | required | nargs=1 | — | — | Position as 'x,y,z' or 'x,y'. |
| --size | text | required | nargs=1 | — | — | Size: float for sphere/circle radius, or 'x,y,z' for box. |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics ping`

Check physics system status.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics raycast`

Perform a physics raycast.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --origin / -o | text | required | nargs=1 | — | — | Origin as 'x,y,z'. |
| --direction / -d | text | required | nargs=1 | — | — | Direction as 'x,y,z'. |
| --max-distance | float | optional | nargs=1 | — | — | Max distance. |
| --dimension | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics raycast-all`

Perform a multi-hit raycast returning all hits.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --origin / -o | text | required | nargs=1 | — | — | Origin as 'x,y,z'. |
| --direction / -d | text | required | nargs=1 | — | — | Direction as 'x,y,z'. |
| --max-distance | float | optional | nargs=1 | — | — | Max distance. |
| --dimension | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics remove-joint`

Remove joint(s) from a GameObject.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | required | nargs=1 | — | — | Target GameObject. |
| --joint-type / -j | text | optional | nargs=1 | — | — | Joint type to remove (omit to remove all). |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple joints of the same type exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics set-collision-matrix`

Set collision between two layers.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| layer_a | text | required | nargs=1 | — | — |  |
| layer_b | text | required | nargs=1 | — | — |  |
| --collide / --ignore | boolean | optional | nargs=1, flag | true | — | Enable or disable collision. |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics set-settings`

Set a physics setting (key value). Gravity JSON accepts @file or -/@- stdin.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| key | text | required | nargs=1 | — | — |  |
| value | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics shapecast`

Cast a shape (sphere, box, capsule) along a direction.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --shape / -s | text | required | nargs=1 | — | — | Shape: sphere, box, capsule (3D); circle, box, capsule (2D). |
| --origin / -o | text | required | nargs=1 | — | — | Origin as 'x,y,z' or 'x,y'. |
| --direction / -d | text | required | nargs=1 | — | — | Direction as 'x,y,z' or 'x,y'. |
| --size | text | required | nargs=1 | — | — | Size: float for sphere/circle radius, or 'x,y,z' for box. |
| --max-distance | float | optional | nargs=1 | — | — | Max distance. |
| --dimension | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics simulate`

Run physics simulation steps in edit mode.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --steps / -s | integer | optional | nargs=1 | 1 | — | Number of steps (max 100). |
| --step-size | float | optional | nargs=1 | — | — | Step size in seconds. |
| --dimension / -d | text | optional | nargs=1 | "3d" | — | 3d or 2d. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp physics validate`

Validate physics setup for common mistakes.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --target / -t | text | optional | nargs=1 | — | — | Target GameObject (or whole scene). |
| --dimension / -d | text | optional | nargs=1 | "both" | — | 3d, 2d, or both. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario`

Save and run repeatable Play Mode scenarios; status never waits or polls.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario analyze-flakiness`

Analyze &lt;=64 explicit run.json files offline. Exit 0 analyzed, 2 incomplete. No execution, retry or history discovery occurs. Session JSONL and soak parent reports are unsupported; an ordinary selected iteration cannot establish a whole soak outcome. Only compatible known definition/environment groups establish cross-run history.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| reports | file/path (lexical only) | required | nargs=-1 | — | — |  |
| --output | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario analyze-history`

Analyze &lt;=16 explicit session/soak summaries offline. Exit 0 analyzed, 2 incomplete. Reads bounded local journals and owned soak artifacts. All outcomes survive report eviction; source-local trends and streaks never infer missing batch iteration order.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| histories | file/path (lexical only) | required | nargs=-1 | — | — |  |
| --output | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario baseline`

Register immutable successful soaks and audit explicit pin selections.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --registry | file/path (lexical only) | required | nargs=1 | — | — | Explicit local registry file or existing directory. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario baseline check`

Apply the existing exact budget gate to a revalidated named baseline.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| candidate | file/path (lexical only) | required | nargs=1 | — | — |  |
| --name | text | optional | nargs=1 | — | — | Require this compatible pinned name; required if several match. |
| --policy | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario baseline compare`

Compare a candidate against one compatible, revalidated pinned baseline.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| candidate | file/path (lexical only) | required | nargs=1 | — | — |  |
| --name | text | optional | nargs=1 | — | — | Require this compatible pinned name; required if several match. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario baseline history`

Show the complete bounded registration and pin provenance.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario baseline list`

List and revalidate only explicitly registered saved evidence.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario baseline pin`

Pin a registered baseline without changing its evidence or other pins.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --reason | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario baseline register`

Admit one saved soak under a new immutable name.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario baseline unpin`

Unpin a baseline while preserving its registration and audit trail.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --reason | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario cancel`

Request cancellation; a processed request succeeds regardless of the job's outcome. An in-flight native scene load cannot be cancelled.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario capture-ui-snapshot`

Save a complete bounded UI tree to a new file; missing evidence fails capture.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --framework | choice: ugui, uitoolkit | required | nargs=1 | — | — |  |
| --target | text | required | nargs=1 | — | — | Unique scene hierarchy path or instance ID. |
| --max-nodes | integer range: [1..1000] | optional | nargs=1 | 200 | — |  |
| --output | file/path (lexical only) | required | nargs=1 | — | — |  |
| --identity-map-file | file/path (lexical only) | optional | nargs=1 | — | — | Optional bounded JSON object containing identity_map; emits v3 comparison IDs, not mutation handles. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario check-soak-budget`

Gate saved soak regression limits offline: 0 within, 1 exceeded, 2 not comparable. Requires complete successful evidence and known matching desktop Player environments. The explicit JSON policy selects metrics and limits. Growth does not establish a leak.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| baseline_directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| candidate_directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| --policy | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario compare-queries`

Compare two saved native reports offline; exit 0 within limits, 1 exceeded, 2 invalid.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | text | required | nargs=1 | — | — |  |
| candidate | text | required | nargs=1 | — | — |  |
| --max-target-searches-increase | integer range: [0..9223372036854775807] | required | nargs=1 | — | — |  |
| --max-hierarchy-visits-increase | integer range: [0..9223372036854775807] | required | nargs=1 | — | — |  |
| --step-details-limit | integer range: [1..640] | optional | nargs=1 | — | — | Include up to N steps in execution order after validating all recorded step counts. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario compare-soak-metrics`

Compare complete saved soak diagnostics offline; 0 computed, 2 insufficient/invalid. Managed bytes, allocated bytes and object counts use aligned post-warmup samples. Growth does not establish a leak. No Player is launched and no files are changed.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| baseline_directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| candidate_directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario compare-ui-snapshots`

Compare saved JSONs offline: exit 0 same, 1 changed, 2 not comparable.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | file/path (lexical only) | required | nargs=1 | — | — |  |
| candidate | file/path (lexical only) | required | nargs=1 | — | — |  |
| --geometry-tolerance | float range: [0..10000] | optional | nargs=1 | 0.0 | — |  |
| --require-occlusion | boolean | optional | nargs=1, flag | — | — | Require actual occlusion proof (unavailable). |
| --output | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario compare-visual`

Compare explicit PNGs offline: exit 0 within budget, 1 exceeded, 2 not comparable. Strict policy declares fixed dimensions, RGBA channel thresholds, maximum changed pixel ratio and ignored rectangles. No resizing or implicit image matching occurs. Output directory optionally receives new bounded heatmap.png and diff.png.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | file/path (lexical only) | required | nargs=1 | — | — |  |
| candidate | file/path (lexical only) | required | nargs=1 | — | — |  |
| --policy | file/path (lexical only) | required | nargs=1 | — | — |  |
| --output-dir | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario compare-visual-reports`

Compare one named native capture offline: exit 0 within, 1 exceeded, 2 invalid. Capture paths must remain under each explicit report directory. Captured status, dimensions, SHA256 and exact stage/step/iteration pairing are required.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | file/path (lexical only) | required | nargs=1 | — | — |  |
| candidate | file/path (lexical only) | required | nargs=1 | — | — |  |
| --policy | file/path (lexical only) | required | nargs=1 | — | — |  |
| --checkpoint | text | required | nargs=1 | — | — |  |
| --stage | choice: setup, main, cleanup | required | nargs=1 | — | — |  |
| --step-index | integer range: [0..63] | required | nargs=1 | — | — |  |
| --iteration | integer range: [1..10] | required | nargs=1 | — | — |  |
| --output-dir | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario delete`

Delete a saved scenario definition.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario export-html`

Export a run/suite JSON or saved soak directory to one new offline HTML file. --baseline adds full compatible soak curves when SOURCE is a soak directory.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| source | file/path (lexical only) | required | nargs=1 | — | — |  |
| --output | file/path (lexical only) | required | nargs=1 | — | — |  |
| --comparison | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --analysis | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --visual | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --baseline | file/path (lexical only) | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario get`

Get a saved scenario definition.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario list`

List saved scenario definitions.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario player-run`

Launch an explicit local Player bundle, wait, and export actual run.json plus JUnit. Each invocation owns a new output subdirectory. Timeout and Ctrl+C request cleanup once, then terminate only the owned process if its bounded cleanup wait expires.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| build_directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| --output-dir | file/path (lexical only) | required | nargs=1 | — | — |  |
| --repeat-count | integer range: [1..10] | optional | nargs=1 | 1 | — |  |
| --timeout-seconds | integer range: [1..1800] | optional | nargs=1 | 300 | — |  |
| --source-revision | text | optional | nargs=1 | — | — |  |
| --expected-build-revision | text | optional | nargs=1 | — | — | Require the frozen build label to match. |
| --expected-build-id | text | optional | nargs=1 | — | — |  |
| --require-verified-payload | boolean | optional | nargs=1, flag | — | — | Reject legacy unverified bundles. |
| --graphical | boolean | optional | nargs=1, flag | — | — | Run with graphics for screenshot checkpoints. |
| --cleanup-wait-seconds | integer range: [1..600] | optional | nargs=1 | 360 | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario player-session`

Run bounded verified Player batches and stream outcomes with explicit process scope.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| build_directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| --output-dir | file/path (lexical only) | required | nargs=1 | — | — |  |
| --mode | choice: shared-batches, fresh-process, compare | optional | nargs=1 | "shared-batches" | — |  |
| --iterations | integer range: [1..1000] | optional | nargs=1 | 100 | — |  |
| --batch-size | integer range: [2..10] | optional | nargs=1 | 10 | — |  |
| --max-runtime-seconds | integer range: [1..86400] | optional | nargs=1 | 3600 | — |  |
| --timeout-seconds | integer range: [1..1800] | optional | nargs=1 | 300 | — |  |
| --cleanup-wait-seconds | integer range: [1..600] | optional | nargs=1 | 360 | — |  |
| --interval-seconds | float range: [0..60] | optional | nargs=1 | 0.5 | — |  |
| --failure-policy | choice: stop, continue | optional | nargs=1 | "stop" | — |  |
| --retain-reports | integer range: [1..64] | optional | nargs=1 | 16 | — |  |
| --source-revision | text | optional | nargs=1 | — | — |  |
| --expected-build-revision | text | optional | nargs=1 | — | — | Require the frozen build label to match. |
| --expected-build-id | text | optional | nargs=1 | — | — |  |
| --graphical | boolean | optional | nargs=1, flag | — | — | Run each child with graphics for screenshot checkpoints. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario player-session-recover`

Recover an incomplete evidence snapshot without replaying or modifying the source session.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| session_directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| --output-dir | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario player-soak`

Execute bounded iterations in one verified native Player process and export evidence.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| build_directory | file/path (lexical only) | required | nargs=1 | — | — |  |
| --output-dir | file/path (lexical only) | required | nargs=1 | — | — |  |
| --iterations | integer range: [2..1000] | optional | nargs=1 | 100 | — |  |
| --max-runtime-seconds | integer range: [1..86400] | optional | nargs=1 | 3600 | — |  |
| --timeout-seconds | integer range: [1..1800] | optional | nargs=1 | 300 | — |  |
| --failure-policy | choice: stop, continue | optional | nargs=1 | "stop" | — |  |
| --retain-reports | integer range: [1..64] | optional | nargs=1 | 4 | — |  |
| --source-revision | text | optional | nargs=1 | — | — |  |
| --expected-build-revision | text | optional | nargs=1 | — | — |  |
| --expected-build-id | text | optional | nargs=1 | — | — |  |
| --cleanup-wait-seconds | integer range: [1..600] | optional | nargs=1 | 360 | — |  |
| --graphical | boolean | optional | nargs=1, flag | — | — | Run with graphics for screenshot checkpoints. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario regression-run`

Run one explicit profile: exit 0 passed, 1 failed, 2 unavailable, 130 cancelled. Output must be new. Build/policy/reference paths are relative to the profile file. Every Player requires verified payload provenance. Baselines are never repinned.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| profile | file/path (lexical only) | required | nargs=1 | — | — |  |
| --output-dir | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario reports`

Read retained terminal reports once; history outcomes do not change query success.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --name | text | optional | nargs=1 | — | — | Optional saved scenario name filter. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario run`

Start once and return immediately; use status for later observations. An idempotent retry reporting an unsuccessful terminal job exits with code 1.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --job-id | text | optional | nargs=1 | — | — | Optional 32-character lowercase hex request key. |
| --repeat-count | integer range: [1..10] | optional | nargs=1 | 1 | — |  |
| --timeout-seconds | integer range: [1..1800] | optional | nargs=1 | 300 | — |  |
| --source-revision | text | optional | nargs=1 | — | — | Optional caller-provided reproduction label. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario save`

Save a whole scenario from a UTF-8 JSON file (or - for stdin).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| definition | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario status`

Read the job report once, including step status and bounded failure logs. Failed, timed_out and cancelled jobs exit with code 1.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| job_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario suite-cancel`

Request native cancellation once, including child cleanup.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| suite_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario suite-delete`

Delete a saved native suite definition.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario suite-get`

Get a saved native suite definition.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario suite-list`

List saved native suite names.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario suite-reports`

Query retained native suite reports once.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --name | text | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario suite-run`

Run one native suite, explicitly wait, and save suite.json plus junit.xml. Requires global --instance to pin every observation to the same Editor. Timeout or Ctrl+C requests cancellation once and observes bounded finalization.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --suite-id | text | optional | nargs=1 | — | — | Optional 32-character lowercase hex request key. |
| --repeat-count | integer range: [1..10] | optional | nargs=1 | 1 | — |  |
| --timeout-seconds | integer range: [1..1800] | optional | nargs=1 | 300 | — |  |
| --source-revision | text | optional | nargs=1 | — | — |  |
| --output-dir | file/path (lexical only) | required | nargs=1 | — | — |  |
| --poll-interval-seconds | float range: [0.1..10] | optional | nargs=1 | 0.5 | — |  |
| --cleanup-wait-seconds | integer range: [1..600] | optional | nargs=1 | 360 | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario suite-save`

Save a bounded native suite definition from UTF-8 JSON or stdin.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| definition | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp play-scenario suite-status`

Observe a suite once; this command never waits.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| suite_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab`

Prefab operations - info, hierarchy, open, save, close, create prefabs.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab apply-overrides`

Apply selected scene instance overrides to its nearest prefab asset.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --prefab-path | text | required | nargs=1 | — | — | Destination prefabPath returned by prefab overrides |
| --override-id | text | required | nargs=1, repeatable | — | — | Exact overrideId; repeat to select properties |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab close`

Close the current prefab stage. Examples: unity-mcp prefab close unity-mcp prefab close --save

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --save / -s | boolean | optional | nargs=1, flag | — | — | Save the prefab before closing. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab create`

Create a prefab from a scene GameObject. Examples: unity-mcp prefab create "Player" "Assets/Prefabs/Player.prefab" unity-mcp prefab create "Enemy" "Assets/Prefabs/Enemy.prefab" --overwrite unity-mcp prefab create "EnemyInstance" "Assets/Prefabs/BossEnemy.prefab" --unlink-if-instance

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| path | text | required | nargs=1 | — | — |  |
| --overwrite | boolean | optional | nargs=1, flag | — | — | Overwrite existing prefab at path. |
| --include-inactive | boolean | optional | nargs=1, flag | — | — | Include inactive objects when finding target. |
| --unlink-if-instance | boolean | optional | nargs=1, flag | — | — | Unlink from existing prefab before creating new one. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab hierarchy`

Get the hierarchical structure of a prefab. Examples: unity-mcp prefab hierarchy "Assets/Prefabs/Player.prefab" unity-mcp prefab hierarchy "Assets/Prefabs/UI.prefab" --compact unity-mcp prefab hierarchy "Assets/Prefabs/Complex.prefab" --show-prefab-info

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --compact / -c | boolean | optional | nargs=1, flag | — | — | Show compact output (names and paths only). |
| --show-prefab-info / -p | boolean | optional | nargs=1, flag | — | — | Show prefab nesting information. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab info`

Get information about a prefab asset. Examples: unity-mcp prefab info "Assets/Prefabs/Player.prefab" unity-mcp prefab info "Assets/Prefabs/UI.prefab" --compact

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --compact / -c | boolean | optional | nargs=1, flag | — | — | Show compact output (key values only). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab modify`

Modify a prefab's contents (headless, no UI). Examples: unity-mcp prefab modify "Assets/Prefabs/Player.prefab" --delete-child Child1 unity-mcp prefab modify "Assets/Prefabs/Player.prefab" --delete-child "Turret/Barrel" --delete-child Bullet unity-mcp prefab modify "Assets/Prefabs/Player.prefab" --target Weapon --position "0,1,2" unity-mcp prefab modify "Assets/Prefabs/Player.prefab" --set-property "Rigidbody.mass=5" unity-mcp prefab modify "Assets/Prefabs/Player.prefab" --create-child '&#123;"name":"Spawn","primitive_type":"Sphere"&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --target / -t | text | optional | nargs=1 | — | — | Target object name/path within prefab (default: root) |
| --position / -p | text | optional | nargs=1 | — | — | New local position as 'x,y,z' |
| --rotation / -r | text | optional | nargs=1 | — | — | New local rotation as 'x,y,z' |
| --scale / -s | text | optional | nargs=1 | — | — | New local scale as 'x,y,z' |
| --name / -n | text | optional | nargs=1 | — | — | New name for target |
| --tag | text | optional | nargs=1 | — | — | New tag |
| --layer | text | optional | nargs=1 | — | — | New layer |
| --active / --inactive | boolean | optional | nargs=1, flag | — | — | Set active state |
| --parent | text | optional | nargs=1 | — | — | New parent object name/path |
| --add-component | text | optional | nargs=1, repeatable | — | — | Component type to add (repeatable) |
| --remove-component | text | optional | nargs=1, repeatable | — | — | Component type to remove (repeatable) |
| --set-property | text | optional | nargs=1, repeatable | — | — | Property as 'Component.prop=value' (repeatable) |
| --delete-child | text | optional | nargs=1, repeatable | — | — | Child name/path to remove (repeatable) |
| --create-child | text | optional | nargs=1 | — | — | JSON object for child creation Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab open`

Open a prefab in the prefab stage for editing. Examples: unity-mcp prefab open "Assets/Prefabs/Player.prefab"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab overrides`

List scene prefab instance overrides and IDs for explicit apply/revert selection.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --object-id | integer range: [-2147483648..2147483647] | optional | nargs=1 | — | — | Filter by GameObject/component instance ID |
| --property-filter | text | optional | nargs=1 | — | — | Case-insensitive property-path substring |
| --offset | integer range: [0..None] | optional | nargs=1 | 0 | — |  |
| --page-size | integer range: [1..500] | optional | nargs=1 | 100 | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab revert-overrides`

Revert explicitly selected scene prefab instance overrides with Undo.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --override-id | text | required | nargs=1, repeatable | — | — | Exact overrideId from prefab overrides; repeat to select properties |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp prefab save`

Save the currently open prefab stage. Examples: unity-mcp prefab save

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder`

ProBuilder operations - 3D modeling, mesh editing, UV management.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder auto-smooth`

Auto-assign smoothing groups by angle threshold. Examples: unity-mcp probuilder auto-smooth "MyCube" unity-mcp probuilder auto-smooth "MyCube" --angle 45

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --angle | float | optional | nargs=1 | 30.0 | — | Angle threshold in degrees (default: 30). |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder bevel-edges`

Bevel edges of a ProBuilder mesh. Examples: unity-mcp probuilder bevel-edges "MyCube" --edges '[0,1,2]' --amount 0.2 unity-mcp probuilder bevel-edges "MyCube" --edges '[&#123;"a":0,"b":1&#125;]' --amount 0.15

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --edges | text | required | nargs=1 | — | — | Edge indices as JSON array [0,1] or vertex pairs [&#123;"a":0,"b":1&#125;]. Accepts @file or -/@- stdin. |
| --amount / -a | float | optional | nargs=1 | 0.1 | — | Bevel amount (0-1). |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder center-pivot`

Move pivot point to mesh bounds center. Examples: unity-mcp probuilder center-pivot "MyCube"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder create-poly`

Create a ProBuilder mesh from a 2D polygon footprint. Examples: unity-mcp probuilder create-poly --points "[[0,0,0],[5,0,0],[5,0,5],[0,0,5]]" --height 3

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --points / -p | text | required | nargs=1 | — | — | Points as JSON: [[x,y,z], ...] Accepts @file or -/@- stdin. |
| --height / -h | float | optional | nargs=1 | 1.0 | — | Extrude height. |
| --name / -n | text | optional | nargs=1 | — | — | Name for the created GameObject. |
| --flip-normals | boolean | optional | nargs=1, flag | — | — | Flip face normals. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder create-shape`

Create a ProBuilder shape with real dimensions. Shape types: Cube, Cylinder, Sphere, Plane, Cone, Torus, Pipe, Arch, Stair, CurvedStair, Door, Prism Each shape accepts type-specific dimension parameters: Cube/Prism: width, height, depth (or size for uniform) Cylinder: radius, height, segments/axisDivisions, heightCuts Cone: radius, height, segments/subdivAxis Sphere: radius, subdivisions Torus: innerRadius, outerRadius, rows, columns Pipe: radius, height, thickness, segments Plane: width, height, widthCuts, heightCuts Stair: width, height, depth, steps, buildSides CurvedStair: width, height, innerRadius, circumference, steps Arch: radius, width, depth, angle, radialCuts Door: width, height, depth, ledgeHeight, legWidth Examples: unity-mcp probuilder create-shape Cube unity-mcp probuilder create-shape Cube --params '&#123;"width": 2, "height": 3, "depth": 1&#125;' unity-mcp probuilder create-shape Cylinder --params '&#123;"radius": 0.5, "height": 3, "segments": 16&#125;' unity-mcp probuilder create-shape Torus --name "MyTorus" --params '&#123;"innerRadius": 0.2, "outerRadius": 1&#125;' unity-mcp probuilder create-shape Stair --position 0 0 5 --params '&#123;"steps": 10, "width": 2&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| shape_type | text | required | nargs=1 | — | — |  |
| --name / -n | text | optional | nargs=1 | — | — | Name for the created GameObject. |
| --position | float | optional | nargs=3 | — | — | Position X Y Z. |
| --rotation | float | optional | nargs=3 | — | — | Rotation X Y Z (euler). |
| --params / -p | text | optional | nargs=1 | "&#123;&#125;" | — | Shape-specific parameters as JSON. Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder delete-faces`

Delete faces from a ProBuilder mesh. Examples: unity-mcp probuilder delete-faces "MyCube" --faces '[0,1]'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --faces | text | required | nargs=1 | — | — | Face indices as JSON array, e.g. '[0,1,2]'. Accepts @file or -/@- stdin. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder extrude-edges`

Extrude edges of a ProBuilder mesh. Edges can be specified as flat indices into the unique edge list, or as vertex pairs [&#123;a: 0, b: 1&#125;, ...]. Examples: unity-mcp probuilder extrude-edges "MyCube" --edges '[0,1]' --distance 0.5 unity-mcp probuilder extrude-edges "MyCube" --edges '[&#123;"a":0,"b":1&#125;]' --distance 1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --edges | text | required | nargs=1 | — | — | Edge indices as JSON array [0,1] or vertex pairs [&#123;"a":0,"b":1&#125;]. Accepts @file or -/@- stdin. |
| --distance / -d | float | optional | nargs=1 | 0.5 | — | Extrusion distance. |
| --as-group / --no-group | boolean | optional | nargs=1, flag | true | — | Extrude as group. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder extrude-faces`

Extrude faces of a ProBuilder mesh. Examples: unity-mcp probuilder extrude-faces "MyCube" --faces '[0]' --distance 1.0 unity-mcp probuilder extrude-faces "MyCube" --faces '[0,1,2]' --method IndividualFaces

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --faces | text | required | nargs=1 | — | — | Face indices as JSON array, e.g. '[0,1,2]'. Accepts @file or -/@- stdin. |
| --distance / -d | float | optional | nargs=1 | 0.5 | — | Extrusion distance. |
| --method | choice: FaceNormal, VertexNormal, IndividualFaces | optional | nargs=1 | "FaceNormal" | — | Extrusion method. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder freeze-transform`

Bake position/rotation/scale into vertex data, reset transform. Examples: unity-mcp probuilder freeze-transform "MyCube"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder info`

Get ProBuilder mesh info. Edge data now includes world-space vertex positions and uses deduplicated edges. Examples: unity-mcp probuilder info "MyCube" unity-mcp probuilder info "MyCube" --include faces unity-mcp probuilder info --search-method by_id --include all -- -12345

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --include | choice: summary, faces, edges, all | optional | nargs=1 | "summary" | — | Detail level: summary, faces, edges, or all. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder move-vertices`

Move vertices by an offset. Examples: unity-mcp probuilder move-vertices "MyCube" --vertices '[0,1,2,3]' --offset 0 1 0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --vertices | text | required | nargs=1 | — | — | Vertex indices as JSON array, e.g. '[0,1,2]'. Accepts @file or -/@- stdin. |
| --offset | float | required | nargs=3 | — | — | Offset X Y Z. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder raw`

Execute any ProBuilder action directly. Actions include: create_shape, create_poly_shape, extrude_faces, extrude_edges, bevel_edges, subdivide, delete_faces, bridge_edges, connect_elements, detach_faces, flip_normals, merge_faces, combine_meshes, merge_objects, duplicate_and_flip, create_polygon, merge_vertices, weld_vertices, split_vertices, move_vertices, insert_vertex, append_vertices_to_edge, select_faces, set_face_material, set_face_color, set_face_uvs, get_mesh_info, convert_to_probuilder, set_smoothing, auto_smooth, center_pivot, set_pivot, freeze_transform, validate_mesh, repair_mesh Examples: unity-mcp probuilder raw extrude_faces "MyCube" --params '&#123;"faceIndices": [0], "distance": 1.0&#125;' unity-mcp probuilder raw bevel_edges "MyCube" --params '&#123;"edges": [&#123;"a":0,"b":1&#125;], "amount": 0.2&#125;' unity-mcp probuilder raw detach_faces "MyCube" --params '&#123;"faceIndices": [0], "deleteSourceFaces": true&#125;' unity-mcp probuilder raw weld_vertices "MyCube" --params '&#123;"vertexIndices": [0,1,2], "radius": 0.1&#125;' unity-mcp probuilder raw select_faces "MyCube" --params '&#123;"direction": "up", "tolerance": 0.9&#125;' unity-mcp probuilder raw insert_vertex "MyCube" --params '&#123;"edge": &#123;"a":0,"b":1&#125;, "point": [0.5,0,0]&#125;' unity-mcp probuilder raw set_pivot "MyCube" --params '&#123;"position": [0, 0, 0]&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| action | text | required | nargs=1 | — | — |  |
| target | text | optional | nargs=1 | — | — |  |
| --params / -p | text | optional | nargs=1 | "&#123;&#125;" | — | Additional parameters as JSON. Accepts @file or -/@- stdin. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder repair`

Auto-fix degenerate triangles and unused vertices. Examples: unity-mcp probuilder repair "MyCube"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder select-faces`

Select faces by criteria (direction, grow, flood, loop). Examples: unity-mcp probuilder select-faces "MyCube" --direction up unity-mcp probuilder select-faces "MyCube" --direction forward --tolerance 0.9 unity-mcp probuilder select-faces "MyCube" --grow-from '[0]' --grow-angle 45

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --direction | choice: up, down, forward, back, left, right | optional | nargs=1 | — | — | Select faces by normal direction. |
| --tolerance | float | optional | nargs=1 | 0.7 | — | Dot product tolerance for direction (0-1). |
| --grow-from | text | optional | nargs=1 | — | — | Face indices to grow selection from (JSON array). Accepts @file or -/@- stdin. |
| --grow-angle | float | optional | nargs=1 | -1 | — | Max angle for grow selection (-1=any). |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder set-material`

Assign a material to specific faces. Examples: unity-mcp probuilder set-material "MyCube" --faces '[0,1]' --material "Assets/Materials/Red.mat"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --faces | text | required | nargs=1 | — | — | Face indices as JSON array. Accepts @file or -/@- stdin. |
| --material / -m | text | required | nargs=1 | — | — | Material asset path. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder set-pivot`

Set pivot to an arbitrary world position. Examples: unity-mcp probuilder set-pivot "MyCube" --position 0 0 0 unity-mcp probuilder set-pivot "MyCube" --position 1.5 0 2.3

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --position | float | required | nargs=3 | — | — | World position X Y Z. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder set-smoothing`

Set smoothing group on specific faces. Examples: unity-mcp probuilder set-smoothing "MyCube" --faces '[0,1,2]' --group 1 unity-mcp probuilder set-smoothing "MyCube" --faces '[3,4,5]' --group 0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --faces | text | required | nargs=1 | — | — | Face indices as JSON array, e.g. '[0,1,2]'. Accepts @file or -/@- stdin. |
| --group | integer | required | nargs=1 | — | — | Smoothing group (0=hard, 1+=smooth). |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder subdivide`

Subdivide faces of a ProBuilder mesh. Examples: unity-mcp probuilder subdivide "MyCube" unity-mcp probuilder subdivide "MyCube" --faces '[0,1]'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --faces | text | optional | nargs=1 | — | — | Face indices as JSON array (optional, subdivides all if omitted). Accepts @file or -/@- stdin. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder validate`

Check mesh health (degenerate triangles, unused vertices). Examples: unity-mcp probuilder validate "MyCube"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp probuilder weld-vertices`

Weld vertices within a proximity radius. Examples: unity-mcp probuilder weld-vertices "MyCube" --vertices '[0,1,2,3]' --radius 0.1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --vertices | text | required | nargs=1 | — | — | Vertex indices as JSON array. Accepts @file or -/@- stdin. |
| --radius / -r | float | optional | nargs=1 | 0.01 | — | Neighbor radius for welding. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler`

Unity Profiler session control, counter reads, memory snapshots, and Frame Debugger.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler frame-debugger-disable`

Disable the Frame Debugger.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler frame-debugger-enable`

Enable the Frame Debugger and report event count.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler frame-debugger-events`

Get Frame Debugger draw call events (paged).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --page-size | integer | optional | nargs=1 | 50 | — | Events per page (default 50, clamped to 1..500). |
| --cursor | integer | optional | nargs=1 | — | — | Cursor offset (negative values treated as 0). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler frame-timing`

Get frame timing via FrameTimingManager (12 fields, synchronous).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler get-counters`

Read profiler counters by category (async, 1-frame wait).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --category | text | required | nargs=1 | — | — | Profiler category (e.g. Render, Scripts, Memory). |
| --counter | text | optional | nargs=1, repeatable | — | — | Specific counter names. Omit to read all in category. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler memory-compare`

Compare two memory snapshots.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --a | text | required | nargs=1 | — | — | First snapshot path. |
| --b | text | required | nargs=1 | — | — | Second snapshot path. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler memory-list`

List available memory snapshot files.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --search-path | text | optional | nargs=1 | — | — | Directory to search for snapshots. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler memory-snapshot`

Take a memory snapshot (requires com.unity.memoryprofiler).

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --path | text | optional | nargs=1 | — | — | Output .snap file path (default: auto-generated). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler object-memory`

Get native memory size of a specific Unity object.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --path | text | required | nargs=1 | — | — | Scene hierarchy or asset path. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler set-areas`

Toggle specific ProfilerAreas on or off.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --area | text | optional | nargs=1, repeatable | — | — | Area=bool pairs (e.g. CPU=true Audio=false). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler start`

Start the Unity Profiler, optionally record to a .raw file.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --log-file | text | optional | nargs=1 | — | — | Path to .raw file for recording. |
| --callstacks | boolean | optional | nargs=1, flag | false | — | Enable allocation callstacks. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler status`

Get Profiler enabled state, active areas, and recording status.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp profiler stop`

Stop the Unity Profiler and any active recording.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp raw`

Send a raw command to Unity. Examples: unity-mcp raw manage_scene '&#123;"action": "get_hierarchy"&#125;' unity-mcp raw read_console '&#123;"count": 10&#125;' JSON input also accepts @file or -/@- stdin.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| command_type | text | required | nargs=1 | — | — |  |
| params | text | optional | nargs=-1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp reflect`

Inspect Unity C# APIs via reflection.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp reflect member`

Get detailed info for a specific member. Examples: unity-mcp reflect member Physics Raycast unity-mcp reflect member NavMeshAgent SetDestination

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| class_name | text | required | nargs=1 | — | — |  |
| member_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp reflect search`

Search for Unity types by name. Examples: unity-mcp reflect search NavMesh unity-mcp reflect search Camera --scope all

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| query | text | required | nargs=1 | — | — |  |
| --scope / -s | choice: unity, packages, project, all | optional | nargs=1 | "unity" | — | Assembly scope to search. |
| --page-size | integer range: [1..100] | optional | nargs=1 | — | — | Page size (default 25). Repeat it with --cursor. |
| --cursor | text | optional | nargs=1 | — | — | Continuation token; repeat the same inputs and exact assembly spelling within five minutes. |
| --assembly | text | optional | nargs=1 | — | — | Case-insensitive simple/full assembly filter; repeat exact spelling with --cursor. |
| --type-kind | choice: class, interface, struct, enum, delegate | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp reflect type`

Get member summary for a Unity type. Examples: unity-mcp reflect type NavMeshAgent unity-mcp reflect type UnityEngine.Physics

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| class_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scenario-recording`

Record Play Mode Button interactions and observed scene changes into a draft.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scenario-recording cancel`

Stop observation and discard the session without saving.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| recording_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scenario-recording checkpoint`

Add an explicitly named future screenshot, with no image assertion.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| recording_id | text | required | nargs=1 | — | — |  |
| checkpoint_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scenario-recording start`

Observe supported Buttons in current Play Mode; return immediately.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --max-events | integer range: [1..31] | optional | nargs=1 | 31 | — |  |
| --duration-seconds | integer range: [1..600] | optional | nargs=1 | 300 | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scenario-recording status`

Read the current or last session once, including draft and diagnostics.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| recording_id | text | optional | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scenario-recording stop`

Stop and save a NEW scenario with draft=true; review before running.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| recording_id | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene`

Scene operations - hierarchy, load, save, create, multi-scene, validation.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene active`

Get information about the active scene.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene build-settings`

Get scenes in build settings.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene close`

Close/unload a loaded scene. Examples: unity-mcp scene close "Level2" unity-mcp scene close "Level2" --remove

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| scene_name | text | required | nargs=1 | — | — |  |
| --remove | boolean | optional | nargs=1, flag | — | — | Fully remove scene instead of just unloading. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene create`

Create a new scene, optionally from a template. Templates: empty - Empty scene, no default objects default - Camera + Directional Light (Unity default) 3d_basic - Default + ground plane 2d_basic - Default + orthographic camera Examples: unity-mcp scene create "NewLevel" unity-mcp scene create "Level1" --template 3d_basic unity-mcp scene create "Level1" --template 2d_basic --path "Assets/Scenes"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --path | text | optional | nargs=1 | — | — | Path to create the scene at. |
| --template / -t | choice: empty, default, 3d_basic, 2d_basic | optional | nargs=1 | — | — | Scene template (omit for empty scene). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene hierarchy`

Get the scene hierarchy. Examples: unity-mcp scene hierarchy unity-mcp scene hierarchy --max-depth 3 unity-mcp scene hierarchy --parent "Canvas" --include-transform unity-mcp --format json scene hierarchy

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --parent | text | optional | nargs=1 | — | — | Parent GameObject to list children of (name, path, or instance ID). |
| --max-depth / -d | integer | optional | nargs=1 | — | — | Maximum depth to traverse. |
| --include-transform / -t | boolean | optional | nargs=1, flag | — | — | Include transform data for each node. |
| --limit / -l | integer | optional | nargs=1 | 50 | — | Maximum nodes to return. |
| --cursor / -c | integer | optional | nargs=1 | 0 | — | Pagination cursor. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene load`

Load a scene. Examples: unity-mcp scene load "Assets/Scenes/Main.unity" unity-mcp scene load "MainScene" unity-mcp scene load 0 --by-index

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| scene | text | required | nargs=1 | — | — |  |
| --by-index / -i | boolean | optional | nargs=1, flag | — | — | Load by build index instead of path/name. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene loaded`

List all currently loaded scenes. Examples: unity-mcp scene loaded

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene move-to`

Move a root GameObject to another loaded scene. Examples: unity-mcp scene move-to "Player" "Level2"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| scene_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene open-additive`

Open a scene additively (keeps current scene loaded). Examples: unity-mcp scene open-additive "Assets/Scenes/Level2.unity"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| scene_path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene save`

Save the current scene. Examples: unity-mcp scene save unity-mcp scene save --path "Assets/Scenes/NewScene.unity"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --path | text | optional | nargs=1 | — | — | Path to save the scene to (for new scenes). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene set-active`

Set a loaded scene as the active scene. Examples: unity-mcp scene set-active "Level2"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| scene_name | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene validate`

Validate the active scene for issues (missing scripts, broken prefabs). Examples: unity-mcp scene validate unity-mcp scene validate --repair

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --repair | boolean | optional | nargs=1, flag | — | — | Auto-fix missing scripts (undoable). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene-edit`

Preview and apply allowlisted scene edits with one named Undo group.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene-edit apply`

Apply exactly one of --preview-id or --edits-file.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --preview-id | text | optional | nargs=1 | — | — | Use a one-attempt token returned by preview. |
| --edits-file | file/path (lexical only) | optional | nargs=1 | — | — | Direct apply JSON; each entry requires expected_old. |
| --undo-name | text | optional | nargs=1 | "MCP Scene Edits" | — | Undo group label (1-80 characters). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp scene-edit preview`

Preview the JSON array in EDITS_FILE without mutating scene values.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| edits_file | file/path (lexical only) | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp script`

Script operations - create, read, edit C# scripts.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp script create`

Create a new C# script. Examples: unity-mcp script create "PlayerController" unity-mcp script create "GameManager" --path "Assets/Scripts/Managers" unity-mcp script create "EnemyData" --type ScriptableObject unity-mcp script create "CustomEditor" --type Editor --namespace "MyGame.Editor"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --path / -p | text | optional | nargs=1 | "Assets/Scripts" | — | Directory to create the script in. |
| --type / -t | choice: MonoBehaviour, ScriptableObject, Editor, EditorWindow, Plain | optional | nargs=1 | "MonoBehaviour" | — | Type of script to create. |
| --namespace / -n | text | optional | nargs=1 | — | — | Namespace for the script. |
| --contents / -c | text | optional | nargs=1 | — | — | Full script contents (overrides template). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp script delete`

Delete a C# script. Examples: unity-mcp script delete "Assets/Scripts/OldScript.cs"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --force / -f | boolean | optional | nargs=1, flag | — | — | Skip confirmation prompt. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp script edit`

Apply text edits to a script. Examples: unity-mcp script edit "Assets/Scripts/Player.cs" --edits '[&#123;"startLine": 10, "startCol": 1, "endLine": 10, "endCol": 20, "newText": "// Modified"&#125;]'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --edits / -e | text | required | nargs=1 | — | — | Edits as JSON array of &#123;startLine, startCol, endLine, endCol, newText&#125;. Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp script read`

Read a C# script file. Examples: unity-mcp script read "Assets/Scripts/Player.cs" unity-mcp script read "Assets/Scripts/Player.cs" --start-line 10 --line-count 20

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --start-line / -s | integer range: [1..None] | optional | nargs=1 | — | — | Starting line number (1-based). |
| --line-count / -n | integer range: [1..None] | optional | nargs=1 | — | — | Number of lines to read. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp script validate`

Validate a C# script for errors. Examples: unity-mcp script validate "Assets/Scripts/Player.cs" unity-mcp script validate "Assets/Scripts/Player.cs" --level standard

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --level / -l | choice: basic, standard | optional | nargs=1 | "basic" | — | Validation level. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp shader`

Shader operations - create, read, update, delete shaders.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp shader create`

Create a new shader. Examples: unity-mcp shader create "MyShader" --path "Assets/Shaders" unity-mcp shader create "MyShader" --file local_shader.shader echo "Shader code..." \| unity-mcp shader create "MyShader"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --path / -p | text | optional | nargs=1 | "Assets/Shaders" | — | Directory to create shader in. |
| --contents / -c | text | optional | nargs=1 | — | — | Shader code (reads from stdin if not provided). |
| --file / -f | file/path (lexical only) | optional | nargs=1 | — | — | Read shader code from file. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp shader delete`

Delete a shader. Examples: unity-mcp shader delete "Assets/Shaders/OldShader.shader" unity-mcp shader delete "Assets/Shaders/OldShader.shader" --force

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --force / -f | boolean | optional | nargs=1, flag | — | — | Skip confirmation prompt. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp shader read`

Read a shader file. Examples: unity-mcp shader read "Assets/Shaders/MyShader.shader"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp shader update`

Update an existing shader. Examples: unity-mcp shader update "Assets/Shaders/MyShader.shader" --file updated.shader echo "New shader code" \| unity-mcp shader update "Assets/Shaders/MyShader.shader"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --contents / -c | text | optional | nargs=1 | — | — | New shader code. |
| --file / -f | file/path (lexical only) | optional | nargs=1 | — | — | Read shader code from file. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp sprite`

Sprite sheet operations - slice sheets, build clips and Animator controllers.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp sprite full-setup`

Slice a sheet, then build its clips and controller in one step. Examples: unity-mcp sprite full-setup Assets/Sprites/coin.png --cols 8 \ --clips '[&#123;"name": "spin", "start_frame": 0, "end_frame": 7, "loop": true&#125;]' unity-mcp sprite full-setup Assets/Sprites/hero.png --cols 6 --rows 4 \ --clips '[&#123;"name": "idle", "start_frame": 0, "end_frame": 5&#125;, &#123;"name": "walk", "start_frame": 6, "end_frame": 11&#125;]' \ --controller-path Assets/Animators/Hero.controller --add-to-scene --scene-target Hero

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --cols | integer | optional | nargs=1 | — | — | Columns in the grid. |
| --rows | integer | optional | nargs=1 | — | — | Rows in the grid (default 1, or from --frame-height). |
| --frame-width | integer | optional | nargs=1 | — | — | Frame width in pixels; alternative to --cols. |
| --frame-height | integer | optional | nargs=1 | — | — | Frame height in pixels; alternative to --rows. |
| --base-name | text | optional | nargs=1 | — | — | Base name for the frames (default: texture file name). |
| --filter-mode | choice: point, bilinear, trilinear | optional | nargs=1 | — | — | Texture filter for the sheet (default point, for pixel art). |
| --clips | text | optional | nargs=1 | — | — | JSON list: [&#123;"name","start_frame","end_frame","fps","loop"&#125;]. Accepts @file or -/@- stdin. |
| --animation-name | text | optional | nargs=1 | — | — | Name of the one clip made from all frames when --clips is not given (default: the sheet's file name; 12 fps; loops only for idle, walk or run-type names). |
| --output-dir | text | optional | nargs=1 | — | — | Folder for the .anim assets and, without --controller-path, the controller (default: the sheet's folder). |
| --controller-path | text | optional | nargs=1 | — | — | Path for the .controller asset (default: &lt;output-dir&gt;/&lt;sheet&gt;_Controller.controller). |
| --overwrite | boolean | optional | nargs=1, flag | — | — | Replace .anim and .controller assets that already exist (the sheet is re-sliced either way). |
| --add-to-scene | boolean | optional | nargs=1, flag | — | — | Give --scene-target an Animator with the controller, and a SpriteRenderer if it has none. |
| --scene-target | text | optional | nargs=1 | — | — | Name of exactly one existing GameObject (inactive ones count) for --add-to-scene. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp sprite info`

Show a sheet's import settings, size and slices. The tool also sends the sheet as a base64 image for a model to look at. The CLI leaves it out: the image is the file at PATH. Examples: unity-mcp sprite info Assets/Sprites/hero.png unity-mcp sprite info Assets/Sprites/atlas.png --cursor 512

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --page-size | integer | optional | nargs=1 | — | — | Slices to list (1-4096, default 512). |
| --cursor | integer | optional | nargs=1 | — | — | Slice index to start at; pass back next_cursor. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp sprite setup-clips`

Create AnimationClips from a sliced sheet. Examples: unity-mcp sprite setup-clips Assets/Sprites/hero.png \ --clips '[&#123;"name": "walk", "start_frame": 0, "end_frame": 5&#125;]'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --clips | text | required | nargs=1 | — | — | JSON list: [&#123;"name","start_frame","end_frame","fps","loop"&#125;]. Accepts @file or -/@- stdin. |
| --output-dir | text | optional | nargs=1 | — | — | Folder for the .anim assets (default: the sheet's folder). |
| --overwrite | boolean | optional | nargs=1, flag | — | — | Replace .anim assets that already exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp sprite setup-controller`

Build an AnimatorController from existing clips. Examples: unity-mcp sprite setup-controller Assets/Animators/Hero.controller \ --clips '[&#123;"name": "idle", "path": "Assets/Sprites/idle.anim"&#125;]'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| controller_path | text | required | nargs=1 | — | — |  |
| --clips | text | required | nargs=1 | — | — | JSON list: [&#123;"name", "path"&#125;], where path is the .anim asset. Accepts @file or -/@- stdin. |
| --overwrite | boolean | optional | nargs=1, flag | — | — | Replace the .controller if it already exists. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp sprite slice`

Slice a sprite sheet into a grid of frames. Replaces the sheet's existing slices and sets it to Sprite (Multiple) import, with NPOT scaling off and the --filter-mode filter. Examples: unity-mcp sprite slice Assets/Sprites/hero.png --cols 6 --rows 4 unity-mcp sprite slice Assets/Sprites/hero.png --frame-width 32 --frame-height 32

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --cols | integer | optional | nargs=1 | — | — | Columns in the grid. |
| --rows | integer | optional | nargs=1 | — | — | Rows in the grid (default 1, or from --frame-height). |
| --frame-width | integer | optional | nargs=1 | — | — | Frame width in pixels; alternative to --cols. |
| --frame-height | integer | optional | nargs=1 | — | — | Frame height in pixels; alternative to --rows. |
| --base-name | text | optional | nargs=1 | — | — | Base name for the frames (default: texture file name). |
| --filter-mode | choice: point, bilinear, trilinear | optional | nargs=1 | — | — | Texture filter for the sheet (default point, for pixel art). |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp status`

Check connection status to Unity MCP server.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp texture`

Texture operations - create, modify, generate sprites.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp texture create`

Create a new procedural texture. Examples: unity-mcp texture create Assets/Red.png --color '[255,0,0]' unity-mcp texture create Assets/Check.png --pattern checkerboard unity-mcp texture create Assets/UI.png --import-settings '&#123;"texture_type": "sprite"&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --width | integer | optional | nargs=1 | 64 | — | Texture width (default: 64) |
| --height | integer | optional | nargs=1 | 64 | — | Texture height (default: 64) |
| --image-path | text | optional | nargs=1 | — | — | Source image path (PNG/JPG) to import. |
| --color | text | optional | nargs=1 | — | — | Fill color (e.g., '#FF0000' or '[1,0,0,1]') |
| --pattern | choice: checkerboard, stripes, stripes_h, stripes_v, stripes_diag, dots, grid, brick | optional | nargs=1 | — | — | Pattern type |
| --palette | text | optional | nargs=1 | — | — | Color palette for pattern (JSON array of colors) Accepts @file or -/@- stdin. |
| --import-settings | text | optional | nargs=1 | — | — | TextureImporter settings (JSON) Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp texture delete`

Delete a texture. Examples: unity-mcp texture delete "Assets/Textures/Old.png" unity-mcp texture delete "Assets/Textures/Old.png" --force

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --force / -f | boolean | optional | nargs=1, flag | — | — | Skip confirmation prompt. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp texture modify`

Modify an existing texture. Examples: unity-mcp texture modify Assets/Tex.png --set-pixels '&#123;"x":0,"y":0,"width":10,"height":10,"color":[255,0,0]&#125;' unity-mcp texture modify Assets/Tex.png --set-pixels '&#123;"x":0,"y":0,"width":2,"height":2,"pixels":[[255,0,0,255],[0,255,0,255],[0,0,255,255],[255,255,0,255]]&#125;' unity-mcp texture modify Assets/UI/icon.png --as-sprite unity-mcp texture modify Assets/UI/bg.png --texture-type sprite --max-size 2048

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --set-pixels | text | optional | nargs=1 | — | — | Modification args as JSON Accepts @file or -/@- stdin. |
| --texture-type | choice: default, normal_map, editor_gui, sprite, cursor, cookie, lightmap, directional_lightmap, shadow_mask, single_channel | optional | nargs=1 | — | — | Texture type |
| --sprite-mode | choice: single, multiple, polygon | optional | nargs=1 | — | — | Sprite import mode |
| --sprite-ppu | float | optional | nargs=1 | — | — | Sprite pixels per unit |
| --max-size | choice: 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384 | optional | nargs=1 | — | — | Max texture size |
| --compression | choice: none, low_quality, normal_quality, high_quality | optional | nargs=1 | — | — | Compression quality |
| --generate-mipmaps / --no-mipmaps | boolean | optional | nargs=1, flag | — | — | Generate mipmaps |
| --srgb / --linear | boolean | optional | nargs=1, flag | — | — | sRGB color texture |
| --readable / --no-readable | boolean | optional | nargs=1, flag | — | — | Read/Write enabled |
| --as-sprite | boolean | optional | nargs=1, flag | — | — | Shorthand: set texture type to Sprite with defaults |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp texture set-import-settings`

Change import settings on an existing texture. Examples: unity-mcp texture set-import-settings Assets/UI/icon.png --texture-type sprite unity-mcp texture set-import-settings Assets/UI/icon.png --as-sprite unity-mcp texture set-import-settings Assets/UI/icon.png --texture-type sprite --sprite-mode single --sprite-ppu 100 unity-mcp texture set-import-settings Assets/UI/bg.png --max-size 2048 --compression high_quality

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --texture-type | choice: default, normal_map, editor_gui, sprite, cursor, cookie, lightmap, directional_lightmap, shadow_mask, single_channel | optional | nargs=1 | — | — | Texture type |
| --sprite-mode | choice: single, multiple, polygon | optional | nargs=1 | — | — | Sprite import mode |
| --sprite-ppu | float | optional | nargs=1 | — | — | Sprite pixels per unit |
| --max-size | choice: 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384 | optional | nargs=1 | — | — | Max texture size |
| --compression | choice: none, low_quality, normal_quality, high_quality | optional | nargs=1 | — | — | Compression quality |
| --generate-mipmaps / --no-mipmaps | boolean | optional | nargs=1, flag | — | — | Generate mipmaps |
| --srgb / --linear | boolean | optional | nargs=1, flag | — | — | sRGB color texture |
| --readable / --no-readable | boolean | optional | nargs=1, flag | — | — | Read/Write enabled |
| --as-sprite | boolean | optional | nargs=1, flag | — | — | Shorthand: set texture type to Sprite with defaults |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp texture sprite`

Quickly create a sprite texture. Examples: unity-mcp texture sprite Assets/Sprites/Player.png unity-mcp texture sprite Assets/Sprites/Coin.png --pattern dots unity-mcp texture sprite Assets/Sprites/Solid.png --color '[0,255,0]'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| path | text | required | nargs=1 | — | — |  |
| --width | integer | optional | nargs=1 | 64 | — | Texture width (default: 64) |
| --height | integer | optional | nargs=1 | 64 | — | Texture height (default: 64) |
| --image-path | text | optional | nargs=1 | — | — | Source image path (PNG/JPG) to import. |
| --color | text | optional | nargs=1 | — | — | Fill color (e.g., '#FF0000' or '[1,0,0,1]') |
| --pattern | choice: checkerboard, stripes, dots, grid | optional | nargs=1 | — | — | Pattern type (defaults to checkerboard if no color specified) |
| --ppu | float | optional | nargs=1 | 100.0 | — | Pixels Per Unit |
| --pivot | text | optional | nargs=1 | — | — | Pivot as [x,y] (default: [0.5, 0.5]) Accepts @file or -/@- stdin. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp tool`

List project tools or inspect a named server-advertised tool.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp tool describe`

Describe NAME for this CLI connection; hidden-tool causes may be unknown.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --protocol | choice: auto, legacy | optional | nargs=1 | "auto" | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp tool list`

List custom tools registered for the active Unity project.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp tool schema`

Print NAME's complete schema receipt as JSON, regardless of --format. Project-declared metadata is distinct from an advertised callable schema.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --protocol | choice: auto, legacy | optional | nargs=1 | "auto" | — |  |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp ui`

UI operations - create and modify UI elements.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp ui create-button`

Create a UI Button. Examples: unity-mcp ui create-button "StartButton" --parent "MainUI" --text "Start Game"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --parent / -p | text | required | nargs=1 | — | — | Parent Canvas or UI element. |
| --text / -t | text | optional | nargs=1 | "Button" | — | Button label text. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp ui create-canvas`

Create a new Canvas. Examples: unity-mcp ui create-canvas "MainUI" unity-mcp ui create-canvas "WorldUI" --render-mode WorldSpace

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --render-mode | choice: ScreenSpaceOverlay, ScreenSpaceCamera, WorldSpace | optional | nargs=1 | "ScreenSpaceOverlay" | — | Canvas render mode. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp ui create-image`

Create a UI Image. Examples: unity-mcp ui create-image "Background" --parent "MainUI" unity-mcp ui create-image "Icon" --parent "MainUI" --sprite "Assets/Sprites/icon.png"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --parent / -p | text | required | nargs=1 | — | — | Parent Canvas or UI element. |
| --sprite / -s | text | optional | nargs=1 | — | — | Sprite asset path. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp ui create-text`

Create a UI Text element (TextMeshPro). Examples: unity-mcp ui create-text "TitleText" --parent "MainUI" --text "Hello World"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| name | text | required | nargs=1 | — | — |  |
| --parent / -p | text | required | nargs=1 | — | — | Parent Canvas or UI element. |
| --text / -t | text | optional | nargs=1 | "New Text" | — | Initial text content. |
| --position | float | optional | nargs=2 | — | — | Anchored position X Y. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx`

VFX operations - particle systems, line renderers, trails.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx line`

Line renderer operations.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx line clear`

Clear all positions from a line renderer.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple LineRenderers exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx line create-circle`

Create a circle shape. Examples: unity-mcp vfx line create-circle "Circle" --radius 5 --segments 64 unity-mcp vfx line create-circle "Ring" --center 0 2 0 --radius 3

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --center | float | optional | nargs=3 | [0, 0, 0] | — | Center point X Y Z |
| --radius | float | required | nargs=1 | — | — | Circle radius |
| --segments | integer | optional | nargs=1 | 32 | — | Number of segments |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple LineRenderers exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx line create-line`

Create a simple line between two points. Examples: unity-mcp vfx line create-line "MyLine" --start 0 0 0 --end 10 5 0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --start | float | required | nargs=3 | — | — | Start point X Y Z |
| --end | float | required | nargs=3 | — | — | End point X Y Z |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple LineRenderers exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx line info`

Get line renderer info. Examples: unity-mcp vfx line info "LaserBeam" unity-mcp vfx line info "MultiLine" --component-index 1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple LineRenderers exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx line set-positions`

Set all positions on a line renderer. Examples: unity-mcp vfx line set-positions "Line" --positions "[[0,0,0], [5,2,0], [10,0,0]]"

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --positions / -p | text | required | nargs=1 | — | — | Positions as JSON array: [[0,0,0], [1,1,1], [2,0,0]] Accepts @file or -/@- stdin. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple LineRenderers exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx particle`

Particle system operations.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx particle clear`

Clear all particles from a particle system.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --with-children | boolean | optional | nargs=1, flag | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple ParticleSystems exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx particle info`

Get particle system info. Examples: unity-mcp vfx particle info "Fire" unity-mcp vfx particle info --search-method by_id -- -12345 unity-mcp vfx particle info "Effects" --component-index 1

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple ParticleSystems exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx particle pause`

Pause a particle system.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple ParticleSystems exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx particle play`

Play a particle system. Examples: unity-mcp vfx particle play "Fire" unity-mcp vfx particle play "Effects" --with-children

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --with-children | boolean | optional | nargs=1, flag | — | — | Also play child particle systems. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple ParticleSystems exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx particle restart`

Restart a particle system.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --with-children | boolean | optional | nargs=1, flag | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple ParticleSystems exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx particle stop`

Stop a particle system.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --with-children | boolean | optional | nargs=1, flag | — | — | Also stop child particle systems. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple ParticleSystems exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx raw`

Execute any VFX action directly. For advanced users who need access to all 60+ VFX actions. Actions include: particle_*: particle_set_main, particle_set_emission, particle_set_shape, ... vfx_*: vfx_set_float, vfx_send_event, vfx_play, ... line_*: line_create_arc, line_create_bezier, ... trail_*: trail_set_width, trail_set_color, ... Examples: unity-mcp vfx raw particle_set_main "Fire" --params '&#123;"duration": 5, "looping": true&#125;' unity-mcp vfx raw line_create_arc "Arc" --params '&#123;"radius": 3, "startAngle": 0, "endAngle": 180&#125;' unity-mcp vfx raw vfx_send_event "Explosion" --params '&#123;"eventName": "OnSpawn"&#125;'

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| action | text | required | nargs=1 | — | — |  |
| target | text | optional | nargs=1 | — | — |  |
| --params / -p | text | optional | nargs=1 | "&#123;&#125;" | — | Additional parameters as JSON. Accepts @file or -/@- stdin. |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple components of the same type exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx trail`

Trail renderer operations.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx trail clear`

Clear a trail renderer.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple TrailRenderers exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx trail info`

Get trail renderer info.

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple TrailRenderers exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

## `unity-mcp vfx trail set-time`

Set trail duration. Examples: unity-mcp vfx trail set-time "PlayerTrail" 2.0

| Argument / option | Type | Required | Arity / flags | Default | Environment | Description |
| --- | --- | --- | --- | --- | --- | --- |
| target | text | required | nargs=1 | — | — |  |
| duration | float | required | nargs=1 | — | — |  |
| --search-method | choice: by_name, by_path, by_id, by_tag, by_layer | optional | nargs=1 | — | — |  |
| --component-index / -i | integer | optional | nargs=1 | — | — | Zero-based index when multiple TrailRenderers exist. |
| --help | boolean | optional | nargs=1, flag | — | — | Show this message and exit. |

