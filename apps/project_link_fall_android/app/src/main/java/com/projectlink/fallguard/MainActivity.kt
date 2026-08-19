package com.projectlink.fallguard

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.projectlink.fallguard.ui.theme.FallGuardTheme

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            FallGuardTheme {
                val state by viewModel.uiState.collectAsStateWithLifecycle()
                MainScreen(
                    state = state,
                    onModeChanged = viewModel::setMode,
                    onGuardianToggle = viewModel::toggleGuardian,
                    onDemoRequested = viewModel::requestDemo,
                    onDemoConfirmed = viewModel::confirmDemo,
                    onDemoDismissed = viewModel::dismissDemo,
                    onDemoCountdownCancelled = viewModel::cancelDemoCountdown,
                    onIncidentCancelled = viewModel::cancelIncident,
                    onSettingsRequested = viewModel::showSettings,
                    onSettingsDismissed = viewModel::hideSettings,
                    onSettingsSaved = viewModel::saveSettings,
                    onConnectionTested = viewModel::testConnection,
                )
            }
        }
    }
}

