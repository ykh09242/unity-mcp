using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    internal sealed class PlayScenarioSoakComparisonChart : VisualElement, IDisposable
    {
        internal static readonly Color32 BaselineColor = new Color32(47, 156, 223, 255);
        internal static readonly Color32 CandidateColor = new Color32(231, 143, 52, 255);
        private PlayScenarioSoakComparison comparison;
        private string metric;
        internal Vertex[] CachedVertices { get; private set; } = Array.Empty<Vertex>();
        internal ushort[] CachedIndices { get; private set; } = Array.Empty<ushort>();
        internal int BaselineEdges { get; private set; }
        internal int CandidateEdges { get; private set; }
        internal string RangeText { get; private set; } = "No saved measurements.";

        internal PlayScenarioSoakComparisonChart()
        {
            name = "soakComparisonChart";
            AddToClassList("scenario-soak-chart");
            generateVisualContent += Paint;
            RegisterCallback<GeometryChangedEvent>(OnGeometryChanged);
        }

        internal void SetData(PlayScenarioSoakComparison value, string selectedMetric)
        {
            comparison = value;
            metric = selectedMetric;
            Rebuild(contentRect.width, contentRect.height);
        }

        private void OnGeometryChanged(GeometryChangedEvent _) => Rebuild(contentRect.width, contentRect.height);

        internal void Rebuild(float width, float height)
        {
            var vertices = new List<Vertex>();
            var indices = new List<ushort>();
            BaselineEdges = CandidateEdges = 0;
            RangeText = "No saved measurements.";
            if (comparison != null)
            {
                var left = comparison.Baseline.Series[metric];
                var right = comparison.Candidate.Series[metric];
                long[] values = left.Concat(right).Where(point => point.Value.HasValue).Select(point => point.Value.Value).ToArray();
                int total = Math.Max(left.Length, right.Length);
                if (values.Length > 0 && total > 0)
                {
                    long minimum = values.Min();
                    long maximum = values.Max();
                    decimal span = maximum == minimum ? 1 : (decimal)maximum - minimum;
                    RangeText = "Iterations 1–" + total + "; range " + minimum + "–" + maximum + (metric == "object_count" ? " objects." : " bytes.");
                    if (!(width > 4 && height > 4) || float.IsNaN(width) || float.IsNaN(height))
                    {
                        CachedVertices = Array.Empty<Vertex>();
                        CachedIndices = Array.Empty<ushort>();
                        MarkDirtyRepaint();
                        return;
                    }
                    int warmup = Math.Min(total, Math.Max(comparison.Baseline.Warmup, comparison.Candidate.Warmup));
                    float warmupWidth = warmup == 0 ? 0 : Math.Min(width, 2 + (width - 4) * (warmup - 0.5f) / Math.Max(1, total - 1));
                    if (warmupWidth > 0)
                        Quad(
                            vertices,
                            indices,
                            new Vector2(0, 0),
                            new Vector2(warmupWidth, 0),
                            new Vector2(warmupWidth, height),
                            new Vector2(0, height),
                            new Color32(139, 139, 139, 45)
                        );
                    for (int grid = 1; grid < 4; grid++)
                        Line(vertices, indices, new Vector2(0, grid * height / 4), new Vector2(width, grid * height / 4), 1, new Color32(130, 130, 130, 65));
                    BaselineEdges = Series(left, BaselineColor);
                    CandidateEdges = Series(right, CandidateColor);

                    int Series(PlayScenarioSoakComparisonSnapshot.Point[] points, Color32 color)
                    {
                        int edges = 0;
                        Vector2? previous = null;
                        foreach (var point in points)
                        {
                            if (!point.Value.HasValue)
                            {
                                previous = null;
                                continue;
                            }
                            var position = new Vector2(
                                2 + (width - 4) * (point.Iteration - 1) / Math.Max(1, total - 1),
                                height - 2 - (height - 4) * (float)(((decimal)point.Value.Value - minimum) / span)
                            );
                            if (previous.HasValue)
                            {
                                Line(vertices, indices, previous.Value, position, 2, color);
                                edges++;
                            }
                            Quad(
                                vertices,
                                indices,
                                position + new Vector2(-1, -1),
                                position + new Vector2(1, -1),
                                position + new Vector2(1, 1),
                                position + new Vector2(-1, 1),
                                color
                            );
                            previous = position;
                        }
                        return edges;
                    }
                }
            }
            CachedVertices = vertices.ToArray();
            CachedIndices = indices.ToArray();
            MarkDirtyRepaint();
        }

        private static void Line(List<Vertex> vertices, List<ushort> indices, Vector2 start, Vector2 end, float thickness, Color32 color)
        {
            Vector2 delta = end - start;
            Vector2 normal = new Vector2(-delta.y, delta.x).normalized * thickness / 2;
            Quad(vertices, indices, start - normal, end - normal, end + normal, start + normal, color);
        }

        private static void Quad(List<Vertex> vertices, List<ushort> indices, Vector2 a, Vector2 b, Vector2 c, Vector2 d, Color32 color)
        {
            ushort first = (ushort)vertices.Count;
            foreach (Vector2 position in new[] { a, b, c, d })
                vertices.Add(new Vertex { position = new Vector3(position.x, position.y, Vertex.nearZ), tint = color });
            foreach (int offset in new[] { 0, 1, 2, 2, 3, 0 })
                indices.Add((ushort)(first + offset));
        }

        private void Paint(MeshGenerationContext context)
        {
            if (CachedVertices.Length == 0)
                return;
            MeshWriteData mesh = context.Allocate(CachedVertices.Length, CachedIndices.Length, null);
            if (mesh.vertexCount == 0)
                return;
            foreach (Vertex vertex in CachedVertices)
                mesh.SetNextVertex(vertex);
            foreach (ushort index in CachedIndices)
                mesh.SetNextIndex(index);
        }

        public void Dispose()
        {
            generateVisualContent -= Paint;
            UnregisterCallback<GeometryChangedEvent>(OnGeometryChanged);
            comparison = null;
            CachedVertices = Array.Empty<Vertex>();
            CachedIndices = Array.Empty<ushort>();
        }
    }
}
