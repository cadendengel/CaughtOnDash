using System;
using System.Collections.Generic;
using System.IO;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Models;
using Newtonsoft.Json;

namespace CaughtOnDash.Worker.Services
{
    public class WorkerApiClient
    {
        private readonly HttpClient _httpClient;
        private string _backendUrl = "";
        private string _apiToken = "";

        public WorkerApiClient()
        {
            _httpClient = new HttpClient();
        }

        public void Initialize(string backendUrl, string apiToken)
        {
            _backendUrl = backendUrl.TrimEnd('/');
            _apiToken = apiToken;
            Logger.Log($"API Client initialized with backend: {_backendUrl}");
        }

        private string GetAuthorizationHeader()
        {
            return $"Bearer {_apiToken}";
        }

        private async Task<T?> SendRequest<T>(string method, string endpoint, object? body = null, CancellationToken cancellationToken = default)
        {
            try
            {
                var url = $"{_backendUrl}{endpoint}";
                var request = new HttpRequestMessage(new HttpMethod(method), url);
                request.Headers.Add("Authorization", GetAuthorizationHeader());

                if (body != null)
                {
                    var json = JsonConvert.SerializeObject(body);
                    request.Content = new StringContent(json, Encoding.UTF8, "application/json");
                }

                var response = await _httpClient.SendAsync(request, cancellationToken);

                if (!response.IsSuccessStatusCode)
                {
                    var errorContent = await response.Content.ReadAsStringAsync(cancellationToken);
                    Logger.Log($"API Error ({response.StatusCode}): {errorContent}", Logger.LogLevel.Error);
                    return default;
                }

                var content = await response.Content.ReadAsStringAsync(cancellationToken);
                var result = JsonConvert.DeserializeObject<T>(content);
                return result;
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to send request to {endpoint}: {ex.Message}", Logger.LogLevel.Error);
                return default;
            }
        }

        /// <summary>
        /// Stream a video to disk, reporting 0-100 as it goes.
        /// </summary>
        /// <remarks>
        /// The file is written as it arrives rather than buffered in memory --
        /// dashcam clips are large enough that ReadAsByteArrayAsync would be a
        /// problem. No Authorization header: playback URLs point at Supabase
        /// public storage, not at our API.
        ///
        /// Returns the SHA-256 of the bytes written, hashed as they stream past so
        /// the file is never read twice. The backend compares it with the hash it
        /// took at upload: a match proves the analysis ran on the uploaded file.
        /// </remarks>
        public async Task<string> DownloadVideo(
            string videoUrl,
            string destinationPath,
            IProgress<int>? progress = null,
            CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(videoUrl))
            {
                throw new InvalidOperationException(
                    "This job has no video_url, so there is nothing to analyze. The upload " +
                    "probably never completed.");
            }

            using var response = await _httpClient.GetAsync(
                videoUrl, HttpCompletionOption.ResponseHeadersRead, cancellationToken);

            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException(
                    $"Could not download the video ({(int)response.StatusCode} {response.ReasonPhrase}) from {videoUrl}");
            }

