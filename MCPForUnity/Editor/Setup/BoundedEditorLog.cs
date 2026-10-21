using System;
using System.Collections.Generic;
using System.Text;

namespace MCPForUnity.Editor.Setup
{
    /// <summary>Thread-safe bounded output; producers and UI share one retained tail.</summary>
    internal sealed class BoundedEditorLog
    {
        private const int MaximumLines = 1024;
        private const int MaximumCharacters = 256 * 1024;
        private const int MaximumLineCharacters = 4096;
        private readonly object gate = new();
        private readonly Queue<string> lines = new();
        private int characters;
        private long omitted;
        private long revision;
        private string snapshot = string.Empty;
        public long Revision
        {
            get
            {
                lock (gate)
                    return revision;
            }
        }

        public void Append(string line)
        {
            string text = Sanitize(line);
            if (string.IsNullOrWhiteSpace(text))
                return;
            text = $"[{DateTime.Now:HH:mm:ss}] {text}{Environment.NewLine}";
            lock (gate)
            {
                while (lines.Count >= MaximumLines || characters + text.Length > MaximumCharacters)
                {
                    characters -= lines.Dequeue().Length;
                    if (omitted < long.MaxValue)
                        omitted++;
                }
                lines.Enqueue(text);
                characters += text.Length;
                revision++;
                snapshot = null;
            }
        }

        public string Snapshot()
        {
            lock (gate)
            {
                if (snapshot != null)
                    return snapshot;
                var builder = new StringBuilder(characters + 80);
                if (omitted > 0)
                    builder.Append("Earlier log lines omitted: ").Append(omitted).AppendLine();
                foreach (string line in lines)
                    builder.Append(line);
                return snapshot = builder.ToString();
            }
        }

        public void Clear()
        {
            lock (gate)
            {
                lines.Clear();
                characters = 0;
                omitted = 0;
                revision++;
                snapshot = string.Empty;
            }
        }

        private static string Sanitize(string line)
        {
            if (string.IsNullOrEmpty(line))
                return string.Empty;
            int inspected = Math.Min(line.Length, MaximumLineCharacters * 4);
            var builder = new StringBuilder(Math.Min(inspected, MaximumLineCharacters));
            bool escape = false;
            for (int index = 0; index < inspected && builder.Length < MaximumLineCharacters; index++)
            {
                char value = line[index];
                if (escape)
                {
                    if (value >= '@' && value <= '~' && value != '[')
                        escape = false;
                    continue;
                }
                if (value == '\u001b')
                {
                    escape = true;
                    continue;
                }
                if (value == '\t' || (value >= ' ' && value != 127))
                    builder.Append(value);
            }
            if (builder.Length > 0 && char.IsHighSurrogate(builder[builder.Length - 1]))
                builder.Length--;
            return builder.ToString().Trim();
        }
    }
}
