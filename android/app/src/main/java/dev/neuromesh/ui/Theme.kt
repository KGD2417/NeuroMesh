package dev.neuromesh.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Dark by default: the provider phone spends the night face-down on a desk. */
private val Mint = Color(0xFF5EE7C7)
private val Indigo = Color(0xFF7AA2FF)
private val Ink = Color(0xFF0B1020)
private val Slate = Color(0xFF161C33)

private val Dark = darkColorScheme(
    primary = Mint,
    onPrimary = Ink,
    secondary = Indigo,
    background = Ink,
    surface = Slate,
    surfaceVariant = Color(0xFF1E2542),
    error = Color(0xFFFF6B6B),
)

private val Light = lightColorScheme(
    primary = Color(0xFF00786A),
    secondary = Color(0xFF2E4FA8),
)

@Composable
fun NeuroMeshTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (dark) Dark else Light, content = content)
}
