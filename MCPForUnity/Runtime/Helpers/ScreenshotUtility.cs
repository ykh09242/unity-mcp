using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Experimental.Rendering;

namespace MCPForUnity.Runtime.Helpers
//The reason for having another Runtime Utilities in additional to Editor Utilities is to avoid Editor-only dependencies in this runtime code.
{
    public readonly struct ScreenshotCaptureResult
    {
        public ScreenshotCaptureResult(string fullPath, string projectRelativePath, int superSize)
            : this(fullPath, projectRelativePath, superSize, isAsync: false, imageBase64: null, imageWidth: 0, imageHeight: 0) { }

        public ScreenshotCaptureResult(string fullPath, string projectRelativePath, int superSize, bool isAsync)
            : this(fullPath, projectRelativePath, superSize, isAsync, imageBase64: null, imageWidth: 0, imageHeight: 0) { }

        public ScreenshotCaptureResult(
            string fullPath,
            string projectRelativePath,
            int superSize,
            bool isAsync,
            string imageBase64,
            int imageWidth,
            int imageHeight
        )
            : this(fullPath, projectRelativePath, superSize, isAsync, imageBase64, imageWidth, imageHeight, fallbackReason: null, fallbackCameraName: null) { }

        public ScreenshotCaptureResult(
            string fullPath,
            string projectRelativePath,
            int superSize,
            bool isAsync,
            string imageBase64,
            int imageWidth,
            int imageHeight,
            string fallbackReason,
            string fallbackCameraName
        )
        {
            FullPath = fullPath;
            ProjectRelativePath = projectRelativePath;
            SuperSize = superSize;
            IsAsync = isAsync;
            ImageBase64 = imageBase64;
            ImageWidth = imageWidth;
            ImageHeight = imageHeight;
            FallbackReason = fallbackReason;
            FallbackCameraName = fallbackCameraName;
        }

        public string FullPath { get; }

        /// <summary>Path relative to the Unity project root (e.g. "Captures/foo.png"). Suitable for ScreenCapture.CaptureScreenshot.</summary>
        public string ProjectRelativePath { get; }
        public int SuperSize { get; }
        public bool IsAsync { get; }

        /// <summary>Base64-encoded PNG image data. Only populated when include_image is true.</summary>
        public string ImageBase64 { get; }
        public int ImageWidth { get; }
        public int ImageHeight { get; }

        /// <summary>
        /// Set when a composited capture was replaced by a camera render, and says why. A camera
        /// render has no Screen Space - Overlay canvases or UI Toolkit panels. Null otherwise.
        /// </summary>
        public string FallbackReason { get; }

        /// <summary>The camera that rendered the image when <see cref="FallbackReason"/> is set; null otherwise.</summary>
        public string FallbackCameraName { get; }
    }

    public static class ScreenshotUtility
    {
        /// <summary>
        /// Built-in default folder for screenshots, project-relative.
        /// Callers can override per-call via the <c>folderOverride</c> parameter on capture methods,
        /// or globally via <c>ScreenshotPreferences</c> in the Editor assembly.
        /// </summary>
        public const string DefaultFolder = "Assets/Screenshots";
        private static readonly SemaphoreSlim CompositedCaptureGate = new SemaphoreSlim(1, 1);

        // Support 8K UHD captures, with independent batch work and retained-image limits.
        public const int MaxCaptureDimension = 8192;
        public const long MaxCapturePixels = 32L * 1024 * 1024;
        public const int MaxSuperSize = 4;
        public const int MaxBatchShots = 128;
        public const int MaxOrbitElevations = 16;
        public const long MaxBatchRenderedPixels = 256L * 1024 * 1024;
        public const long MaxRetainedTilePixels = 32L * 1024 * 1024;
        public const long MaxContactSheetPixels = 32L * 1024 * 1024;

        public static void ValidateFrameDimensions(int width, int height)
        {
            if (width <= 0 || height <= 0 || width > MaxCaptureDimension || height > MaxCaptureDimension || (long)width * height > MaxCapturePixels)
                throw new ArgumentException($"Capture dimensions must be positive, at most {MaxCaptureDimension} per side and {MaxCapturePixels} pixels.");
        }

        public static void ValidateSuperSize(int superSize)
        {
            if (superSize < 1 || superSize > MaxSuperSize)
                throw new ArgumentException($"superSize must be between 1 and {MaxSuperSize}.");
        }

        public static void ValidateMaxResolution(int maxResolution)
        {
            if (maxResolution < 0 || maxResolution > MaxCaptureDimension)
                throw new ArgumentException($"maxResolution must be between 0 (default) and {MaxCaptureDimension}.");
        }

        public static void ValidateCaptureDimensions(int width, int height, int superSize, out int captureWidth, out int captureHeight)
        {
            ValidateSuperSize(superSize);
            ValidateFrameDimensions(width, height);
            if (width > MaxCaptureDimension / superSize || height > MaxCaptureDimension / superSize)
                throw new ArgumentException("Supersampled capture exceeds the maximum dimensions.");
            captureWidth = width * superSize;
            captureHeight = height * superSize;
            ValidateFrameDimensions(captureWidth, captureHeight);
        }

        private static void GetTileDimensions(int width, int height, int maxResolution, out int tileWidth, out int tileHeight)
        {
            ValidateMaxResolution(maxResolution);
            int targetMax = maxResolution > 0 ? maxResolution : 640;
            float scale = Mathf.Min(1f, (float)targetMax / Mathf.Max(width, height));
            tileWidth = Mathf.Max(1, Mathf.RoundToInt(width * scale));
            tileHeight = Mathf.Max(1, Mathf.RoundToInt(height * scale));
        }

        public static void GetContactSheetDimensions(int tileWidth, int tileHeight, int count, int padding, out int sheetWidth, out int sheetHeight)
        {
            ValidateFrameDimensions(tileWidth, tileHeight);
            if (count < 1 || count > MaxBatchShots || padding < 0 || padding > MaxCaptureDimension)
                throw new ArgumentException("Contact sheet tile count or padding exceeds its budget.");
            if ((long)tileWidth * tileHeight > MaxRetainedTilePixels / count)
                throw new ArgumentException("Contact sheet tiles exceed the retained pixel budget.");
            int cols = Mathf.CeilToInt(Mathf.Sqrt(count));
            int rows = (count + cols - 1) / cols;
            int labelHeight = Mathf.Max(14, tileHeight / 12);
            long width = (long)cols * (tileWidth + padding) + padding;
            long height = (long)rows * (tileHeight + labelHeight + padding) + padding;
            if (width > MaxCaptureDimension || height > MaxCaptureDimension || width * height > MaxContactSheetPixels)
                throw new ArgumentException("Contact sheet dimensions or pixels exceed its budget.");
            sheetWidth = (int)width;
            sheetHeight = (int)height;
        }

        public static void ValidateBatchCapture(int width, int height, int shots, int maxResolution)
        {
            ValidateFrameDimensions(width, height);
            if (shots < 1 || shots > MaxBatchShots)
                throw new ArgumentException($"Batch capture must contain between 1 and {MaxBatchShots} shots.");
            if ((long)width * height > MaxBatchRenderedPixels / shots)
                throw new ArgumentException("Batch capture exceeds the aggregate rendered pixel budget.");
            GetTileDimensions(width, height, maxResolution, out int tileWidth, out int tileHeight);
            GetContactSheetDimensions(tileWidth, tileHeight, shots, 4, out _, out _);
        }

        public sealed class CaptureBatchBudget
        {
            private long renderedPixels,
                retainedPixels;
            private int shots;

            // Account actual per-frame dimensions in case a render callback changes the viewport.
            public void AdmitFrame(int width, int height, int tileWidth, int tileHeight)
            {
                ValidateFrameDimensions(width, height);
                ValidateFrameDimensions(tileWidth, tileHeight);
                long framePixels = (long)width * height,
                    tilePixels = (long)tileWidth * tileHeight;
                if (shots >= MaxBatchShots || framePixels > MaxBatchRenderedPixels - renderedPixels || tilePixels > MaxRetainedTilePixels - retainedPixels)
                    throw new ArgumentException("Batch capture exceeds its frame or aggregate pixel budget.");
                renderedPixels += framePixels;
                retainedPixels += tilePixels;
                shots++;
            }
        }

        public static void ReleaseCaptureTiles(IEnumerable<Texture2D> tiles)
        {
            if (tiles != null)
                foreach (var tile in tiles)
                    DestroyTexture(tile);
        }

        private static Camera FindAvailableCamera()
        {
            var main = Camera.main;
            if (main != null)
            {
                return main;
            }

            try
            {
                var cams = UnityFindObjectsCompat.FindAll<Camera>();
                return cams.FirstOrDefault();
            }
            catch
            {
                return null;
            }
        }

        public static ScreenshotCaptureResult CaptureToProjectFolder(
            string fileName = null,
            int superSize = 1,
            bool ensureUniqueFileName = true,
            string folderOverride = null
        )
        {
            ValidateCaptureDimensions(Mathf.Max(1, Screen.width), Mathf.Max(1, Screen.height), superSize, out _, out _);
            ScreenshotCaptureResult result = PrepareCaptureResult(fileName, superSize, ensureUniqueFileName, folderOverride, isAsync: true);
            // ScreenCapture.CaptureScreenshot accepts paths relative to the project root.
            using (var folders = new OutputFolderScope(GetProjectRootPath()))
            {
                folders.EnsureParentDirectory(result.FullPath);
                SafePathUtility.ResolveWithinRoot(GetProjectRootPath(), result.FullPath);
                ScreenCapture.CaptureScreenshot(result.ProjectRelativePath, result.SuperSize);
                folders.Complete();
            }
            return result;
        }

        /// <summary>
        /// Captures a screenshot from a specific camera by rendering into a temporary RenderTexture (works in Edit Mode).
        /// When <paramref name="includeImage"/> is true, the result includes a base64-encoded PNG (optionally
        /// downscaled so the longest edge is at most <paramref name="maxResolution"/>).
        /// </summary>
        public static ScreenshotCaptureResult CaptureFromCameraToProjectFolder(
            Camera camera,
            string fileName = null,
            int superSize = 1,
            bool ensureUniqueFileName = true,
            bool includeImage = false,
            int maxResolution = 0,
            string folderOverride = null
        )
        {
            if (camera == null)
            {
                throw new ArgumentNullException(nameof(camera));
            }

            int width = Mathf.Max(1, camera.pixelWidth > 0 ? camera.pixelWidth : Screen.width);
            int height = Mathf.Max(1, camera.pixelHeight > 0 ? camera.pixelHeight : Screen.height);
            ValidateCaptureDimensions(width, height, superSize, out width, out height);
            ValidateMaxResolution(maxResolution);
            ScreenshotCaptureResult result = PrepareCaptureResult(fileName, superSize, ensureUniqueFileName, folderOverride, isAsync: false);

            RenderTexture prevRT = camera.targetTexture;
            RenderTexture prevActive = RenderTexture.active;
            var rt = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32);
            Texture2D tex = null;
            Texture2D downscaled = null;
            string imageBase64 = null;
            int imgW = 0,
                imgH = 0;
            try
            {
                camera.targetTexture = rt;
                camera.Render();

                RenderTexture.active = rt;
                tex = new Texture2D(width, height, TextureFormat.RGBA32, false);
                tex.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                tex.Apply();

                byte[] png = tex.EncodeToPNG();
                WriteCaptureBytes(result.FullPath, png, ensureUniqueFileName);

                if (includeImage)
                {
                    int targetMax = maxResolution > 0 ? maxResolution : 640;
                    if (width > targetMax || height > targetMax)
                    {
                        downscaled = DownscaleTexture(tex, targetMax);
                        byte[] smallPng = downscaled.EncodeToPNG();
                        imageBase64 = System.Convert.ToBase64String(smallPng);
                        imgW = downscaled.width;
                        imgH = downscaled.height;
                    }
                    else
                    {
                        imageBase64 = System.Convert.ToBase64String(png);
                        imgW = width;
                        imgH = height;
                    }
                }
            }
            finally
            {
                camera.targetTexture = prevRT;
                RenderTexture.active = prevActive;
                RenderTexture.ReleaseTemporary(rt);
                DestroyTexture(tex);
                DestroyTexture(downscaled);
            }

            if (includeImage && imageBase64 != null)
            {
                return new ScreenshotCaptureResult(result.FullPath, result.ProjectRelativePath, result.SuperSize, false, imageBase64, imgW, imgH);
            }
            return result;
        }

        /// <summary>
        /// Play-mode composited capture that waits for end-of-frame without pumping
        /// <c>EditorApplication.Step</c>. MCP commands run inside
        /// <c>UnitySynchronizationContext.ExecuteTasks</c>, so a synchronous Step()
        /// re-enters the PlayerLoop and can flood Editor.log until the Editor dies.
        /// When no end of frame can arrive, this returns a camera render instead and sets
        /// <see cref="ScreenshotCaptureResult.FallbackReason"/>.
        /// </summary>
        public static async Task<ScreenshotCaptureResult> CaptureCompositedAsync(
            string fileName = null,
            int superSize = 1,
            bool ensureUniqueFileName = true,
            bool includeImage = false,
            int maxResolution = 0,
            string folderOverride = null
        )
        {
            ValidateCaptureDimensions(Mathf.Max(1, Screen.width), Mathf.Max(1, Screen.height), superSize, out _, out _);
            ValidateMaxResolution(maxResolution);

            // Batch mode renders no frames, so WaitForEndOfFrame never resumes there and every
            // capture would sit out the timeout before falling back.
            if (Application.isBatchMode)
            {
                return CaptureWithCameraInstead(
                    fileName,
                    superSize,
                    ensureUniqueFileName,
                    includeImage,
                    maxResolution,
                    folderOverride,
                    "Batch mode renders no frames"
                );
            }

            if (!await CompositedCaptureGate.WaitAsync(TimeSpan.FromSeconds(ScreenshotCapturer.DefaultTimeoutSeconds * 4)).ConfigureAwait(true))
            {
                throw new TimeoutException("Another composited screenshot capture is still in progress. Retry shortly.");
            }
            try
            {
                return await CaptureCompositedAsyncUngated(fileName, superSize, ensureUniqueFileName, includeImage, maxResolution, folderOverride)
                    .ConfigureAwait(true);
            }
            finally
            {
                CompositedCaptureGate.Release();
            }
        }

        private static Task<ScreenshotCaptureResult> CaptureCompositedAsyncUngated(
            string fileName,
            int superSize,
            bool ensureUniqueFileName,
            bool includeImage,
            int maxResolution,
            string folderOverride
        )
        {
            // Fail fast on a bad folder, but pick the file name only when the image is written:
            // a camera capture that runs during the wait could otherwise take the same unique
            // name, and this write would then replace that file.
            ResolveFolderAbsolute(folderOverride);
            var tcs = new TaskCompletionSource<ScreenshotCaptureResult>(TaskCreationOptions.RunContinuationsAsynchronously);

            ScreenshotCapturer.Begin(
                Mathf.Max(1, superSize),
                (tex, timedOut) =>
                {
                    Texture2D downscaled = null;
                    try
                    {
                        // Not an error: the caller still gets an image, and the result says it is a
                        // camera render rather than the composited Game view.
                        if (timedOut)
                        {
                            tcs.TrySetResult(
                                CaptureWithCameraInstead(
                                    fileName,
                                    superSize,
                                    ensureUniqueFileName,
                                    includeImage,
                                    maxResolution,
                                    folderOverride,
                                    $"No frame was rendered within {ScreenshotCapturer.DefaultTimeoutSeconds:0.#} s "
                                        + "(for example, the game is paused or the Editor is not rendering while unfocused)"
                                )
                            );
                            return;
                        }

                        if (tex == null)
                        {
                            tcs.TrySetResult(
                                CaptureWithCameraInstead(
                                    fileName,
                                    superSize,
                                    ensureUniqueFileName,
                                    includeImage,
                                    maxResolution,
                                    folderOverride,
                                    "ScreenCapture returned no image"
                                )
                            );
                            return;
                        }

                        var prepared = PrepareCaptureResult(fileName, superSize, ensureUniqueFileName, folderOverride: folderOverride, isAsync: false);
                        tcs.TrySetResult(EncodeAndSaveComposited(tex, prepared, includeImage, maxResolution, ensureUniqueFileName, ref downscaled));
                    }
                    catch (Exception ex)
                    {
                        tcs.TrySetException(ex);
                    }
                    finally
                    {
                        DestroyTexture(tex);
                        DestroyTexture(downscaled);
                    }
                }
            );

            return tcs.Task;
        }

        /// <summary>Renders a scene camera in place of a composited capture; <paramref name="cause"/> says why.</summary>
        private static ScreenshotCaptureResult CaptureWithCameraInstead(
            string fileName,
            int superSize,
            bool ensureUniqueFileName,
            bool includeImage,
            int maxResolution,
            string folderOverride,
            string cause
        )
        {
            var cam = FindAvailableCamera();
            if (cam == null)
                throw new InvalidOperationException(cause + ", and there is no camera to render instead.");

            var r = CaptureFromCameraToProjectFolder(
                cam,
                fileName,
                superSize,
                ensureUniqueFileName,
                includeImage,
                maxResolution,
                folderOverride: folderOverride
            );
            return new ScreenshotCaptureResult(
                r.FullPath,
                r.ProjectRelativePath,
                r.SuperSize,
                r.IsAsync,
                r.ImageBase64,
                r.ImageWidth,
                r.ImageHeight,
                $"{cause}, so this is a render of camera '{cam.name}'. A camera render does not show "
                    + "Screen Space - Overlay canvases or UI Toolkit panels.",
                cam.name
            );
        }

        private static ScreenshotCaptureResult EncodeAndSaveComposited(
            Texture2D tex,
            ScreenshotCaptureResult prepared,
            bool includeImage,
            int maxResolution,
            bool ensureUniqueFileName,
            ref Texture2D downscaled
        )
        {
            int width = tex.width;
            int height = tex.height;
            ValidateFrameDimensions(width, height);
            byte[] png = tex.EncodeToPNG();
            WriteCaptureBytes(prepared.FullPath, png, ensureUniqueFileName);

            if (!includeImage)
                return prepared;

            int targetMax = maxResolution > 0 ? maxResolution : 640;
            string imageBase64;
            int imgW;
            int imgH;
            if (width > targetMax || height > targetMax)
            {
                downscaled = DownscaleTexture(tex, targetMax);
                imageBase64 = Convert.ToBase64String(downscaled.EncodeToPNG());
                imgW = downscaled.width;
                imgH = downscaled.height;
            }
            else
            {
                imageBase64 = Convert.ToBase64String(png);
                imgW = width;
                imgH = height;
            }

            return new ScreenshotCaptureResult(prepared.FullPath, prepared.ProjectRelativePath, prepared.SuperSize, false, imageBase64, imgW, imgH);
        }

        /// <summary>
        /// Renders a camera to a Texture2D without saving to disk. Used for multi-angle captures.
        /// Returns the base64-encoded PNG, downscaled to fit within <paramref name="maxResolution"/>.
        /// </summary>
        public static (string base64, int width, int height) RenderCameraToBase64(Camera camera, int maxResolution = 640)
        {
            if (camera == null)
                throw new ArgumentNullException(nameof(camera));

            int width = Mathf.Max(1, camera.pixelWidth > 0 ? camera.pixelWidth : Screen.width);
            int height = Mathf.Max(1, camera.pixelHeight > 0 ? camera.pixelHeight : Screen.height);
            ValidateFrameDimensions(width, height);
            ValidateMaxResolution(maxResolution);

            RenderTexture prevRT = camera.targetTexture;
            RenderTexture prevActive = RenderTexture.active;
            var rt = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32);
            Texture2D tex = null;
            Texture2D downscaled = null;
            try
            {
                camera.targetTexture = rt;
                camera.Render();

                RenderTexture.active = rt;
                tex = new Texture2D(width, height, TextureFormat.RGBA32, false);
                tex.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                tex.Apply();

                int targetMax = maxResolution > 0 ? maxResolution : 640;
                if (width > targetMax || height > targetMax)
                {
                    downscaled = DownscaleTexture(tex, targetMax);
                    string b64 = System.Convert.ToBase64String(downscaled.EncodeToPNG());
                    return (b64, downscaled.width, downscaled.height);
                }
                else
                {
                    string b64 = System.Convert.ToBase64String(tex.EncodeToPNG());
                    return (b64, width, height);
                }
            }
            finally
            {
                camera.targetTexture = prevRT;
                RenderTexture.active = prevActive;
                RenderTexture.ReleaseTemporary(rt);
                DestroyTexture(tex);
                DestroyTexture(downscaled);
            }
        }

        /// <summary>
        /// Renders a camera to a Texture2D without saving to disk.
        /// Caller owns the returned texture and must destroy it.
        /// </summary>
        public static Texture2D RenderCameraToTexture(Camera camera, int maxResolution = 640, CaptureBatchBudget budget = null)
        {
            if (camera == null)
                throw new ArgumentNullException(nameof(camera));

            int width = Mathf.Max(1, camera.pixelWidth > 0 ? camera.pixelWidth : Screen.width);
            int height = Mathf.Max(1, camera.pixelHeight > 0 ? camera.pixelHeight : Screen.height);
            ValidateFrameDimensions(width, height);
            GetTileDimensions(width, height, maxResolution, out int tileWidth, out int tileHeight);
            budget?.AdmitFrame(width, height, tileWidth, tileHeight);

            RenderTexture prevRT = camera.targetTexture;
            RenderTexture prevActive = RenderTexture.active;
            var rt = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32);
            Texture2D tex = null;
            try
            {
                camera.targetTexture = rt;
                camera.Render();

                RenderTexture.active = rt;
                tex = new Texture2D(width, height, TextureFormat.RGBA32, false);
                tex.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                tex.Apply();

                int targetMax = maxResolution > 0 ? maxResolution : 640;
                if (width > targetMax || height > targetMax)
                {
                    var downscaled = DownscaleTexture(tex, targetMax);
                    DestroyTexture(tex);
                    tex = null;
                    return downscaled;
                }
                var result = tex;
                tex = null; // transfer ownership to caller
                return result;
            }
            finally
            {
                camera.targetTexture = prevRT;
                RenderTexture.active = prevActive;
                RenderTexture.ReleaseTemporary(rt);
                DestroyTexture(tex);
            }
        }

        /// <summary>
        /// Composites a list of tile textures into a single contact-sheet grid image.
        /// Labels are drawn as white text on a dark banner at the bottom of each tile.
        /// Returns base64 PNG plus dimensions. Destroys all input tile textures.
        /// </summary>
        public static (string base64, int width, int height) ComposeContactSheet(List<Texture2D> tiles, List<string> labels, int padding = 4)
        {
            Texture2D sheet = null;
            try
            {
                if (tiles == null || tiles.Count == 0 || tiles[0] == null)
                    throw new ArgumentException("No tiles to compose.", nameof(tiles));
                int tileW = tiles[0].width;
                int tileH = tiles[0].height;
                int count = tiles.Count;
                GetContactSheetDimensions(tileW, tileH, count, padding, out int sheetW, out int sheetH);
                foreach (var tile in tiles)
                    if (tile == null || tile.width != tileW || tile.height != tileH)
                        throw new ArgumentException("Contact sheet tiles must have matching dimensions.");

                int cols = Mathf.CeilToInt(Mathf.Sqrt(count));
                int rows = (count + cols - 1) / cols;
                int labelHeight = Mathf.Max(14, tileH / 12);
                int cellW = tileW + padding;
                int cellH = tileH + labelHeight + padding;
                sheet = new Texture2D(sheetW, sheetH, TextureFormat.RGBA32, false);

                // Build the full sheet in a Color32[] buffer, then upload once
                var bgColor = new Color32(30, 30, 30, 255);
                Color32[] sheetPixels = new Color32[sheetW * sheetH];
                for (int i = 0; i < sheetPixels.Length; i++)
                    sheetPixels[i] = bgColor;

                // Track label draw requests so we can apply them after the bulk upload
                var labelDraws = new List<(string text, int x, int y, int h)>();

                for (int idx = 0; idx < count; idx++)
                {
                    int col = idx % cols;
                    int row = idx / cols;
                    // Place tiles top-left to bottom-right (Unity Texture2D y=0 is bottom)
                    int x = padding + col * cellW;
                    int y = sheetH - padding - (row + 1) * cellH + padding;

                    // Copy tile pixels row-by-row using bulk operations
                    Color32[] tilePixels = tiles[idx].GetPixels32();
                    for (int ty = 0; ty < tileH; ty++)
                    {
                        int srcOffset = ty * tileW;
                        int dstOffset = (y + labelHeight + ty) * sheetW + x;
                        System.Array.Copy(tilePixels, srcOffset, sheetPixels, dstOffset, tileW);
                    }
                    DestroyTexture(tiles[idx]);
                    tiles[idx] = null; // release each retained native texture as soon as it is copied

                    // Draw label banner (dark background strip below tile)
                    var bannerColor = new Color32(20, 20, 20, 220);
                    for (int ly = 0; ly < labelHeight; ly++)
                    {
                        int dstOffset = (y + ly) * sheetW + x;
                        for (int lx = 0; lx < tileW; lx++)
                        {
                            sheetPixels[dstOffset + lx] = bannerColor;
                        }
                    }

                    // Queue label text drawing (applied after bulk pixel upload)
                    if (labels != null && idx < labels.Count && !string.IsNullOrEmpty(labels[idx]))
                    {
                        labelDraws.Add((labels[idx], x + 3, y + 2, labelHeight - 4));
                    }
                }

                // Upload all tile + banner pixels in one SetPixels32 call
                sheet.SetPixels32(sheetPixels);

                // Draw label text on top (small glyph-based writes, negligible cost)
                foreach (var (text, lx, ly, lh) in labelDraws)
                {
                    DrawText(sheet, text, lx, ly, lh, Color.white);
                }

                sheet.Apply();

                byte[] png = sheet.EncodeToPNG();
                string b64 = System.Convert.ToBase64String(png);
                return (b64, sheetW, sheetH);
            }
            finally
            {
                ReleaseCaptureTiles(tiles);
                DestroyTexture(sheet);
            }
        }

        private static void DrawText(Texture2D tex, string text, int startX, int startY, int charHeight, Color color)
        {
            // Simple 5x7 bitmap font for basic ASCII characters
            int charWidth = Mathf.Max(4, charHeight * 5 / 7);
            int spacing = Mathf.Max(1, charWidth / 5);
            int x = startX;

            foreach (char c in text)
            {
                if (x + charWidth > tex.width)
                    break;
                ulong glyph = GetGlyph(c);
                if (glyph != 0)
                {
                    for (int row = 0; row < 7; row++)
                    {
                        for (int col = 0; col < 5; col++)
                        {
                            bool on = ((glyph >> ((6 - row) * 5 + (4 - col))) & 1) == 1;
                            if (!on)
                                continue;
                            // Scale the 5x7 glyph to charWidth x charHeight
                            int px0 = x + col * charWidth / 5;
                            int px1 = x + (col + 1) * charWidth / 5;
                            int py0 = startY + (6 - row) * charHeight / 7;
                            int py1 = startY + (7 - row) * charHeight / 7;
                            for (int py = py0; py < py1 && py < tex.height; py++)
                            for (int px = px0; px < px1 && px < tex.width; px++)
                                tex.SetPixel(px, py, color);
                        }
                    }
                }
                x += charWidth + spacing;
            }
        }

        private static ulong GetGlyph(char c)
        {
            // 5x7 pixel font stored as 35-bit values (row0=bits34-30 ... row6=bits4-0)
            // Each row is 5 wide, MSB=left. Row 0 is top.
            switch (char.ToUpperInvariant(c))
            {
                case 'A':
                    return 0b01110_10001_10001_11111_10001_10001_10001UL;
                case 'B':
                    return 0b11110_10001_10001_11110_10001_10001_11110UL;
                case 'C':
                    return 0b01110_10001_10000_10000_10000_10001_01110UL;
                case 'D':
                    return 0b11100_10010_10001_10001_10001_10010_11100UL;
                case 'E':
                    return 0b11111_10000_10000_11110_10000_10000_11111UL;
                case 'F':
                    return 0b11111_10000_10000_11110_10000_10000_10000UL;
                case 'G':
                    return 0b01110_10001_10000_10111_10001_10001_01110UL;
                case 'H':
                    return 0b10001_10001_10001_11111_10001_10001_10001UL;
                case 'I':
                    return 0b01110_00100_00100_00100_00100_00100_01110UL;
                case 'K':
                    return 0b10001_10010_10100_11000_10100_10010_10001UL;
                case 'L':
                    return 0b10000_10000_10000_10000_10000_10000_11111UL;
                case 'M':
                    return 0b10001_11011_10101_10101_10001_10001_10001UL;
                case 'N':
                    return 0b10001_11001_10101_10011_10001_10001_10001UL;
                case 'O':
                    return 0b01110_10001_10001_10001_10001_10001_01110UL;
                case 'R':
                    return 0b11110_10001_10001_11110_10100_10010_10001UL;
                case 'S':
                    return 0b01110_10001_10000_01110_00001_10001_01110UL;
                case 'T':
                    return 0b11111_00100_00100_00100_00100_00100_00100UL;
                case 'U':
                    return 0b10001_10001_10001_10001_10001_10001_01110UL;
                case 'V':
                    return 0b10001_10001_10001_10001_01010_01010_00100UL;
                case 'W':
                    return 0b10001_10001_10001_10101_10101_11011_10001UL;
                case 'Y':
                    return 0b10001_10001_01010_00100_00100_00100_00100UL;
                case '0':
                    return 0b01110_10011_10101_10101_10101_11001_01110UL;
                case '1':
                    return 0b00100_01100_00100_00100_00100_00100_01110UL;
                case '2':
                    return 0b01110_10001_00001_00010_00100_01000_11111UL;
                case '3':
                    return 0b01110_10001_00001_00110_00001_10001_01110UL;
                case '4':
                    return 0b00010_00110_01010_10010_11111_00010_00010UL;
                case '5':
                    return 0b11111_10000_11110_00001_00001_10001_01110UL;
                case '6':
                    return 0b01110_10001_10000_11110_10001_10001_01110UL;
                case '7':
                    return 0b11111_00001_00010_00100_01000_01000_01000UL;
                case '8':
                    return 0b01110_10001_10001_01110_10001_10001_01110UL;
                case '9':
                    return 0b01110_10001_10001_01111_00001_10001_01110UL;
                case 'J':
                    return 0b00111_00010_00010_00010_00010_10010_01100UL;
                case 'P':
                    return 0b11110_10001_10001_11110_10000_10000_10000UL;
                case 'Q':
                    return 0b01110_10001_10001_10001_10101_10010_01101UL;
                case 'X':
                    return 0b10001_01010_00100_00100_00100_01010_10001UL;
                case 'Z':
                    return 0b11111_00001_00010_00100_01000_10000_11111UL;
                case '-':
                    return 0b00000_00000_00000_11111_00000_00000_00000UL;
                case '_':
                    return 0b00000_00000_00000_00000_00000_00000_11111UL;
                case ' ':
                    return 0UL;
                case '+':
                    return 0b00000_00100_00100_11111_00100_00100_00000UL;
                default:
                    return 0UL;
            }
        }

        /// <summary>
        /// Downscales a Texture2D so that its longest edge is at most <paramref name="maxEdge"/> pixels.
        /// Uses bilinear filtering via a temporary RenderTexture blit.
        /// Caller must destroy the returned Texture2D.
        /// </summary>
        public static Texture2D DownscaleTexture(Texture2D source, int maxEdge)
        {
            if (source == null)
                throw new System.ArgumentNullException(nameof(source));
            if (maxEdge <= 0)
                throw new System.ArgumentOutOfRangeException(nameof(maxEdge), maxEdge, "maxEdge must be > 0.");

            int srcW = source.width;
            int srcH = source.height;
            ValidateFrameDimensions(srcW, srcH);
            ValidateMaxResolution(maxEdge);
            float scale = Mathf.Min((float)maxEdge / srcW, (float)maxEdge / srcH);
            scale = Mathf.Min(scale, 1f); // never upscale
            int dstW = Mathf.Max(1, Mathf.RoundToInt(srcW * scale));
            int dstH = Mathf.Max(1, Mathf.RoundToInt(srcH * scale));

            // Match the temporary RT's read/write mode to the source texture rather than the
            // project default. In a Linear-colorspace project a Default (sRGB) RT applies an
            // sRGB encode on store while the blit samples a linear-flagged capture without a
            // matching decode, which washes the image out (see issue #1328).
            bool srcIsSrgb = GraphicsFormatUtility.IsSRGBFormat(source.graphicsFormat);
            RenderTextureReadWrite readWrite = srcIsSrgb ? RenderTextureReadWrite.sRGB : RenderTextureReadWrite.Linear;

            RenderTexture prevActive = RenderTexture.active;
            var rt = RenderTexture.GetTemporary(dstW, dstH, 0, RenderTextureFormat.ARGB32, readWrite);
            Texture2D dst = null;
            try
            {
                rt.filterMode = FilterMode.Bilinear;
                Graphics.Blit(source, rt);
                RenderTexture.active = rt;
                dst = new Texture2D(dstW, dstH, TextureFormat.RGBA32, false, linear: !srcIsSrgb);
                dst.ReadPixels(new Rect(0, 0, dstW, dstH), 0, 0);
                dst.Apply();
                var result = dst;
                dst = null; // transfer ownership to caller
                return result;
            }
            finally
            {
                RenderTexture.active = prevActive;
                RenderTexture.ReleaseTemporary(rt);
                DestroyTexture(dst);
            }
        }

        private static void DestroyTexture(Texture2D tex)
        {
            if (tex == null)
                return;
            if (Application.isPlaying)
                UnityEngine.Object.Destroy(tex);
            else
                UnityEngine.Object.DestroyImmediate(tex);
        }

        /// <summary>Validates the complete output immediately before opening it for a capture write.</summary>
        public static void WriteCaptureBytes(string fullPath, byte[] bytes, bool ensureUniqueFileName = true)
        {
            if (bytes == null)
                throw new ArgumentNullException(nameof(bytes));
            string containedPath = SafePathUtility.ResolveWithinRoot(GetProjectRootPath(), fullPath);
            using (var folders = new OutputFolderScope(GetProjectRootPath()))
            {
                folders.EnsureParentDirectory(containedPath);
                containedPath = SafePathUtility.ResolveWithinRoot(GetProjectRootPath(), containedPath);
                using (
                    var output = new FileStream(containedPath, ensureUniqueFileName ? FileMode.CreateNew : FileMode.Create, FileAccess.Write, FileShare.None)
                )
                {
                    output.Write(bytes, 0, bytes.Length);
                }
                folders.Complete();
            }
        }

        public static ScreenshotCaptureResult PrepareCaptureResult(
            string fileName,
            int superSize,
            bool ensureUniqueFileName,
            string folderOverride,
            bool isAsync
        )
        {
            ValidateSuperSize(superSize);
            int size = Mathf.Max(1, superSize);
            string resolvedName = BuildFileName(fileName);
            string folderAbsolute = ResolveFolderAbsolute(folderOverride);
            string fullPath = SafePathUtility.ResolveWithinRoot(folderAbsolute, resolvedName);
            if (ensureUniqueFileName)
            {
                fullPath = EnsureUnique(fullPath);
            }

            fullPath = SafePathUtility.ResolveWithinRoot(folderAbsolute, fullPath);
            string normalizedFullPath = fullPath.Replace('\\', '/');
            string projectRelativePath = ToProjectRelativePath(normalizedFullPath);

            return new ScreenshotCaptureResult(normalizedFullPath, projectRelativePath, size, isAsync);
        }

        /// <summary>
        /// Resolves a folder spec (which may be project-relative like "Assets/Screenshots",
        /// absolute, or null/empty) to an absolute filesystem path inside the project root.
        /// Throws on traversal escape or absolute paths outside the project.
        /// </summary>
        public static string ResolveFolderAbsolute(string folderOverride)
        {
            string projectRoot = GetProjectRootPath().TrimEnd('/');
            string requested = string.IsNullOrWhiteSpace(folderOverride) ? DefaultFolder : folderOverride.Trim();
            requested = requested.Replace('\\', '/').TrimEnd('/');

            string combined = Path.IsPathRooted(requested) ? requested : Path.Combine(projectRoot, requested);

            string fullFolder = Path.GetFullPath(combined).Replace('\\', '/').TrimEnd('/');
            string normalizedRoot = projectRoot;

            // Reject paths that escape the project root (case-insensitive on Windows, exact elsewhere).
            var rootComparison = Application.platform == RuntimePlatform.WindowsEditor ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (!fullFolder.Equals(normalizedRoot, rootComparison) && !fullFolder.StartsWith(normalizedRoot + "/", rootComparison))
            {
                throw new InvalidOperationException(
                    $"Screenshot folder '{folderOverride}' resolves outside the Unity project root ('{fullFolder}'). "
                        + $"Use a project-relative path (e.g. 'Assets/Screenshots' or 'Captures')."
                );
            }

            return SafePathUtility.ResolveWithinRoot(projectRoot, fullFolder);
        }

        /// <summary>
        /// Converts an absolute filesystem path inside the project to a project-relative path
        /// (forward slashes, no leading separator). Returns the input unchanged when it does
        /// not live under the project root.
        /// </summary>
        public static string ToProjectRelativePath(string normalizedFullPath)
        {
            if (string.IsNullOrEmpty(normalizedFullPath))
                return normalizedFullPath;
            string projectRoot = GetProjectRootPath();
            string normalized = normalizedFullPath.Replace('\\', '/');
            if (normalized.StartsWith(projectRoot, StringComparison.OrdinalIgnoreCase))
            {
                return normalized.Substring(projectRoot.Length).TrimStart('/');
            }
            return normalized;
        }

        /// <summary>
        /// True when <paramref name="projectRelativePath"/> lives under the Unity Assets/ folder
        /// (and therefore should be imported via AssetDatabase).
        /// </summary>
        public static bool IsUnderAssets(string projectRelativePath)
        {
            if (string.IsNullOrEmpty(projectRelativePath))
                return false;
            string norm = projectRelativePath.Replace('\\', '/').TrimStart('/');
            return norm.Equals("Assets", StringComparison.OrdinalIgnoreCase) || norm.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase);
        }

        private static string BuildFileName(string fileName)
        {
            string name = string.IsNullOrWhiteSpace(fileName) ? $"screenshot-{DateTime.Now:yyyyMMdd-HHmmss}" : fileName.Trim();

            if (
                name == "."
                || name == ".."
                || name.IndexOfAny(new[] { '/', '\\', ':', '*', '?', '"', '<', '>', '|' }) >= 0
                || name.Any(char.IsControl)
                || name.EndsWith(".")
                || name.Length > 200
            )
                throw new InvalidOperationException("Screenshot filename must be a simple filename without directory components.");

            name = SanitizeFileName(name);

            if (
                !name.EndsWith(".png", StringComparison.OrdinalIgnoreCase)
                && !name.EndsWith(".jpg", StringComparison.OrdinalIgnoreCase)
                && !name.EndsWith(".jpeg", StringComparison.OrdinalIgnoreCase)
            )
            {
                name += ".png";
            }

            return name;
        }

        private static string SanitizeFileName(string fileName)
        {
            // GetInvalidFileNameChars() doesn't include '\' or '/' on Unix, so a caller-supplied
            // name like "foo\bar" would survive and later get spliced into the directory portion.
            var invalidChars = Path.GetInvalidFileNameChars();
            string cleaned = new string(fileName.Select(ch => invalidChars.Contains(ch) || ch == '/' || ch == '\\' ? '_' : ch).ToArray());

            return string.IsNullOrWhiteSpace(cleaned) ? "screenshot" : cleaned;
        }

        private static string EnsureUnique(string path)
        {
            string directory = Path.GetDirectoryName(path) ?? string.Empty;
            path = SafePathUtility.ResolveWithinRoot(directory, path);
            if (!File.Exists(path))
            {
                return path;
            }

            string baseName = Path.GetFileNameWithoutExtension(path);
            string extension = Path.GetExtension(path);
            int counter = 1;

            string candidate;
            do
            {
                candidate = Path.Combine(directory, $"{baseName}-{counter}{extension}");
                candidate = SafePathUtility.ResolveWithinRoot(directory, candidate);
                counter++;
            } while (File.Exists(candidate));

            return candidate;
        }

        private static string GetProjectRootPath()
        {
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            root = root.Replace('\\', '/');
            if (!root.EndsWith("/", StringComparison.Ordinal))
            {
                root += "/";
            }
            return root;
        }
    }

    /// <summary>
    /// Transient MonoBehaviour that yields WaitForEndOfFrame, calls
    /// ScreenCapture.CaptureScreenshotAsTexture, invokes the callback, and self-destructs.
    /// Times out via the editor update loop so a paused or unfocused PlayerLoop cannot leak
    /// hidden capturer objects for the rest of the session.
    /// </summary>
    public sealed class ScreenshotCapturer : MonoBehaviour
    {
        public const float DefaultTimeoutSeconds = 2f;

        private int _superSize = 1;
        private Action<Texture2D, bool> _onComplete;
        private float _timeoutSeconds = DefaultTimeoutSeconds;
        private float _startedAt;
        private bool _finished;
        private bool _destroying;

        /// <summary>Spawns a hidden GameObject, attaches a capturer, returns immediately.</summary>
        public static ScreenshotCapturer Begin(int superSize, Action<Texture2D> onComplete, float timeoutSeconds = DefaultTimeoutSeconds)
        {
            return Begin(superSize, onComplete == null ? (Action<Texture2D, bool>)null : (tex, _) => onComplete(tex), timeoutSeconds);
        }

        /// <summary>Spawns a hidden GameObject, attaches a capturer, returns immediately.</summary>
        public static ScreenshotCapturer Begin(int superSize, Action<Texture2D, bool> onComplete, float timeoutSeconds = DefaultTimeoutSeconds)
        {
            ScreenshotUtility.ValidateCaptureDimensions(Mathf.Max(1, Screen.width), Mathf.Max(1, Screen.height), superSize, out _, out _);
            var go = new GameObject("__MCP_ScreenshotCapturer__") { hideFlags = HideFlags.HideAndDontSave };
            var c = go.AddComponent<ScreenshotCapturer>();
            c._superSize = superSize;
            c._onComplete = onComplete;
            c._timeoutSeconds = Mathf.Max(0.05f, timeoutSeconds);
            c._startedAt = Time.realtimeSinceStartup;
            c.ArmTimeout();
            return c;
        }

        private void ArmTimeout()
        {
#if UNITY_EDITOR
            UnityEditor.EditorApplication.update += TickTimeout;
#else
            StartCoroutine(TimeoutWatch());
#endif
        }

        private void OnDestroy()
        {
            _destroying = true;
            DisarmTimeout();
            if (!_finished)
                Complete(null, timedOut: true);
        }

        private void DisarmTimeout()
        {
#if UNITY_EDITOR
            UnityEditor.EditorApplication.update -= TickTimeout;
#endif
        }

#if UNITY_EDITOR
        private void TickTimeout()
        {
            if (_finished)
                return;
            if (Time.realtimeSinceStartup - _startedAt < _timeoutSeconds)
                return;
            Complete(null, timedOut: true);
        }
#else
        private System.Collections.IEnumerator TimeoutWatch()
        {
            yield return new WaitForSecondsRealtime(_timeoutSeconds);
            if (!_finished)
                Complete(null, timedOut: true);
        }
#endif

        private System.Collections.IEnumerator Start()
        {
            yield return new WaitForEndOfFrame();
            if (_finished)
                yield break;

            Texture2D tex = null;
            try
            {
                ScreenshotUtility.ValidateCaptureDimensions(Mathf.Max(1, Screen.width), Mathf.Max(1, Screen.height), _superSize, out _, out _);
                tex = ScreenCapture.CaptureScreenshotAsTexture(_superSize);
                if (tex != null)
                    ScreenshotUtility.ValidateFrameDimensions(tex.width, tex.height);
            }
            catch (Exception ex)
            {
                if (tex != null)
                    Destroy(tex);
                tex = null;
                Debug.LogError($"[MCP for Unity] CaptureScreenshotAsTexture failed: {ex.Message}");
            }
            Complete(tex, timedOut: false);
        }

        private void Complete(Texture2D tex, bool timedOut)
        {
            if (_finished)
            {
                if (tex != null)
                {
                    if (Application.isPlaying)
                        Destroy(tex);
                    else
                        DestroyImmediate(tex);
                }
                return;
            }
            _finished = true;
            DisarmTimeout();
            var onComplete = _onComplete;
            _onComplete = null;
            try
            {
                if (onComplete != null)
                    onComplete(tex, timedOut);
                else if (tex != null)
                {
                    if (Application.isPlaying)
                        Destroy(tex);
                    else
                        DestroyImmediate(tex);
                }
            }
            finally
            {
                // `this == null` once something else destroyed the capturer: outside play mode
                // Unity sends it no OnDestroy, so _destroying stays false, and touching
                // gameObject then throws MissingReferenceException.
                if (!_destroying && this != null)
                {
#if UNITY_EDITOR
                    DestroyImmediate(gameObject);
#else
                    Destroy(gameObject);
#endif
                }
            }
        }
    }
}
