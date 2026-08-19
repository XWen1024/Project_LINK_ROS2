package com.projectlink.fallguard

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.projectlink.fallguard.ui.theme.FallGuardTheme
import org.junit.Rule
import org.junit.Test

class MainScreenTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun demoModeShowsSafeTrigger() {
        composeRule.setContent {
            FallGuardTheme {
                MainScreen(
                    state = MainUiState(mode = AppMode.DEMO, orinConnected = true),
                    onModeChanged = {},
                    onGuardianToggle = {},
                    onDemoRequested = {},
                    onDemoConfirmed = {},
                    onDemoDismissed = {},
                    onDemoCountdownCancelled = {},
                    onIncidentCancelled = {},
                    onIncidentDismissed = {},
                    onSettingsRequested = {},
                    onSettingsDismissed = {},
                    onSettingsSaved = {},
                    onConnectionTested = {},
                )
            }
        }

        composeRule.onNodeWithText("安全演示").assertIsDisplayed()
        composeRule.onNodeWithText("模拟跌倒").assertIsDisplayed().performClick()
    }
}

