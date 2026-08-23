package dev.neuromesh

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.Surface
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.neuromesh.provider.ProviderService
import dev.neuromesh.ui.AppViewModel
import dev.neuromesh.ui.ConsumerScreen
import dev.neuromesh.ui.JobScreen
import dev.neuromesh.ui.ModeScreen
import dev.neuromesh.ui.NeuroMeshTheme
import dev.neuromesh.ui.ProviderScreen
import dev.neuromesh.ui.SetupScreen

/**
 * The single activity. One APK, two modes, five screens.
 *
 * No navigation library: the app is a five-state machine and the state lives in
 * the view model, which is the thing a navigation graph would have wrapped.
 */
class MainActivity : ComponentActivity() {

    private val notifications =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        askForNotifications()

        setContent {
            NeuroMeshTheme {
                val vm: AppViewModel = viewModel()
                val ui by vm.ui.collectAsState()

                Surface(
                    Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.systemBars)
                ) {
                    when (ui.screen) {
                        AppViewModel.Screen.SETUP -> SetupScreen(vm)
                        AppViewModel.Screen.MODE -> ModeScreen(vm)
                        AppViewModel.Screen.PROVIDER -> ProviderScreen(
                            vm,
                            onStart = { ProviderService.start(this) },
                            onStop = { ProviderService.stop(this) },
                        )
                        AppViewModel.Screen.CONSUMER -> ConsumerScreen(vm)
                        AppViewModel.Screen.JOB -> JobScreen(vm)
                    }
                }
            }
        }
    }

    /**
     * The provider notification is the owner's off switch, so it is worth
     * asking for. Denied is survivable -- the service still runs, the owner
     * just has to come back to the app to stop it.
     */
    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}
