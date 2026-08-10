using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using CaughtOnDash.Worker.Models;
using CaughtOnDash.Worker.Services;

namespace CaughtOnDash.Worker.ViewModels
{
    /// <summary>
    /// Drives the WPF window from the shared WorkerSession.
    /// </summary>
    /// <remarks>
    /// This used to own a WorkerLoopService directly and duplicate the state
    /// handling the Avalonia host already had in WorkerSession. Everything
    /// meaningful now lives in Core, so the two hosts cannot drift: this class
    /// only renders state and forwards clicks.
    /// </remarks>
    public class MainViewModel
    {
        private const int MaxLogRows = 100;

        private readonly MainWindow _mainWindow;
        private readonly WorkerSession _session;
        private readonly ObservableCollection<QueueRow> _rows = new();

        private QueueSnapshot _snapshot = new();
        private DispatcherTimer? _queuePollTimer;
        private bool _showingReviewQueue = true;
        private readonly ThumbnailCache _thumbnails = new();

        public MainViewModel(MainWindow mainWindow)
        {
            _mainWindow = mainWindow;

            _session = new WorkerSession();
            _session.StateChanged += OnStateChanged;
            _session.LogAppended += OnLogAppended;
            _session.QueueChanged += OnQueueChanged;

            _mainWindow.QueueGrid.ItemsSource = _rows;
            RebuildRows();
            Render(_session.State);

            AddLog("Application started");

            if (!_session.IsConfigured)
            {
                AddLog("Worker config is missing backend URL or API token.", Logger.LogLevel.Error);
                return;
            }

            _ = _session.RefreshQueuesAsync();
            StartQueuePolling();
        }

        /// <summary>
        /// Keep the queue roughly current without hammering the backend.
        /// </summary>
        /// <remarks>
        /// Ten seconds is a compromise: uploads arrive rarely, but a batch you
        /// just started should visibly drain. Phase 3 replaces this with pushed
        /// updates.
        /// </remarks>
        private void StartQueuePolling()
        {
            _queuePollTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(10) };
            _queuePollTimer.Tick += (_, _) => _ = _session.RefreshQueuesAsync();
            _queuePollTimer.Start();
        }

        // ---- worker controls ----

        public Task StartWorker() => _session.StartAsync();

        public async void StopWorker() => await _session.StopAsync();

        public async void CancelCurrentJob() => await _session.CancelCurrentJobAsync();

        /// <summary>
        /// Kept for the window's startup call. It no longer starts the worker:
        /// with an approval gate, starting before anything is approved just
        /// polls an empty queue.
        /// </summary>
        public Task StartAutomaticallyAsync() => _session.RefreshQueuesAsync();

        // ---- queue ----

        private void OnQueueChanged(QueueSnapshot snapshot)
        {
            _mainWindow.Dispatcher.Invoke(() =>
            {
                _snapshot = snapshot;
                RebuildRows();
            });
        }

        /// <summary>
        /// Repopulate the table, preserving ticks across a refresh.
        /// </summary>
        /// <remarks>
        /// A poll every ten seconds that silently cleared your selection would
        /// make choosing a large batch impossible.
        /// </remarks>
        private void RebuildRows()
        {
            var selected = new HashSet<Guid>();
            foreach (var row in _rows)
            {
                if (row.IsSelected)
                {
                    selected.Add(row.Entry.VideoId);
                }
            }

            var entries = _showingReviewQueue ? _snapshot.AwaitingReview : _snapshot.Queued;

            _rows.Clear();
            foreach (var entry in entries)
            {
                var row = new QueueRow(entry) { IsSelected = selected.Contains(entry.VideoId) };
                _rows.Add(row);
                _ = LoadThumbnail(row);
            }

            _mainWindow.QueueHeading.Text = _showingReviewQueue ? "Review Queue" : "Run Queue";
            _mainWindow.QueueCountText.Text = _rows.Count == 0
                ? (_showingReviewQueue ? "Nothing awaiting review" : "Queue empty")
                : $"{_rows.Count} video{(_rows.Count == 1 ? "" : "s")}";

            // Reordering works on both tabs: arrange the review list, tick a
            // batch, and Start Batch runs it in that order. Priority is stored
            // per video regardless of approval state, and approving does not
            // reset it, so the order set here survives the decision.
            _mainWindow.MoveUpButton.IsEnabled = true;
            _mainWindow.MoveDownButton.IsEnabled = true;
            // Start Batch works on both tabs. It was review-only on the
            // reasoning that approving is a no-op for already-approved videos
            // and the Status panel's Start button covers the queue -- which is
            // true of the implementation and useless to the person looking at
            // a list of queued videos with no way to say "run these". The
            // control that starts them lived in another panel under another
            // name. Approving an approved video is idempotent, so this is
            // safe; on the queued tab it means "run these next".
            _mainWindow.StartBatchButton.IsEnabled = true;

            // Reject stays review-only: rejecting is a decision about whether
            // a video should be analyzed at all, which has already been made
            // for anything in the run queue.
            _mainWindow.RejectButton.IsEnabled = _showingReviewQueue;

            _mainWindow.ReviewTabButton.FontWeight =
                _showingReviewQueue ? FontWeights.Bold : FontWeights.Normal;
            _mainWindow.QueuedTabButton.FontWeight =
                _showingReviewQueue ? FontWeights.Normal : FontWeights.Bold;
        }

