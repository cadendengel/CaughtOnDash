using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Data;
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
        private readonly ThumbnailCache _thumbnails = new();

        public MainViewModel(MainWindow mainWindow)
        {
            _mainWindow = mainWindow;

            _session = new WorkerSession();
            _session.StateChanged += OnStateChanged;
            _session.LogAppended += OnLogAppended;
            _session.QueueChanged += OnQueueChanged;

            // One list, grouped by band. Groups appear in the order their first
            // row does, which QueueList.Build makes the order things happen:
            // running, queued, needs review, failed.
            var view = CollectionViewSource.GetDefaultView(_rows);
            view.GroupDescriptions.Add(new PropertyGroupDescription(nameof(QueueRow.GroupHeader)));
            _mainWindow.QueueGrid.ItemsSource = view;

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
        /// just started should visibly drain. Each poll is one request.
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
        /// Repopulate the list, preserving ticks and the highlighted row across
        /// a refresh.
        /// </summary>
        /// <remarks>
        /// A poll every ten seconds that silently cleared your selection would
        /// make choosing a large batch impossible.
        /// </remarks>
        private void RebuildRows()
        {
            var selected = new HashSet<Guid>(_rows.Where(r => r.IsSelected).Select(r => r.Entry.VideoId));
            var highlighted = (_mainWindow.QueueGrid.SelectedItem as QueueRow)?.Entry.VideoId;

            foreach (var row in _rows)
            {
                row.PropertyChanged -= OnRowChanged;
            }
            _rows.Clear();

            foreach (var row in QueueList.Build(_snapshot))
            {
                row.IsSelected = selected.Contains(row.Entry.VideoId);
                row.PropertyChanged += OnRowChanged;
                _rows.Add(row);
                _ = LoadThumbnail(row);
            }

            _mainWindow.QueueGrid.SelectedItem = _rows.FirstOrDefault(r => r.Entry.VideoId == highlighted);
            _mainWindow.QueueCountText.Text = QueueList.Summary(_rows);
            _mainWindow.EmptyText.Visibility = _rows.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
            UpdateActionBar();
        }

        private void OnRowChanged(object? sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName == nameof(QueueRow.IsSelected))
            {
                UpdateActionBar();
            }
        }

        /// <summary>Show only the actions that make sense for what is ticked.</summary>
        private void UpdateActionBar()
        {
            var selection = QueueSelection.For(_rows);
            _mainWindow.SelectionText.Text = selection.Summary;

            static Visibility Show(bool visible) => visible ? Visibility.Visible : Visibility.Collapsed;
            _mainWindow.MoveUpButton.Visibility = Show(selection.CanMove);
            _mainWindow.MoveDownButton.Visibility = Show(selection.CanMove);
            _mainWindow.RejectButton.Visibility = Show(selection.CanSkip);
            _mainWindow.StartBatchButton.Visibility = Show(selection.CanStart);
            _mainWindow.StartBatchButton.Content = selection.StartLabel;
            _mainWindow.PreviewButton.IsEnabled = _rows.Count > 0;
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

        public async void RefreshQueues() => await _session.RefreshQueuesAsync();

        /// <summary>
        /// Requeue every video not on the current analyzer version. The outcome
        /// is written to the Activity Log by the session.
        /// </summary>
        public async Task RequeueOutdatedAsync() => await _session.RequeueOutdatedAsync();

        public async Task ResetStaleJobsAsync() => await _session.ResetStaleJobsAsync();

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

        /// <summary>Open the highlighted video so it can be judged before starting it.</summary>
        public void PreviewSelected()
        {
            var row = _mainWindow.QueueGrid.SelectedItem as QueueRow
                      ?? _rows.FirstOrDefault(r => r.IsSelected)
                      ?? _rows.FirstOrDefault();

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
        /// Move the ticked rows one place within their group, then send the
        /// whole order to the backend -- priority lives server-side so every
        /// host agrees on it.
        /// </summary>
        private async Task Move(int direction)
        {
            var selection = QueueSelection.For(_rows);
            if (selection.MoveGroup is not QueueGroup group)
            {
                AddLog("Tick queued videos, or videos needing review, to reorder them.", Logger.LogLevel.Warning);
                return;
            }

            var isReview = group == QueueGroup.Review;
            var entries = isReview ? _snapshot.AwaitingReview : _snapshot.Queued;
            var ticked = new HashSet<Guid>(_rows.Where(r => r.IsSelected).Select(r => r.Entry.VideoId));
            var order = QueueOrdering.Move(entries.Select(e => e.VideoId).ToList(), ticked, direction);

            // Show the new order now rather than after the round trip.
            var byId = entries.ToDictionary(e => e.VideoId);
            var reordered = order.Select(id => byId[id]).ToList();
            if (isReview) _snapshot.AwaitingReview = reordered; else _snapshot.Queued = reordered;
            RebuildRows();

            // Shared with the Avalonia host so the two cannot disagree: send one
            // order spanning both groups, or reordering one renumbers it into the
            // other's priority band.
            await _session.ReorderAsync(QueueOrdering.GlobalOrder(
                _snapshot.Queued, _snapshot.AwaitingReview, order, isReview));
        }

        /// <summary>
        /// Queue the ticked videos for analysis. Retrying a failed video is the
        /// same request: approving puts it back in the queue.
        /// </summary>
        public async void StartBatch()
        {
            var ids = QueueSelection.For(_rows).DecisionIds;
            if (ids.Count == 0)
            {
                AddLog("Tick videos needing review, or failed ones, to start them.", Logger.LogLevel.Warning);
                return;
            }

            _mainWindow.StartBatchButton.IsEnabled = false;
            try
            {
                await _session.StartBatchAsync(ids);
            }
            finally
            {
                _mainWindow.StartBatchButton.IsEnabled = true;
            }
        }

        public async void RejectSelected()
        {
            var ids = QueueSelection.For(_rows).DecisionIds;
            if (ids.Count == 0)
            {
                AddLog("Tick videos needing review, or failed ones, to skip them.", Logger.LogLevel.Warning);
                return;
            }

            await _session.RejectAsync(ids);
        }

        // ---- status ----

        private void OnStateChanged(WorkerSessionState state)
            => _mainWindow.Dispatcher.Invoke(() => Render(state));

        private void Render(WorkerSessionState state)
        {
            // The pill: green while heartbeats land, red when they are refused
            // or something is wrong, grey when disconnected.
            var (pill, ink, dot) = state.HeartbeatOk switch
            {
                true => ("#DCFCE7", "#14532D", "#15803D"),
                false => ("#FEE2E2", "#7F1D1D", "#B91C1C"),
                _ when !state.IsConfigured || state.Status == "Error" => ("#FEE2E2", "#7F1D1D", "#B91C1C"),
                _ => ("#EEF0F3", "#57606A", "#8C959F"),
            };
            _mainWindow.ConnectionPill.Background = Brush(pill);
            _mainWindow.ConnectionPill.ToolTip = state.ConnectionTooltip;
            _mainWindow.ConnectionDot.Fill = Brush(dot);
            _mainWindow.StatusText.Foreground = Brush(ink);
            _mainWindow.LastHeartbeatText.Foreground = Brush(ink);
            _mainWindow.StatusText.Text = state.Status;
            _mainWindow.LastHeartbeatText.Text = state.LastHeartbeat.HasValue
                ? $"· heartbeat {state.LastHeartbeatDisplay}"
                : "";

            _mainWindow.BackendUrlDisplay.Text = state.BackendUrl;
            _mainWindow.CurrentJobText.Text = state.CurrentJob;
            _mainWindow.StageText.Text = state.Stage;
            _mainWindow.ProgressBar.Value = state.Progress;
            _mainWindow.ProgressText.Text = state.ProgressDisplay;

            // Show the progress card only while a job is actually running. A bar
            // sitting at 0% reads as stuck; nothing there reads as nothing running.
            var isProcessing = state.Status == "Processing";
            _mainWindow.ProcessingPanel.Visibility = isProcessing ? Visibility.Visible : Visibility.Collapsed;
            _mainWindow.IdlePanel.Visibility = isProcessing ? Visibility.Collapsed : Visibility.Visible;
            _mainWindow.IdleText.Text = state.IsConfigured
                ? state.CanStop ? "Connected, waiting for a job." : "Not connected. Nothing runs until you connect."
                : "Worker is not configured.";

            // One switch: Connect while disconnected, Disconnect while connected.
            _mainWindow.StartButton.IsEnabled = state.CanStart;
            _mainWindow.StopButton.IsEnabled = state.CanStop;
            _mainWindow.StartButton.Visibility = state.CanStop ? Visibility.Collapsed : Visibility.Visible;
            _mainWindow.StopButton.Visibility = state.CanStop ? Visibility.Visible : Visibility.Collapsed;
            _mainWindow.CancelJobButton.IsEnabled = state.CanCancelJob;
        }

        private static SolidColorBrush Brush(string hex)
        {
            var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
            brush.Freeze();
            return brush;
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
                            Logger.LogLevel.Error => Brushes.Firebrick,
                            Logger.LogLevel.Warning => Brushes.DarkOrange,
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
