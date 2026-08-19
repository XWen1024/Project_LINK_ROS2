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
                    onInformationDismissed = {},
                )
            }
        }

        composeRule.onNodeWithText("安全演示").assertIsDisplayed()
        composeRule.onNodeWithText("模拟跌倒").assertIsDisplayed().performClick()
    }

    @Test
    fun diagnosticDialogShowsConnectionFailureDetails() {
        composeRule.setContent {
            FallGuardTheme {
                MainScreen(
                    state = MainUiState(
                        informationDialog = InformationDialogState(
                            title = "连接测试失败",
                            message = "连接 Orin 超时",
                            details = "当前网络：蜂窝数据\n目标：http://10.255.176.119:8765/health",
                            isError = true,
                        ),
                    ),
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
                    onInformationDismissed = {},
                )
            }
        }

        composeRule.onNodeWithText("连接测试失败").assertIsDisplayed()
        composeRule.onNodeWithText("连接 Orin 超时").assertIsDisplayed()
        composeRule.onNodeWithText("知道了").assertIsDisplayed()
    }
}
