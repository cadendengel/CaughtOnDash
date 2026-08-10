using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    public interface IAnalyzer
    {
        Task<AnalysisResult> AnalyzeAsync(string videoPath, IProgress<(string stage, int progress)> progress, CancellationToken cancellationToken);

        /// <summary>
        /// The analyzer's version string -- the same value it stamps into each
        /// result's metadata. Used by "requeue outdated" so the backend can
        /// re-run everything not on this version.
        /// </summary>
        Task<string> GetVersionAsync(CancellationToken cancellationToken = default);
    }

    public class PlaceholderAnalyzer : IAnalyzer
    {
        private readonly Random _random;

        public PlaceholderAnalyzer()
        {
            _random = new Random();
        }

        // The placeholder produces stub results, so its "version" is a fixed
        // sentinel. Requeuing against it is meaningless, which the UI notes.
        public Task<string> GetVersionAsync(CancellationToken cancellationToken = default)
            => Task.FromResult("placeholder");

        public async Task<AnalysisResult> AnalyzeAsync(string videoPath, IProgress<(string stage, int progress)> progress, CancellationToken cancellationToken)
        {
            Logger.Log($"Starting placeholder analysis of: {videoPath}");

            // Simulate stages of analysis
            var stages = new[]
            {
                ("downloading", 10),
                ("initializing", 25),
                ("analyzing", 50),
                ("detecting_events", 75),
                ("uploading_results", 90),
            };

            foreach (var (stage, progressValue) in stages)
            {
                if (cancellationToken.IsCancellationRequested)
                {
                    Logger.Log("Analysis cancelled by user");
                    throw new OperationCanceledException();
                }

                progress?.Report((stage, progressValue));
                await Task.Delay(500 + _random.Next(500), cancellationToken); // Simulate work
            }

            // Generate placeholder results
            var result = new AnalysisResult
            {
                Summary = "Placeholder analysis completed. This dashcam footage shows typical driving scenes.",
                Tags = new List<string>
                {
                    "dashcam",
                    "driving",
                    "placeholder",
                    "highway"
                },
                Events = GeneratePlaceholderEvents(),
                Metadata = new Dictionary<string, object>
                {
                    { "analyzer_version", "placeholder-0.1" },
                    { "analysis_date", DateTime.UtcNow.ToString("O") },
                    { "processing_time_seconds", 3.5 }
                }
            };

            Logger.Log("Placeholder analysis completed successfully");
            return result;
        }

        private List<AnalysisEvent> GeneratePlaceholderEvents()
        {
            return new List<AnalysisEvent>
            {
                new AnalysisEvent
                {
                    TimestampSeconds = 2.5f,
                    Label = "placeholder_event",
                    Description = "Test event 1: Placeholder detection for testing worker pipeline",
                    Confidence = 0.75f
                },
                new AnalysisEvent
                {
                    TimestampSeconds = 5.0f,
                    Label = "placeholder_event",
                    Description = "Test event 2: Another fake event for testing",
                    Confidence = 0.85f
                },
                new AnalysisEvent
                {
                    TimestampSeconds = 8.3f,
                    Label = "placeholder_event",
                    Description = "Test event 3: Final test event",
                    Confidence = 0.65f
                }
            };
        }
    }
}
