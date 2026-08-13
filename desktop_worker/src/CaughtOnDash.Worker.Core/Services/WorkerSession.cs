using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// UI-agnostic worker state and lifecycle.
    ///
    /// This holds everything a host needs to render -- status, current job,
    /// stage, progress, last heartbeat -- and raises events when it changes.
    /// It deliberately knows nothing about WPF, Avalonia or the console, so the
    /// Windows and macOS hosts share one implementation.
    /// </summary>
    public class WorkerSession
    {
        private readonly AppConfigService _configService;
        private readonly WorkerApiClient _apiClient;
        private readonly LocalVideoStorageService _storageService;
        private readonly IAnalyzer _analyzer;
        private readonly WorkerConfig _config;

        private WorkerLoopService? _loop;
        private int _lastReviewCount = -1;
        private int _lastQueuedCount = -1;

        public event Action<WorkerSessionState>? StateChanged;
        public event Action<WorkerLogEntry>? LogAppended;

        /// <summary>Raised when the review or run queue has been refetched.</summary>
        public event Action<QueueSnapshot>? QueueChanged;

        public WorkerSession(IAnalyzer? analyzer = null)
        {
            _configService = new AppConfigService();
            _apiClient = new WorkerApiClient();
            _storageService = new LocalVideoStorageService();
            _config = _configService.LoadConfig();
            _analyzer = analyzer ?? CreateAnalyzer(_config);

            State = new WorkerSessionState
            {
                BackendUrl = _config.IsConfigured ? _config.BackendUrl : "Not configured",
                Status = _config.IsConfigured ? "Disconnected" : "Missing config",
                IsConfigured = _config.IsConfigured,
                CanStart = _config.IsConfigured,
                CanStop = false,
                CanCancelJob = false,
            };

            if (_config.IsConfigured)
            {
                _apiClient.Initialize(_config.BackendUrl, _config.ApiToken);
            }
        }

        /// <summary>
        /// Pick the analyzer named in config, defaulting to the placeholder.
        /// </summary>
        /// <remarks>
        /// An unrecognised name falls back rather than throwing: a typo should
        /// leave a working worker with a loud log line, not a dead one.
        /// </remarks>
        private static IAnalyzer CreateAnalyzer(WorkerConfig config)
        {
            var name = (config.Analyzer ?? "").Trim().ToLowerInvariant();

            switch (name)
            {
                case "python":
                    Logger.Log($"Using the Python analyzer ({config.PythonExecutable})");
                    return new PythonAnalyzer(config);

                case "placeholder":
                case "":
                    Logger.Log("Using the placeholder analyzer -- results are not real");
                    return new PlaceholderAnalyzer();

                default:
                    Logger.Log(
                        $"Unknown analyzer '{config.Analyzer}'; falling back to the placeholder.",
                        Logger.LogLevel.Warning);
                    return new PlaceholderAnalyzer();
            }
        }

        public WorkerSessionState State { get; private set; }

        public bool IsConfigured => _config.IsConfigured;

        public bool IsRunning => _loop?.IsRunning ?? false;

        public void Log(string message, Logger.LogLevel level = Logger.LogLevel.Info)
        {
            Logger.Log(message, level);
            LogAppended?.Invoke(new WorkerLogEntry
            {
                Timestamp = DateTime.Now,
                Message = message,
                Level = level,
            });
        }

        public async Task StartAsync()
        {
            if (!_config.IsConfigured)
            {
                Log("Worker not configured. Check appsettings.json.", Logger.LogLevel.Error);
                return;
            }

            if (IsRunning)
            {
                Log("Worker is already running.");
                return;
            }

            try
            {
                _loop = new WorkerLoopService(_config, _apiClient, _analyzer, _storageService);
                _loop.OnStatusUpdate += HandleLoopEvent;

                Mutate(state =>
                {
                    state.CanStart = false;
                    state.CanStop = true;
                });

                
                Log("Connecting...");

                await _loop.StartAsync();
            }
            catch (Exception ex)
            {
                Log($"Failed to start worker: {ex.Message}", Logger.LogLevel.Error);
                Mutate(state =>
                {
                    state.Status = "Error";
                    state.CanStart = true;
                    state.CanStop = false;
                });
            }
        }

        public async Task StopAsync()
        {
            try
            {
                Log("Disconnecting...");

                if (_loop != null)
                {
                    await _loop.StopAsync();
                    _loop.OnStatusUpdate -= HandleLoopEvent;
                    _loop = null;
                }

                Mutate(state =>
                {
                    state.Status = "Disconnected";
                    state.HeartbeatOk = null;
                    state.Stage = "Idle";
                    state.CurrentJob = "None";
                    state.Progress = 0;
                    state.CanStart = true;
                    state.CanStop = false;
                    state.CanCancelJob = false;
                });

                Log("Disconnected");
            }
            catch (Exception ex)
            {
                Log($"Error stopping worker: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        /// <summary>Refetch both queues and publish them.</summary>
        public async Task RefreshQueuesAsync(CancellationToken cancellationToken = default)
        {
            if (!_config.IsConfigured)
            {
                return;
            }

            try
            {
                var review = await _apiClient.GetReviewQueue(cancellationToken);
                var run = await _apiClient.GetRunQueue(cancellationToken);

                // Only on a change: this polls every ten seconds, and a log line
                // per poll would bury everything else.
                if (review.Count != _lastReviewCount || run.Count != _lastQueuedCount)
                {
                    _lastReviewCount = review.Count;
                    _lastQueuedCount = run.Count;
                    Log($"Queue: {review.Count} not started, {run.Count} queued");
                }

                QueueChanged?.Invoke(new QueueSnapshot { AwaitingReview = review, Queued = run });
            }
            catch (Exception ex)
            {
                Log($"Could not refresh the queue: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        /// <summary>
        /// Requeue every analyzed video that is not on the current analyzer
        /// version, so an algorithm change re-runs the whole corpus. Asks the
        /// analyzer for its version (single source of truth), then the backend to
        /// requeue anything else. Returns a short summary for the UI.
        /// </summary>
        public async Task<string> RequeueOutdatedAsync(CancellationToken cancellationToken = default)
        {
            if (!_config.IsConfigured)
            {
                Log("Worker not configured; cannot requeue.", Logger.LogLevel.Warning);
                return "Worker not configured.";
            }

            string version;
            try
            {
                version = await _analyzer.GetVersionAsync(cancellationToken);
            }
            catch (Exception ex)
            {
                Log($"Could not determine the analyzer version: {ex.Message}", Logger.LogLevel.Error);
                return "Could not determine the analyzer version.";
            }

            Log($"Requeuing every video not on analyzer version '{version}'...");
            var result = await _apiClient.RequeueStaleVersion(_config.WorkerId, version, cancellationToken);
            if (result == null)
            {
                Log("Requeue failed. See the log for the API error.", Logger.LogLevel.Error);
                return "Requeue failed.";
            }

            var summary = $"Requeued {result.Requeued} video(s); {result.SkippedCurrentVersion} already on '{version}'.";
            Log(summary);
            await RefreshQueuesAsync(cancellationToken);
            return summary;
        }

        /// <summary>
        /// Approve the given videos, in the order supplied, and start the worker.
        /// </summary>
        /// <remarks>
        /// Order is applied before approval so the queue reflects the order they
        /// were listed in, rather than whatever order the approvals happened to
        /// land in. Approving is what makes a video claimable, so an approval
        /// that fails simply leaves that video in review -- the rest still run.
        /// </remarks>
        public async Task<BatchResult> StartBatchAsync(
            IReadOnlyList<Guid> videoIds, CancellationToken cancellationToken = default)
        {
            var result = new BatchResult();

            if (videoIds.Count == 0)
            {
                Log("Nothing selected.", Logger.LogLevel.Warning);
                return result;
            }

            Log($"Starting batch of {videoIds.Count}...");

            // Send the whole intended running order -- what is already queued,
            // then this batch behind it -- rather than just the batch.
            //
            // Priorities descend from the length of whatever list is sent, so
            // reordering the batch alone numbers it 3,2,1 in the same space the
            // queued videos already occupy, and the new work interleaves with
            // work that was there first. Approving is not a request to jump the
            // queue.
            // Read the run queue rather than trusting a cached snapshot: another
            // host may have approved something since the last refresh, and this
            // write decides the order everything runs in.
            List<Guid> order;
            try
            {
                order = QueueOrdering.BatchOrder(
                    await _apiClient.GetRunQueue(cancellationToken), videoIds);
            }
            catch (Exception ex)
            {
                // Fall back to ordering the batch alone. Worse than ideal -- it
                // can interleave with queued work -- but better than not running.
                Log($"Could not read the run queue, ordering the batch only: {ex.Message}",
                    Logger.LogLevel.Warning);
                order = new List<Guid>(videoIds);
            }

            if (order.Count > 1 && !await _apiClient.ReorderQueue(order, cancellationToken))
            {
                // Not fatal: they will still run, just in the queue's existing order.
                Log("Could not set the batch order; the videos will run in queue order.",
                    Logger.LogLevel.Warning);
            }

            foreach (var videoId in videoIds)
            {
                if (await _apiClient.DecideApproval(videoId, approve: true, cancellationToken))
                {
                    result.Approved++;
                }
                else
                {
                    result.Failed++;
                    Log($"Could not approve {videoId}", Logger.LogLevel.Error);
                }
            }

            Log($"Batch: {result.Approved} started" +
                (result.Failed > 0 ? $", {result.Failed} failed" : ""));

            await RefreshQueuesAsync(cancellationToken);

            if (result.Approved > 0 && !IsRunning)
            {
                // Approving without starting would leave the batch sitting there,
                // which is not what "start" means.
                _ = StartAsync();
            }

            return result;
        }

        /// <summary>Reject the given videos so they are never analyzed.</summary>
        public async Task<BatchResult> RejectAsync(
            IReadOnlyList<Guid> videoIds, CancellationToken cancellationToken = default)
        {
            var result = new BatchResult();

            foreach (var videoId in videoIds)
            {
                if (await _apiClient.DecideApproval(videoId, approve: false, cancellationToken))
                {
                    result.Approved++;
                }
                else
                {
                    result.Failed++;
                }
            }

            Log($"Skipped {result.Approved} video(s)" +
                (result.Failed > 0 ? $", {result.Failed} failed" : ""));

            await RefreshQueuesAsync(cancellationToken);
            return result;
        }

        /// <summary>Apply a new run order to the approved queue.</summary>
        public async Task ReorderAsync(
            IReadOnlyList<Guid> videoIds, CancellationToken cancellationToken = default)
        {
            if (await _apiClient.ReorderQueue(videoIds, cancellationToken))
            {
                await RefreshQueuesAsync(cancellationToken);
            }
            else
            {
                Log("Could not reorder the queue.", Logger.LogLevel.Error);
            }
        }

        public async Task CancelCurrentJobAsync()
        {
            try
            {
                if (_loop != null)
                {
                    Log("Cancelling current job...");
                    await _loop.CancelCurrentJobAsync();
                }
            }
            catch (Exception ex)
            {
                Log($"Error cancelling job: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        private void HandleLoopEvent(WorkerLoopEvent evt)
        {
            Mutate(state =>
            {
                switch (evt.Status)
                {
                    case "idle":
                        state.Status = "Idle";
                        state.Stage = "Idle";
                        state.CurrentJob = "None";
                        // Clear the bar too. Without this it keeps the last
                        // job's value, so an idle worker sat at whatever
                        // percentage the previous job stopped reporting at.
                        state.Progress = 0;
                        state.CanCancelJob = false;
                        break;

                    case "processing":
                        state.Status = "Processing";
                        if (evt.JobTitle != null)
                        {
                            state.CurrentJob = evt.JobTitle;
                        }
                        if (evt.Stage != null)
                        {
                            state.Stage = evt.Stage;
                        }
                        state.Progress = evt.Progress;
                        state.CanCancelJob = true;
                        break;

                    case "error":
                        state.Status = "Error";
                        break;

                    case "heartbeat":
                        // Connection health only. It does not touch Status, so a
                        // beat arriving mid-job cannot overwrite "Processing".
                        break;

                    case "stopped":
                        state.Status = "Disconnected";
                        state.Progress = 0;
                        state.CanCancelJob = false;
                        state.HeartbeatOk = null;
                        break;
                }

                if (evt.HeartbeatOk.HasValue)
                {
                    state.HeartbeatOk = evt.HeartbeatOk;
                    // Only a real beat moves the timestamp. Stamping it on every
                    // event made "Last heartbeat" mean "last anything".
                    state.LastHeartbeat = DateTime.Now;
                }
            });

            if (!string.IsNullOrWhiteSpace(evt.Message))
            {
                Log(evt.Message);
            }
        }

        private void Mutate(Action<WorkerSessionState> mutation)
        {
            var next = State.Clone();
            mutation(next);
            State = next;
            StateChanged?.Invoke(next);
        }
    }

    public class WorkerSessionState
    {
        public string BackendUrl { get; set; } = "Not configured";
        public string Status { get; set; } = "Disconnected";
        public string Stage { get; set; } = "Idle";
        public string CurrentJob { get; set; } = "None";
        public int Progress { get; set; }
        public DateTime? LastHeartbeat { get; set; }
        /// <summary>Connection health for the indicator: true after a delivered
        /// heartbeat, false after a rejected one, null while disconnected.</summary>
        public bool? HeartbeatOk { get; set; }
        public bool IsConfigured { get; set; }
        public bool CanStart { get; set; }
        public bool CanStop { get; set; }
        public bool CanCancelJob { get; set; }

        public string LastHeartbeatDisplay =>
            LastHeartbeat.HasValue ? LastHeartbeat.Value.ToString("HH:mm:ss") : "Never";

        /// <summary>Green connected, red failing, grey disconnected.</summary>
        public string ConnectionColour => HeartbeatOk switch
        {
            true => "#27AE60",
            false => "#E74C3C",
            _ => "#B0B0B0",
        };

        public string ConnectionTooltip => HeartbeatOk switch
        {
            true => $"Connected. Last heartbeat {LastHeartbeatDisplay}.",
            false => "Connected, but the backend is rejecting heartbeats.",
            _ => "Disconnected.",
        };

        public string ProgressDisplay => $"{Progress}%";

        public WorkerSessionState Clone() => (WorkerSessionState)MemberwiseClone();
    }

    /// <summary>Both queues as of the last refresh.</summary>
    public class QueueSnapshot
    {
        public List<QueueEntry> AwaitingReview { get; set; } = new();
        public List<QueueEntry> Queued { get; set; } = new();
    }

    public class BatchResult
    {
        public int Approved { get; set; }
        public int Failed { get; set; }
    }

    public class WorkerLogEntry
    {
        public DateTime Timestamp { get; set; }
        public string Message { get; set; } = "";
        public Logger.LogLevel Level { get; set; }

        public string Display => $"[{Timestamp:HH:mm:ss}] {Message}";
    }
}
