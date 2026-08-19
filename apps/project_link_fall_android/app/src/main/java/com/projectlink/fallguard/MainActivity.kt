package com.projectlink.fallguard

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.projectlink.fallguard.ui.theme.FallGuardTheme

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()
    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) viewModel.toggleGuardian()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            FallGuardTheme {
                val state by viewModel.uiState.collectAsStateWithLifecycle()
                MainScreen(
                    state = state,
                    onModeChanged = viewModel::setMode,
                    onGuardianToggle = { toggleGuardianWithPermission(state) },
                    onDemoRequested = viewModel::requestDemo,
                    onDemoConfirmed = viewModel::confirmDemo,
                    onDemoDismissed = viewModel::dismissDemo,
                    onDemoCountdownCancelled = viewModel::cancelDemoCountdown,
                    onIncidentCancelled = viewModel::cancelIncident,
                    onIncidentDismissed = viewModel::clearIncident,
                    onSettingsRequested = viewModel::showSettings,
                    onSettingsDismissed = viewModel::hideSettings,
                    onSettingsSaved = viewModel::saveSettings,
                    onConnectionTested = viewModel::testConnection,
                    onInformationDismissed = viewModel::dismissInformation,
                )
            }
        }
    }

    override fun onStart() {
        super.onStart()
        viewModel.startConnectionMonitoring()
    }

    override fun onStop() {
        viewModel.stopConnectionMonitoring()
        super.onStop()
    }

    private fun toggleGuardianWithPermission(state: MainUiState) {
        val permissionRequired = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        if (!state.guarding && permissionRequired) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            viewModel.toggleGuardian()
        }
    }
}