            var totalBytes = response.Content.Headers.ContentLength;
            var directory = Path.GetDirectoryName(destinationPath);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }

            using var source = await response.Content.ReadAsStreamAsync(cancellationToken);
            using var destination = new FileStream(
                destinationPath, FileMode.Create, FileAccess.Write, FileShare.None,
                bufferSize: 81920, useAsync: true);

            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            var buffer = new byte[81920];
            long received = 0;
            var lastReported = -1;
            int read;

            while ((read = await source.ReadAsync(buffer, cancellationToken)) > 0)
            {
                await destination.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
                hash.AppendData(buffer, 0, read);
                received += read;

                // Without Content-Length we cannot compute a percentage, so hold
                // at 0 rather than inventing one.
                if (progress == null || totalBytes is null or <= 0)
                {
                    continue;
                }

                var percent = (int)(received * 100 / totalBytes.Value);
                if (percent != lastReported)
                {
                    lastReported = percent;
                    progress.Report(percent);
                }
            }

            if (received == 0)
            {
                throw new InvalidOperationException($"Downloaded an empty file from {videoUrl}");
            }

            var sha256 = Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
            Logger.Log($"Downloaded {received:N0} bytes to {destinationPath} (sha256 {sha256})");
            return sha256;
        }

        public async Task<bool> GetWorkerStatus(CancellationToken cancellationToken = default)
        {
            var result = await SendRequest<WorkerStatus>("GET", "/api/videos/worker/status/", cancellationToken: cancellationToken);
            return result != null;
        }

        public async Task<bool> SendHeartbeat(string workerId, string workerName, string status, string? currentJobId = null, string stage = "", int progress = 0, CancellationToken cancellationToken = default)
        {
            var heartbeat = new
            {
                worker_id = workerId,
                worker_name = workerName,
                status = status,
                current_job_id = currentJobId,
                stage = stage,
                progress = progress
            };

            var result = await SendRequest<dynamic>("POST", "/api/videos/worker/heartbeat/", heartbeat, cancellationToken);
            return result != null;
        }

        /// <summary>Videos waiting for someone to approve or reject them.</summary>
        public Task<List<QueueEntry>> GetReviewQueue(CancellationToken cancellationToken = default)
            => GetQueue("/api/videos/worker/jobs/review/", cancellationToken);

        /// <summary>Approved videos, in the order they will run.</summary>
        public Task<List<QueueEntry>> GetRunQueue(CancellationToken cancellationToken = default)
            => GetQueue("/api/videos/worker/jobs/", cancellationToken);

        private async Task<List<QueueEntry>> GetQueue(string endpoint, CancellationToken cancellationToken)
        {
            var result = await SendRequest<QueueResponse>("GET", endpoint, cancellationToken: cancellationToken);
            return result?.Items ?? new List<QueueEntry>();
        }

        /// <summary>Approve or reject a video for analysis.</summary>
        public async Task<bool> DecideApproval(
            Guid videoId, bool approve, CancellationToken cancellationToken = default)
        {
            var result = await SendRequest<dynamic>(
                "POST",
                $"/api/videos/worker/jobs/{videoId}/approval/",
                new { approve },
                cancellationToken);

            return result != null;
        }

        /// <summary>Set the order the queue will run in.</summary>
        public async Task<bool> ReorderQueue(
            IEnumerable<Guid> videoIds, CancellationToken cancellationToken = default)
        {
            var result = await SendRequest<dynamic>(
                "POST",
                "/api/videos/worker/jobs/reorder/",
                new { video_ids = videoIds.Select(id => id.ToString()).ToList() },
                cancellationToken);

            return result != null;
        }

        private class QueueResponse
        {
            [JsonProperty("count")]
            public int Count { get; set; }

            [JsonProperty("items")]
            public List<QueueEntry> Items { get; set; } = new();
        }

        public class RequeueResult
        {
            [JsonProperty("requeued")]
            public int Requeued { get; set; }

            [JsonProperty("skipped_current_version")]
            public int SkippedCurrentVersion { get; set; }

            [JsonProperty("target_version")]
            public string TargetVersion { get; set; } = "";
        }

        /// <summary>
        /// Ask the backend to requeue every analyzed video NOT on
        /// <paramref name="analyzerVersion"/>, so an algorithm change can re-run
        /// the whole corpus. Returns null on failure. The version is the
        /// analyzer's own (from analyze.py --version), keeping it the single
        /// source of truth.
        /// </summary>
        public async Task<RequeueResult?> RequeueStaleVersion(
            string workerId, string analyzerVersion, CancellationToken cancellationToken = default)
        {
            return await SendRequest<RequeueResult>(
                "POST",
                "/api/videos/worker/jobs/requeue-stale/",
                new { worker_id = workerId, analyzer_version = analyzerVersion },
                cancellationToken);
        }

        public class ResetStaleResult
        {
            [JsonProperty("reset_count")]
            public int ResetCount { get; set; }
        }

        /// <summary>Free jobs whose worker died mid-analysis.</summary>
        /// <remarks>
        /// A crashed worker leaves its video claimed and "processing" forever:
        /// requeue skips that state so it cannot take a job from a worker that is
        /// genuinely running one, and nothing else clears it.
        /// </remarks>
        public async Task<ResetStaleResult?> ResetStaleJobs(
            int timeoutMinutes, CancellationToken cancellationToken = default)
        {
            return await SendRequest<ResetStaleResult>(
                "POST",
                $"/api/videos/worker/jobs/reset-stale/?timeout_minutes={timeoutMinutes}",
                cancellationToken: cancellationToken);
        }

        public async Task<JobDto?> GetNextPendingJob(CancellationToken cancellationToken = default)
        {
            try
            {
                var result = await SendRequest<dynamic>("GET", "/api/videos/worker/jobs/next/", cancellationToken: cancellationToken);
                if (result == null)
                    return null;

                var jobData = result["job"];
                if (jobData == null)
                    return null;

                var job = JsonConvert.DeserializeObject<JobDto>(jobData.ToString());
                return job;
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to get next pending job: {ex.Message}", Logger.LogLevel.Error);
                return null;
            }
        }

        public async Task<bool> ClaimJob(Guid jobId, string workerId, string workerName, CancellationToken cancellationToken = default)
        {
            var claimRequest = new
            {
                worker_id = workerId,
                worker_name = workerName
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/claim/", claimRequest, cancellationToken);
            if (result?["success"] == true)
            {
                Logger.Log($"Successfully claimed job {jobId}");
                return true;
            }

            Logger.Log($"Failed to claim job {jobId}: {result?["error"]}", Logger.LogLevel.Error);
            return false;
        }

        public async Task<bool> UpdateJobProgress(Guid jobId, string workerId, string stage, int progress, CancellationToken cancellationToken = default)
        {
            var updateRequest = new
            {
                worker_id = workerId,
                stage = stage,
                progress = progress
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/progress/", updateRequest, cancellationToken);
            return result?["success"] == true;
        }

        public async Task<bool> CompleteJob(
            Guid jobId, string workerId, AnalysisResult analysisResult, string sourceSha256 = "",
            CancellationToken cancellationToken = default)
        {
            var completeRequest = new
            {
                worker_id = workerId,
                summary = analysisResult.Summary,
                tags = analysisResult.Tags,
                events = analysisResult.Events,
                metadata = analysisResult.Metadata,
                // The chain-of-custody check. Sent beside the metadata, not in
                // it: metadata is public, and this is between worker and backend.
                source_sha256 = sourceSha256,
                // Provenance, for the same reason. Stored in the private
                // evidence record, never with the video.
                @private = analysisResult.PrivateData
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/complete/", completeRequest, cancellationToken);
            if (result?["success"] == true)
            {
                Logger.Log($"Successfully completed job {jobId}");
                return true;
            }

            Logger.Log($"Failed to complete job {jobId}", Logger.LogLevel.Error);
            return false;
        }

        /// <summary>
        /// Send one evidence image to the backend's private store.
        /// </summary>
        /// <remarks>
        /// The worker's SHA-256 travels with the file and the backend recomputes
        /// it, so an image damaged in transit is refused rather than stored as
        /// evidence. Returns false on any failure: a missing image is reported,
        /// not fatal to the job.
        /// </remarks>
        public async Task<bool> UploadArtifact(
            Guid jobId, string workerId, AnalysisArtifact artifact, string analyzerVersion,
            CancellationToken cancellationToken = default)
        {
            try
            {
                var bytes = await File.ReadAllBytesAsync(artifact.Path, cancellationToken);
                var sha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

                using var form = new MultipartFormDataContent();
                form.Add(new StringContent(workerId), "worker_id");
                form.Add(new StringContent(artifact.Kind), "kind");
                form.Add(new StringContent(artifact.Label ?? ""), "label");
                form.Add(new StringContent(sha256), "sha256");
                form.Add(new StringContent(analyzerVersion ?? ""), "analyzer_version");
                form.Add(new StringContent(artifact.Width.ToString(CultureInfo.InvariantCulture)), "width");
                form.Add(new StringContent(artifact.Height.ToString(CultureInfo.InvariantCulture)), "height");
                if (artifact.TSeconds is double seconds)
                {
                    form.Add(new StringContent(seconds.ToString(CultureInfo.InvariantCulture)), "t_seconds");
                }

                var file = new ByteArrayContent(bytes);
                file.Headers.ContentType = new MediaTypeHeaderValue(ArtifactUploads.ContentTypeFor(artifact.Path));
                form.Add(file, "file", Path.GetFileName(artifact.Path));

                using var request = new HttpRequestMessage(
                    HttpMethod.Post, $"{_backendUrl}/api/videos/worker/jobs/{jobId}/artifacts/");
                request.Headers.Add("Authorization", GetAuthorizationHeader());
                request.Content = form;

                using var response = await _httpClient.SendAsync(request, cancellationToken);
                if (!response.IsSuccessStatusCode)
                {
                    var error = await response.Content.ReadAsStringAsync(cancellationToken);
                    Logger.Log($"Artifact {artifact.Kind} rejected ({(int)response.StatusCode}): {error}", Logger.LogLevel.Warning);
                    return false;
                }

                Logger.Log($"Uploaded {artifact.Kind} ({bytes.Length:N0} bytes, sha256 {sha256})");
                return true;
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to upload artifact {artifact.Path}: {ex.Message}", Logger.LogLevel.Warning);
                return false;
            }
        }

        public async Task<bool> FailJob(Guid jobId, string workerId, string error, string stage = "", CancellationToken cancellationToken = default)
        {
            var failRequest = new
            {
                worker_id = workerId,
                error = error,
                stage = stage
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/fail/", failRequest, cancellationToken);
            if (result?["success"] == true)
            {
                Logger.Log($"Marked job {jobId} as failed: {error}");
                return true;
            }

            Logger.Log($"Failed to mark job {jobId} as failed", Logger.LogLevel.Error);
            return false;
        }

        public async Task<bool> CancelJob(Guid jobId, string workerId, string reason = "", CancellationToken cancellationToken = default)
        {
            var cancelRequest = new
            {
                worker_id = workerId,
                reason = reason
            };

            var result = await SendRequest<dynamic>("POST", $"/api/videos/worker/jobs/{jobId}/cancel/", cancelRequest, cancellationToken);
            if (result?["success"] == true)
            {
                Logger.Log($"Successfully cancelled job {jobId}");
                return true;
            }

            Logger.Log($"Failed to cancel job {jobId}", Logger.LogLevel.Error);
            return false;
        }
    }
}
