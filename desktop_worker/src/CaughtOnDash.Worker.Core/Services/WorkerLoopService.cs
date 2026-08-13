using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    public class WorkerLoopService
    {
        private readonly WorkerApiClient _apiClient;
        private readonly IAnalyzer _analyzer;
        private readonly LocalVideoStorageService _storageService;
        private readonly WorkerConfig _config;

        private CancellationTokenSource? _cancellationTokenSource;
        private bool _isRunning;
        private Guid? _currentJobId;
        private TaskCompletionSource<bool>? _stopTcs;
        private volatile string _currentStage = "";
        // Whether the last heartbeat failed, so a broken connection logs once
        // rather than every ten seconds for as long as it stays broken.
        private volatile bool _heartbeatFailing;
        private volatile int _currentProgress;

        // Backend progress reporting is throttled to these bounds. See ReportProgress.
        private const int ProgressReportStepPercent = 10;
        private static readonly TimeSpan ProgressReportInterval = TimeSpan.FromSeconds(5);
        private string _lastSentStage = "";
        private int _lastSentProgress = -1;
        private DateTime _lastSentAt = DateTime.MinValue;

        // Set once the backend has been asked to finish a job. Progress
        // delivered through Progress<T> arrives asynchronously, so the
        // analyzer's last reports -- uploading_results at 90 and 95 -- can land
        // after CompleteJob has already returned. The progress endpoint
        // rightly refuses a completed job, which logged three
        // "Cannot update job in state: complete" errors on every successful
        // run. Those are the kind of routine errors that train you to ignore
        // the log, which is how a real failure hid here once before.
        private volatile bool _jobFinishing;

        public event Action<WorkerLoopEvent>? OnStatusUpdate;

        public WorkerLoopService(WorkerConfig config, WorkerApiClient apiClient, IAnalyzer analyzer, LocalVideoStorageService storageService)
        {
            _config = config;
            _apiClient = apiClient;
            _analyzer = analyzer;
            _storageService = storageService;
            _isRunning = false;
        }

        public bool IsRunning => _isRunning;
        public Guid? CurrentJobId => _currentJobId;

        public async Task StartAsync()
        {
            if (_isRunning)
            {
                Logger.Log("Worker already running");
                return;
            }

            _isRunning = true;
            _cancellationTokenSource = new CancellationTokenSource();
            _stopTcs = new TaskCompletionSource<bool>();

            // No log line: WorkerSession brackets this with "Connecting..." and
            // the heartbeat socket announces itself. This used to print twice --
            // once here and once from the Message below, which WorkerSession logs
            // for any event carrying one.
            OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "idle" });

            await MainLoop(_cancellationTokenSource.Token);
        }

        public async Task StopAsync()
        {
            if (!_isRunning)
            {
                return;
            }

            _cancellationTokenSource?.Cancel();
            
            if (_stopTcs != null)
            {
                await _stopTcs.Task;
            }

            _isRunning = false;
            _currentJobId = null;
            // Likewise: WorkerSession logs "Disconnected" once this returns.
            OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "stopped" });
        }

        public async Task CancelCurrentJobAsync()
        {
            if (!_currentJobId.HasValue)
            {
                Logger.Log("No job to cancel", Logger.LogLevel.Warning);
                return;
            }

            Logger.Log($"Cancelling job {_currentJobId}...");
            var reason = "Cancelled by user";
            await _apiClient.CancelJob(_currentJobId.Value, _config.WorkerId, reason, _cancellationTokenSource?.Token ?? CancellationToken.None);
            _currentJobId = null;
        }

        private async Task MainLoop(CancellationToken cancellationToken)
        {
            try
            {
                // Heartbeat timer
                var heartbeatTask = HeartbeatLoop(cancellationToken);
                
                // Job polling loop
                var jobLoopTask = JobProcessingLoop(cancellationToken);

                await Task.WhenAll(heartbeatTask, jobLoopTask);
            }
            catch (OperationCanceledException)
            {
                Logger.Log("Worker main loop cancelled");
            }
            catch (Exception ex)
            {
                Logger.Log($"Worker main loop error: {ex.Message}", Logger.LogLevel.Error);
                OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "error", Message = $"Error: {ex.Message}" });
            }
            finally
            {
                _stopTcs?.TrySetResult(true);
            }
        }

        private async Task HeartbeatLoop(CancellationToken cancellationToken)
        {
            // Held open across beats: the connection is the liveness signal, so
            // the backend learns this worker died when the socket closes rather
            // than after the stale window. Purely an optimisation -- every send
            // that fails falls through to the HTTP POST below.
            using var channel = new HeartbeatChannel(_config.BackendUrl, _config.ApiToken);

            try
            {
                while (!cancellationToken.IsCancellationRequested)
                {
                    try
                    {
                        // Report the stage and progress the job is actually at, not a
                        // fixed guess. These are set by ReportProgress as work moves.
                        var status = _currentJobId.HasValue ? "processing" : "idle";
                        var stage = _currentJobId.HasValue ? _currentStage : "";
                        var progress = _currentJobId.HasValue ? _currentProgress : 0;

                        var delivered = await channel.TrySendAsync(
                            _config.WorkerId,
                            status,
                            _currentJobId?.ToString(),
                            stage,
                            progress,
                            cancellationToken
                        );

                        var transport = delivered ? "socket" : "http";

                        if (!delivered)
                        {
                            delivered = await _apiClient.SendHeartbeat(
                                _config.WorkerId,
                                _config.WorkerName,
                                status,
                                _currentJobId?.ToString(),
                                stage,
                                progress,
                                cancellationToken
                            );
                        }

                        // A heartbeat is a state, not an event, so it is reported as
                        // one: the UI shows a coloured dot rather than two log lines
                        // every ten seconds. Only a failure is worth a log entry,
                        // and only the first of a run of them.
                        if (delivered)
                        {
                            if (_heartbeatFailing)
                            {
                                Logger.Log($"Heartbeat recovered via {transport}");
                            }
                            _heartbeatFailing = false;
                            OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "heartbeat", HeartbeatOk = true });
                        }
                        else
                        {
                            // Do not report success on a rejected heartbeat -- that is what
                            // hid the backend rejecting every worker POST. Reaching here
                            // means both transports failed, not just the socket.
                            if (!_heartbeatFailing)
                            {
                                Logger.Log("Heartbeat rejected by backend", Logger.LogLevel.Error);
                            }
                            _heartbeatFailing = true;
                            OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "heartbeat", HeartbeatOk = false });
                        }

                        await Task.Delay(10000, cancellationToken); // 10 seconds
                    }
                    catch (OperationCanceledException)
                    {
                        break;
                    }
                    catch (Exception ex)
                    {
                        Logger.Log($"Heartbeat error: {ex.Message}", Logger.LogLevel.Error);
                        await Task.Delay(5000, cancellationToken);
                    }
                }
            }
            finally
            {
                // Say goodbye so the backend marks this worker offline now
                // rather than inferring it from silence two minutes later.
                await channel.CloseAsync();
            }
        }

        private async Task JobProcessingLoop(CancellationToken cancellationToken)
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    if (_currentJobId.HasValue)
                    {
                        // Already processing a job
                        await Task.Delay(1000, cancellationToken);
                        continue;
                    }

                    // Poll for next job
                    var job = await _apiClient.GetNextPendingJob(cancellationToken);
                    if (job == null)
                    {
                        await Task.Delay(15000, cancellationToken); // 15 seconds
                        continue;
                    }

                    Logger.Log($"Found pending job: {job.JobId} - {job.Title}");
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "job_found", Message = $"Found job: {job.Title}" });

                    // Try to claim the job
                    var claimed = await _apiClient.ClaimJob(job.JobId, _config.WorkerId, _config.WorkerName, cancellationToken);
                    if (!claimed)
                    {
                        // Back off before retrying: another worker may hold the job,
                        // and retrying immediately spins the loop against the server.
                        Logger.Log($"Failed to claim job {job.JobId}");
                        OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "claim_failed" });
                        await Task.Delay(5000, cancellationToken);
                        continue;
                    }

                    _currentJobId = job.JobId;
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "processing", JobId = job.JobId, JobTitle = job.Title });

                    // Process the job
                    await ProcessJob(job, cancellationToken);

                    _currentJobId = null;
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "idle" });
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    Logger.Log($"Job processing loop error: {ex.Message}", Logger.LogLevel.Error);
                    OnStatusUpdate?.Invoke(new WorkerLoopEvent { Status = "error", Message = ex.Message });
                    _currentJobId = null;
                    await Task.Delay(5000, cancellationToken);
                }
            }
        }

        private async Task ProcessJob(JobDto job, CancellationToken cancellationToken)
        {
            // Set before the download so the finally block cleans up a partial
            // file if the download is cancelled or fails part-way through.
            var downloadedPath = _storageService.GetVideoPath(job.VideoId);
            var stage = "downloading";
            ResetProgressThrottle();
            _jobFinishing = false;

            try
            {
                var progress = new Progress<(string stage, int progressValue)>(report =>
                    ReportProgress(job, report.stage, report.progressValue, cancellationToken));

                ReportProgress(job, stage, 0, cancellationToken);
                await _apiClient.DownloadVideo(
                    job.VideoUrl,
                    downloadedPath,
                    new Progress<int>(percent => ReportProgress(job, "downloading", percent, cancellationToken)),
                    cancellationToken);

                stage = "analyzing";
                var result = await _analyzer.AnalyzeAsync(downloadedPath, progress, cancellationToken);

                stage = "uploading_results";
                ReportProgress(job, stage, 95, cancellationToken);

                // From here the job's progress is the backend's to record.
                // Anything still queued in Progress<T> is stale by definition.
                _jobFinishing = true;

                var success = await _apiClient.CompleteJob(job.JobId, _config.WorkerId, result, cancellationToken);
                if (success)
                {
                    // Close the bar out locally. Deliberately not sent to the
                    // backend: complete_job already recorded 100, and the
                    // progress endpoint rejects a job that is already complete,
                    // so reporting it would log an error on every success.
                    stage = "complete";
                    ReportProgress(job, stage, 100, cancellationToken, notifyBackend: false);
                }
                else
                {
                    await _apiClient.FailJob(job.JobId, _config.WorkerId, "Failed to submit analysis results", stage, cancellationToken);
                }
            }
            catch (OperationCanceledException)
            {
                Logger.Log($"Job {job.JobId} processing cancelled by user");
                // Cancellation token is already tripped, so report on a fresh one
                // or the cancel call itself is cancelled before it is sent.
                await _apiClient.CancelJob(job.JobId, _config.WorkerId, "User cancelled processing", CancellationToken.None);
            }
            catch (Exception ex)
            {
                Logger.Log($"Job {job.JobId} processing failed: {ex.Message}", Logger.LogLevel.Error);
                await _apiClient.FailJob(job.JobId, _config.WorkerId, ex.Message, stage, cancellationToken);
            }
            finally
            {
                _storageService.CleanupVideoFile(downloadedPath);
            }
        }

        /// <summary>
        /// Push progress to the UI, the backend, and the heartbeat.
        /// </summary>
        /// <remarks>
        /// The UI is updated on every report -- it is local and free. The backend
        /// call is throttled, because a fast download emits a report per percent
        /// and each one costs an HTTP request and a row-locking transaction. Left
        /// unthrottled this produced ~77 writes in under a second and SQLite
        /// answered "database is locked".
        ///
        /// The call is fire-and-forget: a dropped progress update must not fail
        /// the job, and blocking analysis on it would be worse than losing one.
        /// </remarks>
        private void ReportProgress(
            JobDto job, string stage, int percent, CancellationToken cancellationToken,
            bool notifyBackend = true)
        {
            var stageChanged = _currentStage != stage;
            _currentStage = stage;
            _currentProgress = percent;

            // Only stage changes reach the activity log. Logging every percent
            // put ~117 lines per job into it -- 3,464 lines for an 18-job batch,
            // of which about 180 were worth reading. The live percentage belongs
            // on the progress bar, which is fed by the event below and updates
            // in place instead of scrolling the interesting lines away.
            if (stageChanged)
            {
                Logger.Log($"[{job.Title}] {stage}");
            }

            OnStatusUpdate?.Invoke(new WorkerLoopEvent
            {
                Status = "processing",
                // No Message: WorkerSession logs any Message it receives, and
                // "Processing: {stage}" duplicated the line above without the
                // title or the percentage. It was half of all log volume.
                JobId = job.JobId,
                JobTitle = job.Title,
                Progress = percent,
                Stage = stage
            });

            if (notifyBackend && !_jobFinishing && ShouldSendProgress(stage, percent))
            {
                _lastSentStage = stage;
                _lastSentProgress = percent;
                _lastSentAt = DateTime.UtcNow;
                _ = _apiClient.UpdateJobProgress(job.JobId, _config.WorkerId, stage, percent, cancellationToken);
            }
        }

        private bool ShouldSendProgress(string stage, int percent)
        {
            // Always report a stage change or the end of one: those are the
            // transitions a watching user actually cares about.
            if (stage != _lastSentStage || percent >= 100 || percent == 0)
            {
                return true;
            }

            if (percent - _lastSentProgress >= ProgressReportStepPercent)
            {
                return true;
            }

            // A slow analysis can sit on the same percentage for a long time;
            // a periodic update keeps the job visibly alive.
            return DateTime.UtcNow - _lastSentAt >= ProgressReportInterval;
        }

        private void ResetProgressThrottle()
        {
            _lastSentStage = "";
            _lastSentProgress = -1;
            _lastSentAt = DateTime.MinValue;
        }
    }

    public class WorkerLoopEvent
    {
        public string Status { get; set; } = "";
        public string Message { get; set; } = "";
        public Guid? JobId { get; set; }
        public string? JobTitle { get; set; }
        public int Progress { get; set; }
        public string? Stage { get; set; }
        /// <summary>Whether the last heartbeat reached the backend, for the
        /// connection indicator. Null on events that say nothing about it.</summary>
        public bool? HeartbeatOk { get; set; }
    }
}