        /// <summary>
        /// Fill in a row's poster frame once it arrives.
        /// </summary>
        /// <remarks>
        /// Fire-and-forget on purpose: the table must render immediately and
        /// fill in as images land. The bitmap is frozen so it can be handed to
        /// the UI thread from here -- an unfrozen BitmapImage belongs to the
        /// thread that created it, and binding it elsewhere throws.
        /// </remarks>
        private async Task LoadThumbnail(QueueRow row)
        {
            var bytes = await _thumbnails.GetAsync(row.Entry.ThumbnailUrl);
            if (bytes == null)
            {
                return;
            }

            try
            {
                var bitmap = new BitmapImage();
                using (var stream = new System.IO.MemoryStream(bytes))
                {
                    bitmap.BeginInit();
                    bitmap.CacheOption = BitmapCacheOption.OnLoad;
                    bitmap.StreamSource = stream;
                    bitmap.EndInit();
                }
                bitmap.Freeze();

                _mainWindow.Dispatcher.Invoke(() => row.Thumbnail = bitmap);
            }
            catch (Exception)
            {
                // Not an image, or one WPF cannot decode. The placeholder
                // stands; a broken thumbnail must not disturb the queue.
            }
        }

        public void ShowReviewQueue()
        {
            _showingReviewQueue = true;
            RebuildRows();
        }

        public void ShowRunQueue()
        {
            _showingReviewQueue = false;
            RebuildRows();
        }

        public async void RefreshQueues() => await _session.RefreshQueuesAsync();

        /// <summary>
        /// Requeue every video not on the current analyzer version. The outcome
        /// is written to the Activity Log by the session.
        /// </summary>
        public async Task RequeueOutdatedAsync() => await _session.RequeueOutdatedAsync();

        public void SelectAll()
        {
            foreach (var row in _rows)
            {
                row.IsSelected = true;
            }
        }

        public void ClearSelection()
        {
            foreach (var row in _rows)
            {
                row.IsSelected = false;
            }
        }

        private List<Guid> SelectedIds()
        {
            var ids = new List<Guid>();
            foreach (var row in _rows)
            {
                if (row.IsSelected)
                {
                    ids.Add(row.Entry.VideoId);
                }
            }
            return ids;
        }

