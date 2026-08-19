package com.projectlink.fallguard.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val LightColors = lightColorScheme(
    primary = Color(0xFF006B5C),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFF78F8DD),
    onPrimaryContainer = Color(0xFF00201A),
    secondary = Color(0xFF4B635D),
    secondaryContainer = Color(0xFFCDE8E0),
    surface = Color(0xFFF7FBF8),
    surfaceVariant = Color(0xFFDAE5E0),
    error = Color(0xFFBA1A1A),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF58DBC2),
    onPrimary = Color(0xFF00382F),
    primaryContainer = Color(0xFF005143),
    onPrimaryContainer = Color(0xFF78F8DD),
    secondary = Color(0xFFB1CCC4),
    secondaryContainer = Color(0xFF344B46),
    surface = Color(0xFF0F1513),
    surfaceVariant = Color(0xFF3F4945),
    error = Color(0xFFFFB4AB),
)

@Composable
fun FallGuardTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val view = LocalView.current
    if (!view.isInEditMode) {
        val window = (view.context as Activity).window
        WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        WindowCompat.getInsetsController(window, view).isAppearanceLightNavigationBars = !darkTheme
    }

    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        content = content,
    )
}

