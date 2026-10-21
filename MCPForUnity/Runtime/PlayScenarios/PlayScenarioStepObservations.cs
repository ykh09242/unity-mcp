using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioPerformanceOptions
    {
        [JsonProperty("max_duration_ms", NullValueHandling = NullValueHandling.Ignore)]
        public int? MaxDurationMs;

        [JsonProperty("max_frame_time_p95_ms", NullValueHandling = NullValueHandling.Ignore)]
        public double? MaxFrameTimeP95Ms;

        [JsonProperty("max_gc_allocated_bytes_per_frame", NullValueHandling = NullValueHandling.Ignore)]
        public int? MaxGcAllocatedBytesPerFrame;

        [JsonProperty("sample_limit")]
        public int SampleLimit = 4096;

        internal static PlayScenarioPerformanceOptions Parse(JObject value)
        {
            PlayScenarioDefinition.Fields(value, "max_duration_ms", "max_frame_time_p95_ms", "max_gc_allocated_bytes_per_frame", "sample_limit");
            var result = new PlayScenarioPerformanceOptions
            {
                SampleLimit = PlayScenarioDefinition.Integer(value, "sample_limit", 1, 4096, 4096),
                MaxDurationMs = value.Property("max_duration_ms") == null ? (int?)null : PlayScenarioDefinition.Integer(value, "max_duration_ms", 0, 120000, 0),
                MaxGcAllocatedBytesPerFrame =
                    value.Property("max_gc_allocated_bytes_per_frame") == null
                        ? (int?)null
                        : PlayScenarioDefinition.Integer(value, "max_gc_allocated_bytes_per_frame", 0, int.MaxValue, 0),
            };
            if (value.Property("max_frame_time_p95_ms") != null)
            {
                JToken token = value["max_frame_time_p95_ms"];
                if (
                    (token.Type != JTokenType.Float && token.Type != JTokenType.Integer)
                    || !double.TryParse(
                        token.ToString(),
                        System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture,
                        out double number
                    )
                    || double.IsNaN(number)
                    || double.IsInfinity(number)
                    || number < 0
                    || number > 120000
                )
                    throw new ArgumentException("max_frame_time_p95_ms must be a finite number between 0 and 120000.");
                result.MaxFrameTimeP95Ms = number;
            }
            return result;
        }
    }

    public sealed class PlayScenarioScreenshotOptions
    {
        public const int MaximumDimension = 8192;
        public const long MaximumPixels = 64L * 1024 * 1024;
        public const int MaximumPngBytes = 320 * 1024 * 1024;
        public const long MaximumRunPngBytes = 8L * 1024 * 1024 * 1024;

        [JsonProperty("name")]
        public string Name;

        [JsonProperty("width")]
        public int Width;

        [JsonProperty("height")]
        public int Height;

        internal static PlayScenarioScreenshotOptions Parse(JObject value)
        {
            PlayScenarioDefinition.Fields(value, "name", "width", "height");
            string name = PlayScenarioDefinition.Text(value, "name", 64);
            PlayScenarioDefinition.ValidateName(name);
            if (value.Property("width") == null || value.Property("height") == null)
                throw new ArgumentException("Screenshot width and height are required.");
            int width = PlayScenarioDefinition.Integer(value, "width", 1, MaximumDimension, 1);
            int height = PlayScenarioDefinition.Integer(value, "height", 1, MaximumDimension, 1);
            if ((long)width * height > MaximumPixels)
                throw new ArgumentException("Screenshot dimensions exceed the 64-Mi-pixel budget.");
            return new PlayScenarioScreenshotOptions
            {
                Name = name,
                Width = width,
                Height = height,
            };
        }
    }

    public sealed class PlayScenarioPerformanceResult
    {
        [JsonProperty("duration_ms")]
        public double DurationMs;

        [JsonProperty("frame_sample_count")]
        public int FrameSampleCount;

        [JsonProperty("gc_sample_count")]
        public int GcSampleCount;

        [JsonProperty("observed_frame_count")]
        public long ObservedFrameCount;

        [JsonProperty("sample_limit_exceeded")]
        public bool SampleLimitExceeded;

        [JsonProperty("frame_time_p95_ms", NullValueHandling = NullValueHandling.Ignore)]
        public double? FrameTimeP95Ms;

        [JsonProperty("gc_allocated_bytes_per_frame_max", NullValueHandling = NullValueHandling.Ignore)]
        public long? GcAllocatedBytesPerFrameMax;

        [JsonProperty("passed")]
        public bool Passed;

        [JsonProperty("error", NullValueHandling = NullValueHandling.Ignore)]
        public string Error;
    }

    public sealed class PlayScenarioScreenshotResult
    {
        [JsonProperty("name")]
        public string Name;

        [JsonProperty("status")]
        public string Status = "pending";

        [JsonProperty("path", NullValueHandling = NullValueHandling.Ignore)]
        public string Path;

        [JsonProperty("sha256", NullValueHandling = NullValueHandling.Ignore)]
        public string Sha256;

        [JsonProperty("requested_width")]
        public int RequestedWidth;

        [JsonProperty("requested_height")]
        public int RequestedHeight;

        [JsonProperty("width", NullValueHandling = NullValueHandling.Ignore)]
        public int? Width;

        [JsonProperty("height", NullValueHandling = NullValueHandling.Ignore)]
        public int? Height;

        [JsonProperty("error", NullValueHandling = NullValueHandling.Ignore)]
        public string Error;
    }

    /// <summary>Retains bounded real frame samples. Overflow and missing selected metrics cannot pass.</summary>
    public sealed class PlayScenarioPerformanceSamples
    {
        private readonly PlayScenarioPerformanceOptions options;
        private readonly List<double> frames;
        private long? gcMaximum;
        private int gcCount;
        private long observed;
        private string error;

        public PlayScenarioPerformanceSamples(PlayScenarioPerformanceOptions options)
        {
            this.options = options ?? throw new ArgumentNullException(nameof(options));
            frames = new List<double>(options.SampleLimit);
        }

        public void AddFrame(double frameTimeMs, long? gcAllocatedBytes)
        {
            observed++;
            if (observed > options.SampleLimit)
                return;
            if (!double.IsNaN(frameTimeMs) && !double.IsInfinity(frameTimeMs) && frameTimeMs > 0)
                frames.Add(frameTimeMs);
            else
                Unavailable("A frame duration was unavailable.");
            if (gcAllocatedBytes.HasValue && gcAllocatedBytes.Value >= 0)
            {
                gcCount++;
                gcMaximum = Math.Max(gcMaximum ?? 0, gcAllocatedBytes.Value);
            }
            else if (options.MaxGcAllocatedBytesPerFrame.HasValue)
                Unavailable("GC Allocated In Frame is unavailable on this execution host.");
        }

        public void Unavailable(string reason) => error = error ?? PlayScenarioEngine.Bounded(reason, 1024);

        public PlayScenarioPerformanceResult Finish(double durationMs)
        {
            var result = new PlayScenarioPerformanceResult
            {
                DurationMs = Math.Max(0, durationMs),
                FrameSampleCount = frames.Count,
                GcSampleCount = gcCount,
                ObservedFrameCount = observed,
                SampleLimitExceeded = observed > options.SampleLimit,
                GcAllocatedBytesPerFrameMax = gcMaximum,
                Error = error,
            };
            if (frames.Count > 0)
            {
                double[] sorted = frames.ToArray();
                Array.Sort(sorted);
                result.FrameTimeP95Ms = sorted[(int)Math.Ceiling(sorted.Length * 0.95) - 1];
            }
            if ((options.MaxFrameTimeP95Ms.HasValue || options.MaxGcAllocatedBytesPerFrame.HasValue) && result.SampleLimitExceeded)
                result.Error = result.Error ?? "Performance sample limit exceeded; complete per-frame budget evidence is unavailable.";
            if (options.MaxFrameTimeP95Ms.HasValue && !result.FrameTimeP95Ms.HasValue)
                result.Error = result.Error ?? "No complete runtime frame duration was observed for this step.";
            if (options.MaxGcAllocatedBytesPerFrame.HasValue && (!gcMaximum.HasValue || gcCount != frames.Count))
                result.Error = result.Error ?? "Complete per-frame GC measurements are unavailable for this step.";
            if (
                (options.MaxDurationMs.HasValue && result.DurationMs > options.MaxDurationMs.Value)
                || (options.MaxFrameTimeP95Ms.HasValue && result.FrameTimeP95Ms > options.MaxFrameTimeP95Ms)
                || (options.MaxGcAllocatedBytesPerFrame.HasValue && gcMaximum > options.MaxGcAllocatedBytesPerFrame)
            )
                result.Error = result.Error ?? "Step performance budget exceeded.";
            result.Passed = result.Error == null;
            return result;
        }
    }
}
