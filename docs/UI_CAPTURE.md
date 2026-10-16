# UI capture request ownership

`manage_ui(action="render_ui", ...)` captures the whole composited Game view in
Play mode, including UI Toolkit overlays. Its target identifies the originating
request and response metadata; it does not crop the image to that target.

Start a capture without `capture_id`, then retain the returned ID:

```python
started = await manage_ui(
    ctx=ctx,
    action="render_ui",
    target="UIRoot",
    screenshot_file_name="ui-preview.png",
    output_folder="Captures",
    include_image=True,
)
capture_id = started["data"]["capture_id"]

# Repeat this call while data.pending is true. The successful completed response
# contains the PNG path and uses the settings from the start request.
result = await manage_ui(ctx=ctx, action="render_ui", capture_id=capture_id)
```

The pending response describes how to poll and cancel. To discard an owned
capture, call:

```python
cancelled = await manage_ui(
    ctx=ctx,
    action="render_ui",
    capture_id=capture_id,
    cancel_capture=True,
)
```

Only one Play-mode request or completed result is retained at a time. A call
without `capture_id` always represents a new request, even when its settings are
identical to another request. It cannot retrieve, cancel or replace a live
owner's result. After retrieval, cancellation or expiry, a new start receives a
new ID. IDs become invalid on Play-mode transitions, assembly reload and editor
shutdown.

An ID-only poll uses the original target, component selection, output path,
dimensions and inline-image settings. If a caller repeats any of these settings,
they must match the originating request. The output path is selected once at
start, including automatic name generation and existing-file collision handling.
A later change to the screenshot folder preference cannot redirect the result.

The pending capture and completed result each have a 30-second retention limit.
The screenshot coroutine also has its existing short timeout for a paused or
unfocused Game view. Expiry and cancellation release the owned texture and
invalidate late callbacks; a stale or foreign ID cannot release a newer result.

## Migration from implicit Play-mode polling

Older callers invoked `render_ui` a second time with the same target and output
arguments to consume a global pending result. Update that second call to echo
`data.capture_id` from the start response. Settings alone cannot distinguish an
owner polling its result from an independent caller requesting the same image.
ID-less calls now receive an explicit busy/result-pending error while another
request owns the slot.

Edit-mode rendering retains its synchronous/best-effort workflow and does not
require a capture ID. The existing Edit-mode RenderTexture warm-up retry remains
separate from Play-mode ID polling.
