using System.Windows;
using CaughtOnDash.Worker.ViewModels;

namespace CaughtOnDash.Worker
{
    /// <summary>
    /// Interaction logic for MainWindow.xaml
    /// </summary>
    public partial class MainWindow : Window
    {
        private MainViewModel? _viewModel;

        public MainWindow()
        {
            InitializeComponent();
            _viewModel = new MainViewModel(this);
            this.DataContext = _viewModel;

            _ = _viewModel.StartAutomaticallyAsync();
        }

        private async void StartButton_Click(object sender, RoutedEventArgs e)
        {
            if (_viewModel != null)
            {
                await _viewModel.StartWorker();
            }
        }

        private void StopButton_Click(object sender, RoutedEventArgs e)
        {
            _viewModel?.StopWorker();
        }

        private void CancelJobButton_Click(object sender, RoutedEventArgs e)
        {
            _viewModel?.CancelCurrentJob();
        }
    }
}