        /// <summary>Open the highlighted video so it can be judged before approving.</summary>
        public void PreviewSelected()
        {
            var row = _mainWindow.QueueGrid.SelectedItem as QueueRow
                      ?? (_rows.Count > 0 ? _rows[0] : null);

            if (row == null || string.IsNullOrWhiteSpace(row.Entry.VideoUrl))
            {
                AddLog("Nothing to preview -- that video has no playback URL.", Logger.LogLevel.Warning);
                return;
            }

            try
            {
                Process.Start(new ProcessStartInfo(row.Entry.VideoUrl) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                AddLog($"Could not open the video: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        public async void MoveSelectedUp() => await Move(-1);

        public async void MoveSelectedDown() => await Move(1);

        /// <summary>
        /// Move the ticked rows one place, then send the whole order to the
        /// backend -- priority lives server-side so every host agrees on it.
        /// </summary>
        private async Task Move(int direction)
        {
            var order = new List<QueueRow>(_rows);
            var indexes = new List<int>();
            for (var i = 0; i < order.Count; i++)
            {
                if (order[i].IsSelected)
                {
                    indexes.Add(i);
                }
            }

            if (indexes.Count == 0)
            {
                AddLog("Tick a row first.", Logger.LogLevel.Warning);
                return;
            }

            // Walk from the edge the rows are moving toward, so a block of
            // adjacent selections shifts together instead of collapsing.
            if (direction < 0)
            {
                foreach (var index in indexes)
                {
                    if (index == 0) break;
                    (order[index - 1], order[index]) = (order[index], order[index - 1]);
                }
            }
            else
            {
                indexes.Reverse();
                foreach (var index in indexes)
                {
                    if (index >= order.Count - 1) break;
                    (order[index + 1], order[index]) = (order[index], order[index + 1]);
                }
            }

            _rows.Clear();
            foreach (var row in order)
            {
                _rows.Add(row);
            }

            var ids = new List<Guid>();
            foreach (var row in order)
            {
                ids.Add(row.Entry.VideoId);
            }

            // Shared with the Avalonia host so the two cannot disagree: send one
            // order spanning both tabs, or reordering one renumbers it into the
            // other's priority band.
            await _session.ReorderAsync(QueueOrdering.GlobalOrder(
                _snapshot.Queued, _snapshot.AwaitingReview, ids, _showingReviewQueue));
        }

        public async void StartBatch()
        {
            var ids = SelectedIds();
            if (ids.Count == 0)
            {
                AddLog("Tick the videos you want to run first.", Logger.LogLevel.Warning);
                return;
            }

            _mainWindow.StartBatchButton.IsEnabled = false;
            try
            {
                await _session.StartBatchAsync(ids);
            }
            finally
            {
                _mainWindow.StartBatchButton.IsEnabled = _showingReviewQueue;
            }
        }

        public async void RejectSelected()
        {
            var ids = SelectedIds();
            if (ids.Count == 0)
            {
                AddLog("Tick the videos you want to reject first.", Logger.LogLevel.Warning);
                return;
            }

            await _session.RejectAsync(ids);
        }

        // ---- status ----

        private void OnStateChanged(WorkerSessionState state)
            => _mainWindow.Dispatcher.Invoke(() => Render(state));

        private void Render(WorkerSessionState state)
        {
            _mainWindow.StatusText.Text = state.Status;
            _mainWindow.StatusText.Foreground = state.Status switch
            {
                "Processing" => Brushes.RoyalBlue,
                "Idle" => Brushes.Gray,
                "Error" => Brushes.Red,
                "Missing config" => Brushes.Red,
                _ => Brushes.Gray,
            };

            _mainWindow.BackendUrlDisplay.Text = state.BackendUrl;
            _mainWindow.LastHeartbeatText.Text = state.LastHeartbeatDisplay;
            _mainWindow.CurrentJobText.Text = state.CurrentJob;
            _mainWindow.StageText.Text = state.Stage;
            _mainWindow.ProgressBar.Value = state.Progress;
            _mainWindow.ProgressText.Text = state.ProgressDisplay;

            _mainWindow.JobDetailsText.Text = state.CurrentJob == "None"
                ? "No active job"
                : $"{state.CurrentJob}\nStage: {state.Stage}\nProgress: {state.ProgressDisplay}";

            // Show the progress card only while a job is actually running. A bar
            // sitting at 0% reads as stuck; nothing there reads as nothing running.
            var isProcessing = state.Status == "Processing";
            _mainWindow.ProcessingPanel.Visibility = isProcessing ? Visibility.Visible : Visibility.Collapsed;
            _mainWindow.IdlePanel.Visibility = isProcessing ? Visibility.Collapsed : Visibility.Visible;
            _mainWindow.IdleText.Text = state.IsConfigured
                ? state.CanStop ? "Waiting for a job." : "Worker stopped."
                : "Worker is not configured.";

            _mainWindow.StartButton.IsEnabled = state.CanStart;
            _mainWindow.StopButton.IsEnabled = state.CanStop;
            _mainWindow.CancelJobButton.IsEnabled = state.CanCancelJob;
        }

        private void AddLog(string message, Logger.LogLevel level = Logger.LogLevel.Info)
            => _session.Log(message, level);

        private void OnLogAppended(WorkerLogEntry entry)
        {
            _mainWindow.Dispatcher.Invoke(() =>
            {
                var item = new System.Windows.Controls.ListBoxItem
                {
                    Content = new System.Windows.Controls.TextBlock
                    {
                        Text = entry.Display,
                        TextWrapping = TextWrapping.Wrap,
                        Foreground = entry.Level switch
                        {
                            Logger.LogLevel.Error => Brushes.Red,
                            Logger.LogLevel.Warning => Brushes.Orange,
                            _ => Brushes.Black,
                        }
                    }
                };

                _mainWindow.LogListBox.Items.Add(item);
                _mainWindow.LogListBox.ScrollIntoView(item);

                while (_mainWindow.LogListBox.Items.Count > MaxLogRows)
                {
                    _mainWindow.LogListBox.Items.RemoveAt(0);
                }
            });
        }
    }
}
