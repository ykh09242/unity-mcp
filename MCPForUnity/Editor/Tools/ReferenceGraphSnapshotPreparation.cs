using System;
using System.Diagnostics;
using System.Threading;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Resumable conversion, merge ordering and deduplication; callers own the input array.</summary>
    internal sealed class ReferenceGraphSnapshotPreparation
    {
        private string[] values;
        private string[] scratch;
        private readonly Func<string, string> convert;
        private int position;
        private int count;
        private int unique;
        private int width = 1;
        private int run;
        private int left;
        private int middle;
        private int right;
        private int end;
        private int destination;
        private string previous;
        private string[] result;
        internal string Stage { get; private set; } = "convert";
        internal int InputCount { get; }
        internal int LastStepOperations { get; private set; }
        internal long Operations { get; private set; }
        internal bool Complete => Stage == "complete";
        internal string[] Result => Complete ? result : null;

        internal ReferenceGraphSnapshotPreparation(string[] input, Func<string, string> convert)
        {
            values = input;
            InputCount = input.Length;
            this.convert = convert;
        }

        internal void Step(int maximumOperations, double milliseconds, CancellationToken token)
        {
            if (maximumOperations < 1 || milliseconds < 0 || double.IsNaN(milliseconds))
                throw new ArgumentOutOfRangeException(nameof(maximumOperations));
            var watch = Stopwatch.StartNew();
            LastStepOperations = 0;
            do
            {
                token.ThrowIfCancellationRequested();
                if (Complete)
                    return;
                Advance();
                LastStepOperations++;
                Operations++;
            } while (!Complete && LastStepOperations < maximumOperations && watch.Elapsed.TotalMilliseconds < milliseconds);
        }

        private void Advance()
        {
            switch (Stage)
            {
                case "convert":
                    if (position < InputCount)
                    {
                        string value = convert(values[position++]);
                        if (!string.IsNullOrEmpty(value))
                            values[count++] = value;
                    }
                    if (position == InputCount)
                    {
                        position = 0;
                        scratch = new string[count];
                        Stage = count > 1 ? "sort" : "deduplicate";
                        BeginRun();
                    }
                    break;
                case "sort":
                    scratch[destination++] =
                        left < middle && (right == end || StringComparer.Ordinal.Compare(values[left], values[right]) <= 0) ? values[left++] : values[right++];
                    if (destination == end)
                    {
                        run = end;
                        if (run == count)
                        {
                            (values, scratch) = (scratch, values);
                            run = 0;
                            if (width >= count - width)
                                Stage = "deduplicate";
                            else
                                width *= 2;
                        }
                        BeginRun();
                    }
                    break;
                case "deduplicate":
                    if (position < count)
                    {
                        string value = values[position++];
                        if (previous == null || !StringComparer.Ordinal.Equals(previous, value))
                            unique++;
                        previous = value;
                    }
                    if (position == count)
                    {
                        result = new string[unique];
                        position = 0;
                        destination = 0;
                        previous = null;
                        Stage = "publish";
                    }
                    break;
                case "publish":
                    if (position < count)
                    {
                        string value = values[position++];
                        if (previous == null || !StringComparer.Ordinal.Equals(previous, value))
                            result[destination++] = value;
                        previous = value;
                    }
                    if (position == count)
                    {
                        values = null;
                        scratch = null;
                        Stage = "complete";
                    }
                    break;
            }
        }

        private void BeginRun()
        {
            left = run;
            middle = run + Math.Min(width, count - run);
            right = middle;
            end = middle + Math.Min(width, count - middle);
            destination = run;
        }
    }
}
