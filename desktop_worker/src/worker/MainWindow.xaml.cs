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
        }

        private void ConnectButton_Click(object sender, RoutedEventArgs e)
        {
            var backendUrl = BackendUrlTextBox.Text;
            var apiToken = ApiTokenPasswordBox.Password;

            if (string.IsNullOrWhiteSpace(backendUrl) || string.IsNullOrWhiteSpace(apiToken))
            {
                ConnectionStatusText.Text = "Please enter both backend URL and API token";
                ConnectionStatusText.Foreground = System.Windows.Media.Brushes.Red;
                return;
            }

            _viewModel?.ConnectToBackend(backendUrl, apiToken);
        }

        private void StartButton_Click(object sender, RoutedEventArgs e)
        {
            _viewModel?.StartWorker();
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
